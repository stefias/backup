import dbus
import faulthandler
import json
import math
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from PyQt5.QtCore import QRect, QRectF, QSize, Qt, QTimer, QPointF
from PyQt5.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QWidget, QGraphicsBlurEffect

faulthandler.enable()


class MarqueeLabel(QLabel):
    """Custom QLabel that smoothly scrolls text continuously if it exceeds the label width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.offset = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step_scroll)
        self.timer.start(16)  # ~60fps for smoother scrolling
        self.text_color = QColor(255, 255, 255, 255)
        self.spacing = 50  # Space between looping texts

    def set_text_color(self, color):
        self.text_color = color
        self.update()

    def setText(self, text):
        if self.text() != text:
            super().setText(text)
            self.offset = 0.0
            self.update()

    def step_scroll(self):
        fm = self.fontMetrics()
        text_width = fm.width(self.text())
        widget_width = self.width()

        if text_width > widget_width:
            self.offset += 0.8  # Smooth sub-pixel increment
            
            # Wrap seamlessly when the offset passes the first text + spacing
            if self.offset >= text_width + self.spacing:
                self.offset -= (text_width + self.spacing)
        else:
            self.offset = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(self.text_color)

        fm = self.fontMetrics()
        text_width = fm.width(self.text())
        widget_width = self.width()
        
        # Keep y as a float for QPointF
        y = (self.height() + fm.ascent() - fm.descent()) / 2.0

        if text_width > widget_width:
            painter.save()
            painter.setClipRect(self.rect())
            
            # Draw the main text using sub-pixel coordinates to eliminate jitter
            painter.drawText(QPointF(-self.offset, y), self.text())
            
            # Draw the looping text right behind it
            painter.drawText(QPointF(-self.offset + text_width + self.spacing, y), self.text())
            
            painter.restore()
        else:
            painter.drawText(QPointF(0, y), self.text())


class Spring:
    """Smooth non-bouncy interpolator replacing the spring bounce."""

    def __init__(self, stiffness=0.0, damping=0.0, mass=1.0):
        self.value = 0.0
        self.target = 0.0
        self.velocity = 0.0

    def set_target(self, t):
        self.target = t

    def step(self, dt):
        diff = self.target - self.value
        # Adjusted speed to 6.8 for a controlled minimization rate
        self.value += diff * min(1.0, 6.8 * dt)
        if abs(self.target - self.value) < 0.001:
            self.value = self.target
        return self.value

    @property
    def settled(self):
        return abs(self.target - self.value) < 0.0005


class AnimatedWaveform(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.heights = [10.0, 11.0, 12.0, 10.0, 11.0]
        self.is_playing = False
        self.color1 = QColor(220, 150, 240)
        self.color2 = QColor(140, 70, 160)

    def set_colors(self, c1, c2):
        self.color1 = c1
        self.color2 = c2
        self.update()

    def start_animation(self):
        self.is_playing = True
        self.timer.start(10)

    def stop_animation(self):
        self.is_playing = False
        self.timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setPen(Qt.NoPen)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, self.color1)
        gradient.setColorAt(1.0, self.color2)
        painter.setBrush(gradient)

        if self.is_playing:
            t = time.time() * 2.8
            self.heights = []
            for i in range(5):
                h = 10.0 + 2.8 * math.sin(t + i * 0.75) * math.cos(t * 0.5 - i * 0.4)
                self.heights.append(max(6.0, min(15.0, h)))
        else:
            self.heights = [10.0, 10.0, 10.0, 10.0, 10.0]

        bar_width = 3.0
        spacing = 3.0
        for i, h in enumerate(self.heights):
            x = i * (bar_width + spacing)
            y = (self.height() - h) / 2.0
            painter.drawRoundedRect(QRectF(x, y, bar_width, h), 1.0, 1.0)


class IconButton(QPushButton):

    def __init__(self, icon_type, parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.old_icon_type = icon_type
        self.anim_val = 1.0
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._step_anim)

        self.click_anim_val = 0.0
        self.click_timer = QTimer(self)
        self.click_timer.timeout.connect(self._step_click_anim)

        self.blur_effect = QGraphicsBlurEffect(self)
        self.blur_effect.setBlurRadius(0.0)
        self.setGraphicsEffect(self.blur_effect)

        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._on_clicked)

    def mousePressEvent(self, event):
        parent_widget = self.parent()
        if parent_widget and hasattr(parent_widget, "anim_timer") and parent_widget.anim_timer.isActive():
            event.ignore()
            return
        super().mousePressEvent(event)

    def _on_clicked(self):
        self.click_anim_val = 1.0
        if not self.click_timer.isActive():
            self.click_timer.start(10)
        self.update_blur()
        self.update()

    def _step_click_anim(self):
        self.click_anim_val -= 1.0 / 30.0
        if self.click_anim_val <= 0.0:
            self.click_anim_val = 0.0
            self.click_timer.stop()
        self.update_blur()
        self.update()

    def set_icon_type(self, new_type, animate=True):
        if new_type == self.icon_type:
            return
        if animate:
            self.old_icon_type = self.icon_type
            self.icon_type = new_type
            self.anim_val = 0.0
            if not self.anim_timer.isActive():
                self.anim_timer.start(10)
        else:
            self.icon_type = new_type
            self.old_icon_type = new_type
            self.anim_val = 1.0
        self.update_blur()
        self.update()

    def _step_anim(self):
        self.anim_val += 1.0 / 30.0
        if self.anim_val >= 1.0:
            self.anim_val = 1.0
            self.anim_timer.stop()
        self.update_blur()
        self.update()

    def update_blur(self):
        trans_blur = 0.0
        if self.anim_timer.isActive() and self.old_icon_type != self.icon_type:
            trans_blur = 1.8 * math.sin(self.anim_val * math.pi)

        click_blur = 2.2 * self.click_anim_val
        total_blur = max(trans_blur, click_blur)
        self.blur_effect.setBlurRadius(total_blur)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        parent_widget = self.parent()
        p_val = 1.0
        if parent_widget and hasattr(parent_widget, "p_spring"):
            p_stiff = max(0.0, min(1.0, parent_widget.p_spring.value))
            p_val = p_stiff ** 1.6  # Steeper opacity reduction on minimize

        base_color = (
            QColor(190, 190, 190)
            if self.isDown() or self.underMouse()
            else QColor(255, 255, 255)
        )

        w, h = self.width(), self.height()

        def draw_single_icon(i_type, opacity_factor, scale_factor):
            if opacity_factor <= 0.001:
                return
            color = QColor(
                base_color.red(),
                base_color.green(),
                base_color.blue(),
                int(255 * p_val * opacity_factor),
            )
            painter.save()
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)

            if scale_factor != 1.0:
                painter.translate(w / 2, h / 2)
                painter.scale(scale_factor, scale_factor)
                painter.translate(-w / 2, -h / 2)

            def draw_rounded_tri(x, y, width, height, right=True):
                path = QPainterPath()
                if right:
                    path.moveTo(x, y)
                    path.lineTo(x + width, y + height / 2)
                    path.lineTo(x, y + height)
                else:
                    path.moveTo(x + width, y)
                    path.lineTo(x, y + height / 2)
                    path.lineTo(x + width, y + height)
                path.closeSubpath()

                painter.setPen(
                    QPen(color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                )
                painter.drawPath(path)
                painter.setPen(Qt.NoPen)
                painter.drawPath(path)

            if i_type == "play":
                draw_rounded_tri(w * 0.25, h * 0.1, w * 0.58, h * 0.75, True)
            elif i_type == "pause":
                bw = w * 0.3
                bh = h * 0.75
                painter.drawRoundedRect(
                    int(w * 0.15), int((h - bh) / 2), int(bw), int(bh), 3, 3
                )
                painter.drawRoundedRect(
                    int(w * 0.55), int((h - bh) / 2), int(bw), int(bh), 3, 3
                )
            elif i_type == "next":
                tw = w * 0.42
                th = h * 0.6
                draw_rounded_tri(w * 0.05, (h - th) / 2, tw, th, True)
                draw_rounded_tri(w * 0.5, (h - th) / 2, tw, th, True)
            elif i_type == "prev":
                tw = w * 0.42
                th = h * 0.6
                draw_rounded_tri(w * 0.08, (h - th) / 2, tw, th, False)
                draw_rounded_tri(w * 0.53, (h - th) / 2, tw, th, False)
            painter.restore()

        if self.anim_val >= 1.0 or self.old_icon_type == self.icon_type:
            draw_single_icon(self.icon_type, 1.0, 1.0)
        else:
            out_opacity = 1.0 - self.anim_val
            out_scale = 1.0 - (0.1 * self.anim_val)
            in_opacity = self.anim_val
            in_scale = 0.9 + (0.1 * self.anim_val)

            draw_single_icon(self.old_icon_type, out_opacity, out_scale)
            draw_single_icon(self.icon_type, in_opacity, in_scale)


class SpringDynamicIsland(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.margin = 16
        self.base_collapsed_w, self.base_collapsed_h = 240, 42
        self.base_expanded_w, self.base_expanded_h = 400, 200

        self.collapsed_w = self.base_collapsed_w + (self.margin * 2)
        self.collapsed_h = self.base_collapsed_h + (self.margin * 2)
        self.expanded_w = self.base_expanded_w + (self.margin * 2)
        self.expanded_h = self.base_expanded_h + (self.margin * 2)

        self.y_pos = 12

        screen = QApplication.primaryScreen().geometry()
        self.screen_width = screen.width()
        self.x_pos = (self.screen_width - self.collapsed_w) // 2
        self.setGeometry(
            self.x_pos, self.y_pos, self.collapsed_w, self.collapsed_h
        )

        try:
            self.bus = dbus.SessionBus()
        except Exception:
            self.bus = None

        self.mpris_player = None
        self.is_expanded = False

        self.p_spring = Spring()

        self.last_frame_time = time.time()
        self._last_art_size = (0, 0)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_animation)
        self.anim_timer.stop()

        self.track_id = ""
        self.track_title = "Entropy"
        self.track_artist = "Beach Bunny"
        self.art_url = ""
        self.art_request_id = 0
        self.track_length = 0
        self.current_position = 0
        self.current_position_float = 0.0
        self.cached_pixmap = None
        self.is_playing = False

        self.setup_ui()

        self.smooth_timer = QTimer(self)
        self.smooth_timer.timeout.connect(self.smooth_tick)
        self.smooth_timer.start(100)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_media)
        self.poll_timer.start(1000)

        self.poll_media()
        self.update_widget_geometries(0.0)

    def setup_ui(self):
        self.art_label = QLabel(self)
        self.art_label.show()

        font_t = QFont("Segoe UI", 13, QFont.Bold)
        font_t.setStyleStrategy(QFont.PreferAntialias)
        self.title_label = MarqueeLabel(self)
        self.title_label.setFont(font_t)
        self.title_label.setText(self.track_title)
        self.title_label.show()

        font_a = QFont("Segoe UI", 12)
        font_a.setStyleStrategy(QFont.PreferAntialias)
        self.artist_label = QLabel(self.track_artist, self)
        self.artist_label.setFont(font_a)
        self.artist_label.show()

        self.wave_widget = AnimatedWaveform(self)
        self.wave_widget.show()

        font_time = QFont("Segoe UI", 9, QFont.Medium)
        font_time.setStyleStrategy(QFont.PreferAntialias)

        self.time_left = QLabel("0:00", self)
        self.time_left.setFont(font_time)
        self.time_left.show()

        self.time_right = QLabel("-0:00", self)
        self.time_right.setFont(font_time)
        self.time_right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_right.show()

        self.btn_prev = IconButton("prev", self)
        self.btn_play = IconButton("play", self)
        self.btn_next = IconButton("next", self)

        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.show()
            btn.clicked.connect(self.handle_media_button)

    def get_p_stiff(self, p):
        return max(0.0, min(1.0, p))

    def update_animation(self):
        now = time.time()
        dt = min(now - self.last_frame_time, 0.05)
        self.last_frame_time = now

        if self.p_spring.settled:
            self.p_spring.value = self.p_spring.target
            self.p_spring.velocity = 0.0
            p = self.p_spring.target
            self.anim_timer.stop()
        else:
            self.p_spring.step(dt)
            p = self.p_spring.value

        w = self.collapsed_w + (self.expanded_w - self.collapsed_w) * p
        h = self.collapsed_h + (self.expanded_h - self.collapsed_h) * p

        x = (self.screen_width - w) // 2
        y = self.y_pos

        self.setGeometry(int(x), int(y), int(w), int(h))
        self.update_widget_geometries(p)
        self.update()

    def update_widget_geometries(self, p):
        p_stiff = self.get_p_stiff(p)
        alpha_p = max(0.0, min(1.0, p_stiff))
        btn_alpha = max(0.0, min(1.0, p_stiff ** 1.6))

        ax = int((self.margin + 12) + ((self.margin + 20) - (self.margin + 12)) * p_stiff)
        ay = int((self.margin + 8) + ((self.margin + 20) - (self.margin + 8)) * p_stiff)
        asize = int(26 + (76 - 26) * p_stiff)
        arad = int(6 + (20 - 6) * p_stiff)
        self.art_label.setGeometry(ax, ay, asize, asize)

        if self._last_art_size != (asize, arad):
            self._last_art_size = (asize, arad)
            self.art_label.setPixmap(self.get_rounded_pixmap(asize, arad))

        collapsed_wx = self.collapsed_w - self.margin - 42
        expanded_wx = self.expanded_w - self.margin - 55
        wx = int(collapsed_wx + (expanded_wx - collapsed_wx) * p_stiff)
        wy = int((self.margin + 8) + ((self.margin + 35) - (self.margin + 8)) * p_stiff)
        ww = int(30 + (35 - 30) * p_stiff)
        wh = int(26 + (25 - 26) * p_stiff)
        self.wave_widget.setGeometry(wx, wy, ww, wh)

        tx = int((self.margin + 45) + ((self.margin + 115) - (self.margin + 45)) * p_stiff)
        ty = int((self.margin + 15) + ((self.margin + 32) - (self.margin + 15)) * p_stiff)
        tw = int(150 + 50 * p_stiff)
        th = 24
        self.title_label.setGeometry(tx, ty, tw, th)
        
        title_color = QColor(255, 255, 255, int(255 * alpha_p))
        self.title_label.set_text_color(title_color)

        arx = int((self.margin + 45) + ((self.margin + 115) - (self.margin + 45)) * p_stiff)
        ary = int((self.margin + 25) + ((self.margin + 58) - (self.margin + 25)) * p_stiff)
        arw = int(150 + 50 * p_stiff)
        arh = 24
        self.artist_label.setGeometry(arx, ary, arw, arh)
        self.artist_label.setStyleSheet(
            f"color: rgba(187, 187, 187, {alpha_p});"
        )

        current_w = self.width() - (self.margin * 2)
        bar_w = int(120 + (250 - 120) * p_stiff)
        bar_h = int(3 + (7 - 3) * p_stiff)
        bar_x = self.margin + (current_w - bar_w) // 2
        bar_y = int(
            (self.margin + 50) + ((self.margin + 127) - (self.margin + 50)) * p_stiff
        )

        tl_x = bar_x
        tl_y = bar_y + bar_h + 4
        self.time_left.setGeometry(tl_x, tl_y, 45, 18)
        self.time_left.setStyleSheet(
            f"color: rgba(224, 224, 224, {alpha_p});"
        )
        self.time_left.setVisible(alpha_p > 0.01)

        tr_w = 45
        tr_x = bar_x + bar_w - tr_w
        tr_y = bar_y + bar_h + 4
        self.time_right.setGeometry(tr_x, tr_y, tr_w, 18)
        self.time_right.setStyleSheet(
            f"color: rgba(224, 224, 224, {alpha_p});"
        )
        self.time_right.setVisible(alpha_p > 0.01)

        w = self.width()
        play_w, play_h = 40, 40
        prev_w, prev_h = 44, 28
        next_w, next_h = 44, 28
        spacing = 16

        play_x = (w - play_w) // 2
        prev_x = play_x - spacing - prev_w
        next_x = play_x + play_w + spacing

        prev_y = int(
            (self.margin + 20) + ((self.margin + 150) - (self.margin + 20)) * p_stiff
        )
        play_y = int(
            (self.margin + 20) + ((self.margin + 144) - (self.margin + 20)) * p_stiff
        )
        next_y = int(
            (self.margin + 20) + ((self.margin + 150) - (self.margin + 20)) * p_stiff
        )

        self.btn_prev.setGeometry(prev_x, prev_y, prev_w, prev_h)
        self.btn_play.setGeometry(play_x, play_y, play_w, play_h)
        self.btn_next.setGeometry(next_x, next_y, next_w, next_h)

        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.setVisible(btn_alpha > 0.02)
            btn.update()

    def smooth_tick(self):
        if self.is_playing and self.track_length > 0:
            self.current_position_float += 0.1
            if self.current_position_float > self.track_length:
                self.current_position_float = float(self.track_length)
            self.current_position = int(self.current_position_float)
            if self.is_expanded:
                self.update_progress_ui()

    def poll_media(self):
        if not self.bus:
            return

        try:
            services = [
                s
                for s in self.bus.list_names()
                if s.startswith("org.mpris.MediaPlayer2.")
            ]
            if not services:
                return

            player_proxy = self.bus.get_object(
                services[0], "/org/mpris/MediaPlayer2"
            )
            props = dbus.Interface(player_proxy, "org.freedesktop.DBus.Properties")
            self.mpris_player = dbus.Interface(
                player_proxy, "org.mpris.MediaPlayer2.Player"
            )

            metadata = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")

            new_track_id = str(metadata.get("mpris:trackid", ""))
            new_title = str(metadata.get("xesam:title", "Unknown"))
            artists = metadata.get("xesam:artist", ["Unknown"])
            new_artist = str(artists[0]) if artists else "Unknown"
            new_art = str(metadata.get("mpris:artUrl", ""))

            if not new_art and new_title == self.track_title and self.art_url:
                new_art = self.art_url

            self.track_length = int(metadata.get("mpris:length", 0)) // 1000000

            try:
                pos_sec = (
                    int(props.Get("org.mpris.MediaPlayer2.Player", "Position"))
                    // 1000000
                )
                if abs(pos_sec - self.current_position_float) > 1:
                    self.current_position_float = float(pos_sec)
                    self.current_position = pos_sec
            except Exception:
                pass

            status = str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
            playing = status == "Playing"

            if playing != self.is_playing:
                self.is_playing = playing
                if playing:
                    self.btn_play.set_icon_type("pause", animate=True)
                    self.wave_widget.start_animation()
                else:
                    self.btn_play.set_icon_type("play", animate=True)
                    self.wave_widget.stop_animation()

            is_track_changed = (
                new_track_id != self.track_id
                or new_title != self.track_title
                or new_artist != self.track_artist
                or new_art != self.art_url
            )

            if is_track_changed:
                self.track_id = new_track_id
                self.track_title = new_title
                self.track_artist = new_artist
                self.art_url = new_art
                self.art_request_id += 1
                self.load_album_art(self.art_request_id)

            self.update_progress_ui()

        except Exception:
            pass

    def load_album_art(self, req_id):
        def background_load():
            try:
                loaded_pixmap = None
                if self.art_url:
                    if self.art_url.startswith("file://"):
                        path = urllib.parse.unquote(self.art_url[7:])
                        img = QImage(path)
                        if not img.isNull():
                            loaded_pixmap = QPixmap.fromImage(img)
                    elif self.art_url.startswith("http://") or self.art_url.startswith(
                        "https://"
                    ):
                        req = urllib.request.Request(
                            self.art_url, headers={"User-Agent": "Mozilla/5.0"}
                        )
                        with urllib.request.urlopen(req, timeout=3) as response:
                            data = response.read()
                            img = QImage()
                            if img.loadFromData(data):
                                loaded_pixmap = QPixmap.fromImage(img)
                    else:
                        img = QImage(self.art_url)
                        if not img.isNull():
                            loaded_pixmap = QPixmap.fromImage(img)

                if (
                    not loaded_pixmap or loaded_pixmap.isNull()
                ) and self.track_title and self.track_title != "Unknown":
                    try:
                        query = f"{self.track_artist} {self.track_title}"
                        search_url = (
                            "https://itunes.apple.com/search?term="
                            + urllib.parse.quote(query)
                            + "&entity=song&limit=1"
                        )
                        req = urllib.request.Request(
                            search_url, headers={"User-Agent": "Mozilla/5.0"}
                        )
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            result = json.loads(resp.read().decode("utf-8"))
                            if result.get("resultCount", 0) > 0:
                                artwork_url = result["results"][0].get("artworkUrl100", "")
                                if artwork_url:
                                    high_res_url = artwork_url.replace(
                                        "100x100bb", "600x600bb"
                                    )
                                    art_req = urllib.request.Request(
                                        high_res_url, headers={"User-Agent": "Mozilla/5.0"}
                                    )
                                    with urllib.request.urlopen(art_req, timeout=3) as art_resp:
                                        art_data = art_resp.read()
                                        img = QImage()
                                        if img.loadFromData(art_data):
                                            loaded_pixmap = QPixmap.fromImage(img)
                    except Exception:
                        pass

                if req_id != self.art_request_id:
                    return

                if loaded_pixmap and not loaded_pixmap.isNull():
                    self.cached_pixmap = loaded_pixmap
                    c1, c2 = self.extract_colors_from_pixmap()
                    self.wave_widget.set_colors(c1, c2)
                else:
                    self.cached_pixmap = None
                    self.wave_widget.set_colors(
                        QColor(220, 150, 240), QColor(140, 70, 160)
                    )

                QTimer.singleShot(0, self.apply_loaded_art)
            except Exception:
                if req_id == self.art_request_id:
                    self.cached_pixmap = None
                    QTimer.singleShot(0, self.apply_loaded_art)

        threading.Thread(target=background_load, daemon=True).start()

    def apply_loaded_art(self):
        p = max(0.0, min(1.0, self.p_spring.value))
        asize = int(26 + (76 - 26) * p)
        arad = int(6 + (20 - 6) * p)
        self._last_art_size = (asize, arad)
        self.art_label.setPixmap(self.get_rounded_pixmap(asize, arad))

    def extract_colors_from_pixmap(self):
        if not self.cached_pixmap or self.cached_pixmap.isNull():
            return QColor(220, 150, 240), QColor(140, 70, 160)

        img = self.cached_pixmap.toImage().scaled(
            QSize(4, 4), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        return img.pixelColor(1, 1), img.pixelColor(2, 2)

    def update_progress_ui(self):
        self.title_label.setText(self.track_title)
        self.artist_label.setText(self.track_artist)

        curr_m, curr_s = divmod(self.current_position, 60)
        self.time_left.setText(f"{curr_m}:{curr_s:02d}")

        rem = max(0, self.track_length - self.current_position)
        rem_m, rem_s = divmod(rem, 60)
        self.time_right.setText(f"-{rem_m}:{rem_s:02d}")
        self.update()

    def handle_media_button(self):
        try:
            sender = self.sender()
            if self.mpris_player:
                if sender == self.btn_play:
                    self.is_playing = not self.is_playing
                    if self.is_playing:
                        self.btn_play.set_icon_type("pause", animate=True)
                        self.wave_widget.start_animation()
                    else:
                        self.btn_play.set_icon_type("play", animate=True)
                        self.wave_widget.stop_animation()

                    self.mpris_player.PlayPause()
                elif sender == self.btn_next:
                    self.mpris_player.Next()
                elif sender == self.btn_prev:
                    self.mpris_player.Previous()
        except Exception:
            pass

    def get_rounded_pixmap(self, size, radius):
        if not self.cached_pixmap or self.cached_pixmap.isNull():
            target = QPixmap(size, size)
            target.fill(QColor(30, 30, 30))
            return target

        scaled = self.cached_pixmap.scaled(
            QSize(size, size), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        target = QPixmap(size, size)
        target.fill(Qt.transparent)

        painter = QPainter(target)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, radius, radius)
        painter.setClipPath(path)

        dx = (size - scaled.width()) // 2
        dy = (size - scaled.height()) // 2
        painter.drawPixmap(dx, dy, scaled)
        painter.end()
        return target

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.transparent)

        current_w = self.width() - (self.margin * 2)
        current_h = self.height() - (self.margin * 2)
        p = self.p_spring.value
        p_stiff = self.get_p_stiff(p)

        radius = int(21 + (48 - 21) * p)

        for i in range(3, 0, -1):
            shadow_path = QPainterPath()
            s_rect = QRectF(
                self.margin - i * 2.0,
                self.margin - i * 2.0,
                current_w + i * 4.0,
                current_h + i * 4.0,
            )
            shadow_path.addRoundedRect(s_rect, radius + i, radius + i)
            alpha = int(30 / i)
            painter.fillPath(shadow_path, QColor(0, 0, 0, alpha))

        glass_gradient = QLinearGradient(
            self.margin, self.margin, self.margin, self.margin + current_h
        )
        glass_gradient.setColorAt(0.0, QColor(45, 45, 55, 200))
        glass_gradient.setColorAt(1.0, QColor(20, 20, 25, 215))

        painter.setBrush(glass_gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
        painter.drawRoundedRect(
            self.margin, self.margin, current_w, current_h, radius, radius
        )

        if self.is_expanded or p > 0.05:
            bar_w = 120.0 + (250.0 - 120.0) * p_stiff
            bar_h = 3.0 + (7.0 - 3.0) * p_stiff
            bar_x = self.margin + (current_w - bar_w) / 2.0
            bar_y = (self.margin + 50) + ((self.margin + 127) - (self.margin + 50)) * p_stiff

            bar_alpha = int(255 * max(0.0, min(1.0, p_stiff)))
            bg_alpha = int(60 * max(0.0, min(1.0, p_stiff)))

            bar_rect = QRectF(bar_x, bar_y, bar_w, bar_h)
            bar_radius = bar_h / 2.0

            painter.setBrush(QColor(255, 255, 255, bg_alpha))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bar_rect, bar_radius, bar_radius)

            if self.track_length > 0:
                safe_pos = max(0.0, min(self.current_position_float, float(self.track_length)))
                fill_w = (safe_pos / float(self.track_length)) * bar_w

                if fill_w > 0:
                    painter.setBrush(QColor(255, 255, 255, bar_alpha))
                    clip_path = QPainterPath()
                    clip_path.addRoundedRect(bar_rect, bar_radius, bar_radius)

                    painter.save()
                    painter.setClipPath(clip_path)
                    painter.drawRect(QRectF(bar_x, bar_y, fill_w, bar_h))
                    painter.restore()

    def mousePressEvent(self, event):
        if self.anim_timer.isActive():
            return

        p = self.p_spring.value
        p_stiff = self.get_p_stiff(p)

        if self.is_expanded and p_stiff >= 1.0:
            current_w = self.width() - (self.margin * 2)
            bar_w = int(120 + (250 - 120) * p_stiff)
            bar_h = int(3 + (7 - 3) * p_stiff)
            bar_x = self.margin + (current_w - bar_w) // 2
            bar_y = int(
                (self.margin + 50) + ((self.margin + 127) - (self.margin + 50)) * p_stiff
            )

            bar_rect = QRect(bar_x - 5, bar_y - 8, bar_w + 10, bar_h + 16)
            if bar_rect.contains(event.pos()):
                if self.track_length > 0:
                    rel_x = event.pos().x() - bar_x
                    fraction = max(0.0, min(1.0, rel_x / bar_w))
                    target_sec = fraction * self.track_length
                    self.current_position_float = target_sec
                    self.current_position = int(target_sec)
                    self.update_progress_ui()
                    if self.mpris_player and self.track_id:
                        try:
                            self.mpris_player.SetPosition(
                                dbus.ObjectPath(self.track_id), int(target_sec * 1000000)
                            )
                        except Exception:
                            try:
                                self.mpris_player.SetPosition(
                                    self.track_id, int(target_sec * 1000000)
                                )
                            except Exception:
                                pass
                return

        self.is_expanded = not self.is_expanded

        self.p_spring.set_target(1.0 if self.is_expanded else 0.0)
        self.last_frame_time = time.time()
        if not self.anim_timer.isActive():
            self.anim_timer.start(8)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    island = SpringDynamicIsland()
    island.show()
    sys.exit(app.exec_())
