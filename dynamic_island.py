import cairo
import math
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import dbus
from dbus.mainloop.glib import DBusGMainLoop

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo

# ---------------------------------------------------------------- geometry --
COMPACT_W, COMPACT_H = 126.0, 37.0
EXPAND_W, EXPAND_H   = 392.0, 158.0
TOP_MARGIN           = 10
WIN_H                = 200          # window height (card + room for shadow)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH   = "/org/mpris/MediaPlayer2"


# ------------------------------------------------------------ spring physics --
class Spring:
    """Mass-spring-damper: critically/near-critically damped => organic weight."""
    def __init__(self, stiffness=170.0, damping=17.0, mass=1.0):
        self.k = stiffness
        self.c = damping
        self.m = mass
        self.value = 0.0
        self.target = 0.0
        self.velocity = 0.0

    def set_target(self, t):
        self.target = t

    def step(self, dt):
        a = -(self.k / self.m) * (self.value - self.target) - (self.c / self.m) * self.velocity
        self.velocity += a * dt
        self.value += self.velocity * dt
        return self.value

    @property
    def settled(self):
        return abs(self.velocity) < 0.5 and abs(self.target - self.value) < 0.01


# ------------------------------------------------------------------ drawing --
def rounded_rect(cr, x, y, w, h, r):
    """Continuous-curvature-ish rounded rectangle (squircle feel)."""
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# --------------------------------------------------------------- MPRIS glue --
class MediaWatcher:
    """Tracks the active MPRIS player and pushes metadata to a callback."""
    def __init__(self, on_update):
        self.on_update = on_update
        self.bus = dbus.SessionBus()
        self.player = None

        self.bus.add_signal_receiver(
            self._on_props,
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            path=MPRIS_PATH,
            sender_keyword="sender",
        )
        self.bus.add_signal_receiver(
            self._on_name,
            dbus_interface="org.freedesktop.DBus",
            signal_name="NameOwnerChanged",
        )

    # -- players --------------------------------------------------------------
    def _players(self):
        try:
            return [n for n in self.bus.list_names() if n.startswith(MPRIS_PREFIX)]
        except Exception:
            return []

    def _status(self, name):
        try:
            obj = self.bus.get_object(name, MPRIS_PATH)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
        except Exception:
            return ""

    def refresh(self, prefer=None):
        players = self._players()
        if not players:
            if self.player:
                self.player = None
                self.on_update(None)
            return
        if prefer and prefer in players:
            self.player = prefer
        elif self.player not in players:
            self.player = next((p for p in players if self._status(p) == "Playing"), players[0])
        try:
            obj = self.bus.get_object(self.player, MPRIS_PATH)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            md = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
            meta = {
                "title":  str(md.get("xesam:title", "")),
                "artist": " / ".join(str(a) for a in (md.get("xesam:artist") or [])),
                "art":    str(md.get("mpris:artUrl", "")),
                "length": int(md.get("mpris:length", 0)) / 1e6,
                "status": str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")),
                "pos":    int(props.Get("org.mpris.MediaPlayer2.Player", "Position")) / 1e6,
            }
            self.on_update(meta)
        except Exception:
            self.on_update(None)

    def get_position(self):
        if not self.player:
            return 0
        try:
            obj = self.bus.get_object(self.player, MPRIS_PATH)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return int(props.Get("org.mpris.MediaPlayer2.Player", "Position")) / 1e6
        except Exception:
            return 0

    def call(self, method):
        if not self.player:
            return
        try:
            obj = self.bus.get_object(self.player, MPRIS_PATH)
            iface = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
            getattr(iface, method)()
        except Exception:
            pass

    # -- signals (arrive on the GTK main thread thanks to DBusGMainLoop) -------
    def _on_props(self, iface, changed, invalidated, sender=None):
        if iface == "org.mpris.MediaPlayer2.Player" and changed:
            self.refresh(prefer=sender)

    def _on_name(self, name, old, new):
        if name.startswith(MPRIS_PREFIX):
            if new and not old:      # player started
                self.refresh(prefer=name)
            elif not new and old:    # player quit
                self.refresh()


