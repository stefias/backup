import sys
import os
import urllib.parse
import urllib.request
import random
import dbus
from PyQt5.QtCore import Qt, QPropertyAnimation, QRect, QRectF, QEasingCurve, QTimer, QSize
import faulthandler
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QPushButton
from PyQt5.QtGui import QPainter, QColor, QPixmap, QImage, QFont, QPainterPath, QLinearGradient, QPen

faulthandler.enable()

class AnimatedWaveform(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.heights = [6, 12, 18, 9, 15]
        self.is_playing = False
        self.color1 = QColor(220, 150, 240)
        self.color2 = QColor(140, 70, 160)

    def set_colors(self, c1, c2):
        self.color1 = c1
        self.color2 = c2
        self.update()

    def start_animation(self):
        self.is_playing = True
        self.timer.start(120)

    def stop_animation(self):
        self.is_playing = False
        self.timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, self.color1)
        gradient.setColorAt(1.0, self.color2)
        painter.setBrush(gradient)
        
        if self.is_playing:
            self.heights = [random.randint(5, 24) for _ in range(5)]
        else:
            self.heights = [5, 5, 5, 5, 5]
            
        bar_width = 3
        spacing = 3
        for i, h in enumerate(self.heights):
            x = i * (bar_width + spacing)
            y = (self.height() - h) // 2
            painter.drawRoundedRect(x, y, bar_width, h, 1, 1)


class IconButton(QPushButton):
    def __init__(self, icon_type, parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        color = QColor(190, 190, 190) if self.isDown() or self.underMouse() else QColor(255, 255, 255)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        
        w, h = self.width(), self.height()
        
        def draw_rounded_tri(x, y, width, height, right=True):
            path = QPainterPath()
            if right:
                path.moveTo(x, y)
                path.lineTo(x + width, y + height/2)
                path.lineTo(x, y + height)
            else:
                path.moveTo(x + width, y)
                path.lineTo(x, y + height/2)
                path.lineTo(x + width, y + height)
            path.closeSubpath()
            
            painter.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)
            painter.setPen(Qt.NoPen)
            painter.drawPath(path)

        if self.icon_type == "play":
            draw_rounded_tri(w*0.25, h*0.1, w*0.58, h*0.75, True)
        elif self.icon_type == "pause":
            bw = w * 0.3
            bh = h * 0.75
            painter.drawRoundedRect(int(w*0.15), int((h-bh)/2), int(bw), int(bh), 3, 3)
            painter.drawRoundedRect(int(w*0.55), int((h-bh)/2), int(bw), int(bh), 3, 3)
        elif self.icon_type == "next":
            tw = w * 0.42
            th = h * 0.6
            draw_rounded_tri(w*0.05, (h-th)/2, tw, th, True)
            draw_rounded_tri(w*0.5, (h-th)/2, tw, th, True)
        elif self.icon_type == "prev":
            tw = w * 0.42
            th = h * 0.6
            draw_rounded_tri(w*0.08, (h-th)/2, tw, th, False)
            draw_rounded_tri(w*0.53, (h-th)/2, tw, th, False)