# ------------------------------------------------------------------- window --
class IslandWindow(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Dynamic Island")
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.connect("destroy", Gtk.main_quit)

        screen = self.get_screen()
        if screen.get_rgba_visual():          # transparent window needs compositor
            self.set_visual(screen.get_rgba_visual())

        self.da = Gtk.DrawingArea()
        self.da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.da.connect("draw", self.on_draw)
        self.da.connect("button-press-event", self.on_click)
        self.add(self.da)

        # full-width strip at the top of the primary monitor
        geo = screen.get_monitor_geometry(0)
        self.win_w = geo.width
        self.set_default_size(self.win_w, WIN_H)
        self.move(geo.x, geo.y)
        self.stick()                            # all workspaces
        self.show_all()

        # animation state
        self.p_spring = Spring(170, 17)         # expansion 0 -> 1
        self.c_spring = Spring(300, 26)         # content crossfade
        self.v_spring = Spring(140, 16)         # global visibility
        self.expanded = False
        self.meta = None
        self.art_pixbuf = None
        self.art_key = ""
        self.last_frame = 0.0
        self._last_shape = 0.0

        self.watcher = MediaWatcher(self.on_media)
        GLib.idle_add(self.watcher.refresh)     # non-blocking first fetch

        self.add_tick_callback(self.tick)       # vsync-aligned animation
        GLib.timeout_add(1000, self.poll_pos)

    # -- animation loop -------------------------------------------------------
    def tick(self, widget, frame_clock):
        now = frame_clock.get_frame_time() / 1e6
        dt = min(now - self.last_frame, 0.05)
        self.last_frame = now

        self.v_spring.set_target(1.0 if self.visible_target else 0.0)
        self.p_spring.step(dt)
        self.c_spring.step(dt)
        self.v_spring.step(dt)

        if self.v_spring.value > 0.02 or not self.v_spring.settled:
            self.da.queue_draw()

        # input shape is an X round-trip: throttle it, don't spam it
        if now - self._last_shape > 0.12:
            self.update_input_shape()
            self._last_shape = now
        return True

    def poll_pos(self):
        if self.meta and self.watcher.player and self.c_spring.value > 0.5:
            self.meta["pos"] = self.watcher.get_position()
            self.da.queue_draw()
        return True

    # -- input shape (click-through outside the pill/card) --------------------
    def update_input_shape(self):
        if not self.get_realized():
            return
        w, h = self.get_window().get_width(), self.get_window().get_height()
        srf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(srf)
        self.draw_card(cr, opaque=True)
        try:
            region = Gdk.cairo_region_create_from_surface(srf)
            self.get_window().input_shape_combine_region(region, 0, 0)
        except Exception:
            pass

    # -- events ---------------------------------------------------------------
    def on_click(self, widget, ev):
        x, y = ev.x, ev.y
        if self.c_spring.value > 0.5:            # expanded -> hit-test buttons
            p = self.p_spring.value
            w = COMPACT_W + (EXPAND_W - COMPACT_W) * p
            h = COMPACT_H + (EXPAND_H - COMPACT_H) * p
            cx = (self.win_w - w) / 2 + w / 2
            cy = TOP_MARGIN + h - 30
            spacing = 44
            x0 = cx - spacing * 1.5
            buttons = {x0: "Previous", x0 + spacing: "PlayPause",
                       x0 + 2 * spacing: "Next", x0 + 3 * spacing: None}
            for bx, method in buttons.items():
                if abs(x - bx) <= 20 and abs(y - cy) <= 20:
                    if method:
                        self.watcher.call(method)
                    return True                 # swallow clicks on the output icon
        self.toggle()
        return True

    def toggle(self):
        self.expanded = not self.expanded
        self.p_spring.set_target(1.0 if self.expanded else 0.0)
        self.c_spring.set_target(1.0 if self.expanded else 0.0)

    # -- media callback --------------------------------------------------------
    def on_media(self, meta):
        if meta is None:
            self.meta = None
            self.art_pixbuf = None
            self.visible_target = False
            if self.expanded:
                self.expanded = False
                self.p_spring.set_target(0.0)
                self.c_spring.set_target(0.0)
            return
        self.meta = meta
        self.visible_target = True
        self.load_art(meta.get("art", ""))

    # -- album art --------------------------------------------------------------
    def load_art(self, url):
        if url == self.art_key and self.art_pixbuf is not None:
            return
        self.art_key = url
        self.art_pixbuf = None
        if not url:
            self.da.queue_draw()
            return
        if url.startswith("file://"):
            try:
                path = urllib.parse.unquote(url[7:])
                self.art_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 400, 400)
                self.da.queue_draw()
            except Exception:
                pass
            return
        threading.Thread(target=self._fetch_art, args=(url,), daemon=True).start()

    def _fetch_art(self, url):
        pb, tmp = None, None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".png"
            fd, tmp = tempfile.mkstemp(suffix=ext)
            os.write(fd, data)
            os.close(fd)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(tmp, 400, 400)
        except Exception:
            pb = None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

        def apply():
            if pb is not None and self.art_key == url:
                self.art_pixbuf = pb
            self.da.queue_draw()
            return False
        GLib.idle_add(apply)

    # -- painting ----------------------------------------------------------------
    def on_draw(self, widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)           # fully transparent backdrop
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.draw_card(cr)
        return False

    def draw_card(self, cr, opaque=False):
        g = 1.0 if opaque else self.v_spring.value
        if g < 0.02:
            return
        p = self.p_spring.value
        w = COMPACT_W + (EXPAND_W - COMPACT_W) * p
        h = COMPACT_H + (EXPAND_H - COMPACT_H) * p
        r = COMPACT_H / 2 + (42 - COMPACT_H / 2) * p   # capsule -> squircle
        x = (self.win_w - w) / 2.0
        y = TOP_MARGIN

        if not opaque:
            for i in range(6):                   # layered soft shadow
                cr.set_source_rgba(0, 0, 0, 0.05 * (6 - i) / 6 * g)
                cr.set_line_width(4 + i * 3)
                rounded_rect(cr, x, y, w, h, r)
                cr.stroke()

        rounded_rect(cr, x, y, w, h, r)          # body
        cr.set_source_rgba(0.03, 0.03, 0.05, (0.96 * g) if not opaque else 1.0)
        cr.fill()

        rounded_rect(cr, x + 0.5, y + 0.5, w - 1, h - 1, max(1, r - 0.5))
        cr.set_source_rgba(1, 1, 1, 0.10 * g)    # glass edge highlight
        cr.set_line_width(1)
        cr.stroke()

        cr.save()                                # clip content to the card
        rounded_rect(cr, x, y, w, h, r)
        cr.clip()
        self.draw_content(cr, x, y, w, h, g)
        cr.restore()

    def draw_content(self, cr, x, y, w, h, g):
        c = self.c_spring.value
        meta = self.meta or {}

        # ---- compact state ----
        ca = (1 - c) * g
        if ca > 0.02:
            size = 25
            self.draw_art(cr, x + 7, y + (h - size) / 2, size, size, 6, ca)
            self.draw_eq(cr, x + w - 7 - 31, y + (h - 14) / 2, ca,
                         meta.get("status") == "Playing")

        # ---- expanded state ----
        ea = c * g
        if ea <= 0.02:
            return
        self.draw_art(cr, x + 18, y + 14, 80, 80, 14, ea)

        tx, tw = x + 112, w - 177
        self.draw_text(cr, meta.get("title", "No media"), tx, y + 24, tw, 14,
                       Pango.Weight.BOLD, ea)
        self.draw_text(cr, meta.get("artist", "Not playing"), tx, y + 46, tw, 12,
                       Pango.Weight.NORMAL, 0.65 * ea)

        # purple animated waveform, top-right
        self.draw_eq(cr, x + w - 55, y + 26, ea, meta.get("status") == "Playing",
                     color=(0.76, 0.62, 1.0), bar_w=3, gap=6, max_h=18)

        if meta.get("length"):
            self.draw_scrubber(cr, x + 18, y + h - 56, w - 36, ea, meta)

        self.draw_controls(cr, x + w / 2, y + h - 30, ea, meta)

    def draw_art(self, cr, x, y, w, h, rad, alpha):
        cr.save()
        rounded_rect(cr, x, y, w, h, rad)
        cr.clip()
        pb = self.art_pixbuf
        if pb is not None:
            pw, ph = pb.get_width(), pb.get_height()
            scale = max(w / pw, h / ph)          # cover-crop
            sw, sh = pw * scale, ph * scale
            cr.set_source_surface(
                Gdk.cairo_surface_create_from_pixbuf(pb, 0, None),
                x + (w - sw) / 2, y + (h - sh) / 2,
            )
            cr.paint_with_alpha(alpha)
        else:
            cr.set_source_rgba(0.13, 0.13, 0.16, alpha)
            cr.paint()
            try:
                icon = Gtk.IconTheme.get_default().load_icon("audio-x-generic", int(w * 0.55), 0)
                isrf = Gdk.cairo_surface_create_from_pixbuf(icon, 0, None)
                cr.set_source_surface(isrf, x + (w - icon.get_width()) / 2, y + (h - icon.get_height()) / 2)
                cr.paint_with_alpha(0.8 * alpha)
            except Exception:
                pass
        cr.restore()

    def draw_eq(self, cr, x0, y0, alpha, playing, color=(1.0, 1.0, 1.0),
                bar_w=3, gap=7, max_h=14):
        t = time.monotonic()
        for i in range(5):
            hgt = max_h * 0.28
            if playing:
                hgt = max_h * (0.3 + 0.7 * abs(math.sin(t * 2.4 + i * 1.2)))
            cr.set_source_rgba(color[0], color[1], color[2], alpha)
            cr.rectangle(x0 + i * gap, y0 + (max_h - hgt) / 2, bar_w, hgt)
            cr.fill()

    def draw_text(self, cr, text, x, y, maxw, size, weight, alpha,
                  align=Pango.Alignment.LEFT):
        if not text:
            return
        layout = self.da.create_pango_layout()
        layout.set_text(text, -1)
        fd = Pango.FontDescription()
        fd.set_family("Sans")
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(weight)
        layout.set_font_description(fd)
        layout.set_width(int(maxw * Pango.SCALE))
        layout.set_alignment(align)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    @staticmethod
    def fmt(secs):
        secs = max(int(secs), 0)
        return f"{secs // 60}:{secs % 60:02d}"

    def draw_scrubber(self, cr, x, y, w, alpha, meta):
        length = max(float(meta.get("length", 0)), 1)
        pos = min(max(float(meta.get("pos", 0)), 0), length)
        frac = pos / length
        ty = y + 8
        rounded_rect(cr, x, ty, w, 4, 2)                # dark gray track
        cr.set_source_rgba(0.25, 0.25, 0.27, alpha)
        cr.fill()
        fw = w * frac                                   # solid white fill
        if fw > 3:
            rounded_rect(cr, x, ty, fw, 4, 2)
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.fill()
        cr.arc(x + fw, ty + 2, 6, 0, 2 * math.pi)       # thumb
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.fill()
        self.draw_text(cr, self.fmt(pos), x, y - 8, 64, 11, Pango.Weight.NORMAL, 0.6 * alpha)
        self.draw_text(cr, "-" + self.fmt(length - pos), x + w - 64, y - 8, 64, 11,
                       Pango.Weight.NORMAL, 0.6 * alpha, Pango.Alignment.RIGHT)

    def draw_controls(self, cr, cx, cy, alpha, meta):
        spacing = 44
        x0 = cx - spacing * 1.5
        self.draw_skip(cr, x0, cy, False, alpha)
        self.draw_playpause(cr, x0 + spacing, cy, alpha, meta.get("status") == "Playing")
        self.draw_skip(cr, x0 + 2 * spacing, cy, True, alpha)
        self.draw_airplay(cr, x0 + 3 * spacing, cy, 15, alpha)

    def draw_playpause(self, cr, cx, cy, alpha, playing):
        cr.set_source_rgba(1, 1, 1, alpha)
        if playing:                              # pause: two bars
            wdt = 4
            cr.rectangle(cx - wdt - 2, cy - 8, wdt, 16)
            cr.rectangle(cx + 2, cy - 8, wdt, 16)
            cr.fill()
        else:                                    # play: triangle
            cr.move_to(cx - 4, cy - 9)
            cr.line_to(cx + 9, cy)
            cr.line_to(cx - 4, cy + 9)
            cr.close_path()
            cr.fill()

    def draw_skip(self, cr, cx, cy, fwd, alpha):
        s = 8.0
        sign = 1.0 if fwd else -1.0
        cr.set_source_rgba(1, 1, 1, alpha)
        for dx in (-4.0, 4.0):
            cr.move_to(cx + sign * (dx + s * 0.8), cy)
            cr.line_to(cx + sign * (dx - s * 0.8), cy - s)
            cr.line_to(cx + sign * (dx - s * 0.8), cy + s)
            cr.close_path()
            cr.fill()

    def draw_airplay(self, cr, cx, cy, s, alpha):
        cr.set_source_rgba(1, 1, 1, alpha)
        h = s * 0.56                                   # rounded square outline
        rounded_rect(cr, cx - h, cy - h, 2 * h, 2 * h, s * 0.30)
        cr.set_line_width(1.6)
        cr.stroke()
        tx = cx - s * 0.10                             # triangle pointing up
        cr.move_to(tx, cy - s * 0.24)
        cr.line_to(tx - s * 0.18, cy + s * 0.24)
        cr.line_to(tx + s * 0.18, cy + s * 0.24)
        cr.close_path()
        cr.fill()
        for rr in (s * 0.14, s * 0.26):                # concentric arcs
            cr.arc(cx + s * 0.28, cy, rr, -math.pi / 2.2, math.pi / 2.2)
            cr.set_line_width(1.4)
            cr.stroke()


def main():
    DBusGMainLoop(set_as_default=True)           # signals land on GTK's thread
    win = IslandWindow()
    Gtk.main()


if __name__ == "__main__":
    main()