class SimpleDynamicIsland(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
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
        self.setGeometry(self.x_pos, self.y_pos, self.collapsed_w, self.collapsed_h)
        
        try:
            self.bus = dbus.SessionBus()
        except Exception as e:
            print(f"DBus Error: {e}")
            self.bus = None

        self.mpris_player = None
        self.is_expanded = False
        self.is_animating = False
        
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
        
        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(750)
        self.animation.setEasingCurve(QEasingCurve.OutExpo)
        self.animation.finished.connect(self.on_animation_finished)

        self.smooth_timer = QTimer(self)
        self.smooth_timer.timeout.connect(self.smooth_tick)
        self.smooth_timer.start(100)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_media)
        self.poll_timer.start(1000)
        
        self.poll_media()

    def setup_ui(self):
        self.art_label = QLabel(self)
        self.art_label.hide()
        
        self.title_label = QLabel(self.track_title, self)
        self.title_label.hide()
        
        self.artist_label = QLabel(self.track_artist, self)
        self.artist_label.hide()
        
        self.wave_widget = AnimatedWaveform(self)
        
        font_time = QFont("Segoe UI", 9, QFont.Medium)
        font_time.setStyleStrategy(QFont.PreferAntialias)
        
        self.time_left = QLabel("0:00", self)
        self.time_left.setFont(font_time)
        self.time_left.setStyleSheet("color: #e0e0e0;")
        self.time_left.hide()
        
        self.time_right = QLabel("-0:00", self)
        self.time_right.setFont(font_time)
        self.time_right.setStyleSheet("color: #e0e0e0;")
        self.time_right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_right.hide()
        
        self.btn_prev = IconButton("prev", self)
        self.btn_play = IconButton("play", self)
        self.btn_next = IconButton("next", self)
        
        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.hide()
            btn.clicked.connect(self.handle_media_button)

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
            services = [s for s in self.bus.list_names() if s.startswith('org.mpris.MediaPlayer2.')]
            if not services:
                return
                
            player_proxy = self.bus.get_object(services[0], '/org/mpris/MediaPlayer2')
            props = dbus.Interface(player_proxy, 'org.freedesktop.DBus.Properties')
            self.mpris_player = dbus.Interface(player_proxy, 'org.mpris.MediaPlayer2.Player')
            
            metadata = props.Get('org.mpris.MediaPlayer2.Player', 'Metadata')
            
            new_track_id = str(metadata.get('mpris:trackid', ''))
            new_title = str(metadata.get('xesam:title', 'Unknown'))
            artists = metadata.get('xesam:artist', ['Unknown'])
            new_artist = str(artists[0]) if artists else 'Unknown'
            new_art = str(metadata.get('mpris:artUrl', ''))
            
            self.track_length = int(metadata.get('mpris:length', 0)) // 1000000
            
            try:
                pos_sec = int(props.Get('org.mpris.MediaPlayer2.Player', 'Position')) // 1000000
                if abs(pos_sec - self.current_position_float) > 1:
                    self.current_position_float = float(pos_sec)
                    self.current_position = pos_sec
            except Exception:
                pass
                
            status = str(props.Get('org.mpris.MediaPlayer2.Player', 'PlaybackStatus'))
            playing = (status == 'Playing')
            
            if playing and not self.is_playing:
                self.is_playing = True
                self.btn_play.icon_type = "pause"
                self.btn_play.update()
                self.wave_widget.start_animation()
            elif not playing and self.is_playing:
                self.is_playing = False
                self.btn_play.icon_type = "play"
                self.btn_play.update()
                self.wave_widget.stop_animation()

            # Handle track and art updates robustly on track change
            track_changed = (new_track_id != self.track_id)
            if track_changed or new_title != self.track_title or new_artist != self.track_artist:
                self.track_id = new_track_id
                self.track_title = new_title
                self.track_artist = new_artist

            if track_changed or (new_art and new_art != self.art_url):
                self.art_url = new_art
                self.art_request_id += 1
                self.load_album_art(self.art_request_id)
            elif not new_art and track_changed:
                self.art_url = ""
                self.cached_pixmap = None
                self.wave_widget.set_colors(QColor(220, 150, 240), QColor(140, 70, 160))

            if self.is_expanded:
                self.art_label.setPixmap(self.get_rounded_pixmap(76, 20))
                self.update_progress_ui()
            else:
                self.art_label.setPixmap(self.get_rounded_pixmap(26, 6))
                
        except Exception as e:
            print(f"[Error in poll_media]: {e}")

    def load_album_art(self, req_id):
        try:
            if not self.art_url:
                self.cached_pixmap = None
                return

            loaded_pixmap = None
            if self.art_url.startswith('file://'):
                path = urllib.parse.unquote(self.art_url[7:])
                img = QImage(path)
                if not img.isNull():
                    loaded_pixmap = QPixmap.fromImage(img)
            elif self.art_url.startswith('http://') or self.art_url.startswith('https://'):
                req = urllib.request.Request(self.art_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=3) as response:
                    data = response.read()
                    img = QImage()
                    if img.loadFromData(data):
                        loaded_pixmap = QPixmap.fromImage(img)
            else:
                img = QImage(self.art_url)
                if not img.isNull():
                    loaded_pixmap = QPixmap.fromImage(img)
            
            if req_id != self.art_request_id:
                return

            if loaded_pixmap and not loaded_pixmap.isNull():
                self.cached_pixmap = loaded_pixmap
                c1, c2 = self.extract_colors_from_pixmap()
                self.wave_widget.set_colors(c1, c2)
            else:
                self.cached_pixmap = None

            if self.is_expanded:
                self.art_label.setPixmap(self.get_rounded_pixmap(76, 20))
            else:
                self.art_label.setPixmap(self.get_rounded_pixmap(26, 6))
        except Exception as e:
            print(f"[Art Load Exception]: {e}")
            if req_id == self.art_request_id:
                self.cached_pixmap = None
                if self.is_expanded:
                    self.art_label.setPixmap(self.get_rounded_pixmap(76, 20))
                else:
                    self.art_label.setPixmap(self.get_rounded_pixmap(26, 6))

    def extract_colors_from_pixmap(self):
        if not self.cached_pixmap or self.cached_pixmap.isNull():
            return QColor(220, 150, 240), QColor(140, 70, 160)
        
        img = self.cached_pixmap.toImage().scaled(
            QSize(4, 4), 
            Qt.IgnoreAspectRatio, 
            Qt.SmoothTransformation
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
            QSize(size, size), 
            Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
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
        radius = 21 if not self.is_expanded else 48
        
        for i in range(8, 0, -1):
            shadow_path = QPainterPath()
            s_rect = QRectF(
                self.margin - i * 1.5,
                self.margin - i * 1.5,
                current_w + i * 3.0,
                current_h + i * 3.0
            )
            shadow_path.addRoundedRect(s_rect, radius + i, radius + i)
            alpha = int(60 / i)
            painter.fillPath(shadow_path, QColor(0, 0, 0, alpha))

        painter.setBrush(QColor(0, 0, 0)) 
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.margin, self.margin, current_w, current_h, radius, radius)
        
        if self.is_expanded:
            bar_y = self.margin + 127
            bar_x = self.margin + 75
            bar_w = 250
            bar_h = 7
            
            painter.setBrush(QColor(255, 255, 255, 60))
            painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 3, 3)
            
            if self.track_length > 0:
                fill_w = int((self.current_position / self.track_length) * bar_w)
                painter.setBrush(QColor(255, 255, 255))
                painter.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, 3, 3)

    def mousePressEvent(self, event):
        if self.is_animating:
            return
            
        self.is_animating = True
        self.animation.stop()
        current_geo = self.geometry()
        
        if not self.is_expanded:
            w, h = self.expanded_w, self.expanded_h
            self.is_expanded = True
            self.show_expanded_ui()
        else:
            w, h = self.collapsed_w, self.collapsed_h
            self.is_expanded = False
            self.show_collapsed_ui()
            
        target_x = (self.screen_width - w) // 2
        target_rect = QRect(target_x, self.y_pos, w, h)
        
        self.animation.setStartValue(current_geo)
        self.animation.setEndValue(target_rect)
        self.animation.start()

    def show_collapsed_ui(self):
        self.title_label.hide()
        self.artist_label.hide()
        self.time_left.hide()
        self.time_right.hide()
        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.hide()
            
        self.art_label.setPixmap(self.get_rounded_pixmap(26, 6))
        self.art_label.setGeometry(self.margin + 12, self.margin + 8, 26, 26)
        self.art_label.show()
        
        self.wave_widget.setGeometry(self.collapsed_w - self.margin - 42, self.margin + 8, 30, 26)
        self.wave_widget.show()

    def show_expanded_ui(self):
        self.art_label.setPixmap(self.get_rounded_pixmap(76, 20))
        self.art_label.setGeometry(self.margin + 20, self.margin + 20, 76, 76)
        self.art_label.show()
        
        font_t = QFont("Segoe UI", 13, QFont.Bold)
        font_t.setStyleStrategy(QFont.PreferAntialias)
        self.title_label.setFont(font_t)
        self.title_label.setStyleSheet("color: white;")
        self.title_label.setGeometry(self.margin + 115, self.margin + 32, 200, 24)
        self.title_label.show()
        
        font_a = QFont("Segoe UI", 12)
        font_a.setStyleStrategy(QFont.PreferAntialias)
        self.artist_label.setFont(font_a)
        self.artist_label.setStyleSheet("color: #bbbbbb;")
        self.artist_label.setGeometry(self.margin + 115, self.margin + 58, 200, 24)
        self.artist_label.show()
        
        self.wave_widget.setGeometry(self.expanded_w - self.margin - 55, self.margin + 35, 35, 25)
        self.wave_widget.show()
        
        self.time_left.setGeometry(self.margin + 25, self.margin + 120, 45, 20)
        self.time_left.show()
        
        self.time_right.setGeometry(self.expanded_w - self.margin - 70, self.margin + 120, 45, 20)
        self.time_right.show()
        
        self.btn_prev.setGeometry(self.margin + 107, self.margin + 150, 44, 28)
        self.btn_play.setGeometry(self.margin + 180, self.margin + 144, 40, 40)
        self.btn_next.setGeometry(self.margin + 253, self.margin + 150, 44, 28)
        
        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.show()
            
        self.update_progress_ui()

    def on_animation_finished(self):
        QTimer.singleShot(100, lambda: setattr(self, 'is_animating', False))

if __name__ == '__main__':
    app = QApplication(sys.argv)
    island = SimpleDynamicIsland()
    island.show_collapsed_ui()
    island.show()
    sys.exit(app.exec_())
