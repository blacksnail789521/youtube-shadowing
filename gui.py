import os
import sys
import shutil
import vlc
import pysrt
from PyQt5.QtCore import Qt, QTimer, QProcess, QSettings
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSlider,
    QStyle,
    QFrame,
    QSplitter,
    QListWidgetItem,
    QTextEdit,
    QLineEdit,
    QSizePolicy,
    QMessageBox,
)
from PyQt5.QtGui import QFont
import threading
from get_video_and_srt import run_transcription


class ShadowingApp(QWidget):
    def eventFilter(self, obj, event):
        if event.type() == event.KeyPress:
            if event.key() == Qt.Key_Space:
                self.toggle_play_pause()
                return True
            elif event.key() == Qt.Key_Left:
                self.seek_relative(-5000)
                return True
            elif event.key() == Qt.Key_Right:
                self.seek_relative(5000)
                return True
            elif event.key() == Qt.Key_A:
                self.prev_subtitle()
                return True
            elif event.key() == Qt.Key_S:
                self.repeat_subtitle()
                return True
            elif event.key() == Qt.Key_D:
                self.next_subtitle()
                return True
            elif event.key() == Qt.Key_L:
                self.loop_toggle.setChecked(not self.loop_toggle.isChecked())
                self.toggle_loop()
                return True
        return super().eventFilter(obj, event)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎧 English Shadowing Tool with YouTube Videos")
        self.settings = QSettings("ShadowingApp", "WindowState")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.setGeometry(200, 200, 1200, 700)

        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        self.subtitle_index = 0
        self.subtitles = []
        self.project_folder = ""
        self.is_playing = False
        self.total_duration = 0

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(300)
        self.poll_timer.timeout.connect(self.sync_with_video)

        self.status_output = QTextEdit()
        self.status_output.setReadOnly(True)
        self.status_output.append(
            "👋 Welcome to English Shadowing Tool with YouTube Videos!"
        )

        self.process = QProcess()
        self.process.setProgram(sys.executable)
        self.process.readyReadStandardOutput.connect(self.update_status_output)
        self.process.readyReadStandardError.connect(self.update_status_output)
        self.process.finished.connect(self.on_process_finished)

        self.project_list = QListWidget()
        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setMinimumHeight(400)

        self.loop_toggle = QPushButton("🔁 Loop OFF")
        self.loop_toggle.setFixedSize(100, 25)
        self.loop_toggle.setStyleSheet("font-size: 12px; padding: 2px;")
        self.loop_toggle.setCheckable(True)
        self.loop_toggle.clicked.connect(self.toggle_loop)

        self.subtitle_display = QLabel("--")
        self.subtitle_display.setWordWrap(True)
        self.subtitle_display.setFixedHeight(50)
        self.subtitle_display.setFont(QFont("Arial", 16))
        self.subtitle_display.setAlignment(Qt.AlignCenter)

        self.subtitle_list = QListWidget()
        self.subtitle_list.setWordWrap(True)
        self.subtitle_list.itemClicked.connect(self.jump_to_selected_subtitle)

        self.play_pause_btn = QPushButton(
            self.style().standardIcon(QStyle.SP_MediaPlay), ""
        )
        self.skip_back_btn = QPushButton("⏪ 5s")
        self.skip_forward_btn = QPushButton("⏩ 5s")
        self.prev_sub_btn = QPushButton("⏮️ Subtitle")
        self.repeat_sub_btn = QPushButton("🔁 Subtitle")
        self.next_sub_btn = QPushButton("⏭️ Subtitle")

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setTracking(True)
        self.slider.sliderMoved.connect(self.slider_moved)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider_label = QLabel("00:00 / 00:00")
        self.slider_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setMinimum(5)
        self.speed_slider.setMaximum(20)
        self.speed_slider.setValue(10)
        self.speed_slider.setTickInterval(1)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.valueChanged.connect(self.change_speed)
        self.speed_label = QLabel("Speed: 100%")
        self.speed_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.slider_was_pressed = False
        self.loop_current = False

        self.init_ui()
        self.load_projects()

    def init_ui(self):
        control_layout = QHBoxLayout()
        control_layout.addWidget(self.skip_back_btn)
        control_layout.addWidget(self.play_pause_btn)
        control_layout.addWidget(self.skip_forward_btn)
        control_hint = QLabel("←/→: Seek    Space: Play/Pause")
        control_hint.setStyleSheet("color: gray; padding-left: 10px;")
        control_layout.addWidget(control_hint)

        shadow_layout = QHBoxLayout()
        shadow_hint = QLabel("A: ◀ Prev    S: 🔁 Repeat    D: ▶ Next    L: Toggle Loop")
        shadow_hint.setStyleSheet("color: gray; padding-left: 10px;")
        shadow_layout.addWidget(self.prev_sub_btn)
        shadow_layout.addWidget(self.repeat_sub_btn)
        shadow_layout.addWidget(self.next_sub_btn)
        shadow_layout.addWidget(shadow_hint)

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.slider_label)
        slider_layout.addWidget(self.slider)

        speed_layout = QHBoxLayout()
        speed_layout.addWidget(self.speed_label)
        speed_layout.addWidget(self.speed_slider)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL and press Enter")
        self.url_input.returnPressed.connect(self.process_youtube_url)

        status_widget = QWidget()
        status_layout = QVBoxLayout()
        status_layout.addWidget(self.url_input)
        status_layout.addWidget(QLabel("📄 Status:"))
        status_layout.addWidget(self.status_output)
        status_widget.setLayout(status_layout)

        projects_header_layout = QHBoxLayout()
        projects_label = QLabel("📺 YouTube Videos:")

        refresh_button = QPushButton("🔄 Refresh")
        refresh_button.setFixedSize(100, 25)
        refresh_button.setToolTip("Refresh YouTube Video List")
        refresh_button.clicked.connect(self.load_projects)

        delete_button = QPushButton("🗑️ Delete")
        delete_button.setFixedSize(100, 25)
        delete_button.setToolTip("Delete Selected YouTube Video")
        delete_button.clicked.connect(self.delete_selected_project)

        projects_header_layout.addWidget(projects_label)
        projects_header_layout.addStretch()
        projects_header_layout.addWidget(refresh_button)
        projects_header_layout.addWidget(delete_button)

        project_widget = QWidget()
        project_layout = QVBoxLayout()
        project_layout.addLayout(projects_header_layout)
        project_layout.addWidget(self.project_list)
        project_widget.setLayout(project_layout)

        top_row_splitter = QSplitter(Qt.Horizontal)
        top_row_splitter.setStyleSheet(
            "QSplitter::handle { background-color: lightgray; }"
        )
        top_row_splitter.addWidget(status_widget)
        top_row_splitter.addWidget(project_widget)
        top_row_splitter.setSizes([600, 600])

        video_display_layout = QVBoxLayout()
        video_display_layout.addWidget(self.video_frame)
        video_display_layout.addLayout(slider_layout)

        subtitle_with_loop_layout = QHBoxLayout()
        subtitle_with_loop_layout.addWidget(self.loop_toggle)
        subtitle_with_loop_layout.addWidget(self.subtitle_display)
        video_display_layout.addLayout(subtitle_with_loop_layout)

        video_display_widget = QWidget()
        video_display_widget.setLayout(video_display_layout)
        video_display_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        controls_layout = QVBoxLayout()
        controls_layout.addLayout(control_layout)
        controls_layout.addLayout(shadow_layout)
        controls_layout.addLayout(speed_layout)

        controls_widget = QWidget()
        controls_widget.setLayout(controls_layout)
        controls_widget.setFixedHeight(130)
        controls_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        video_splitter = QSplitter(Qt.Vertical)
        video_splitter.setStyleSheet(
            "QSplitter::handle { background-color: lightgray; }"
        )
        video_splitter.addWidget(video_display_widget)
        video_splitter.addWidget(controls_widget)

        video_widget = video_splitter

        top_video_splitter = QSplitter(Qt.Vertical)
        top_video_splitter.setStyleSheet(
            "QSplitter::handle { background-color: lightgray; }"
        )
        top_video_splitter.addWidget(top_row_splitter)
        top_video_splitter.addWidget(video_widget)
        top_video_splitter.setSizes([300, 500])

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("🧾 Subtitle List:"))
        right_layout.addWidget(self.subtitle_list)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setStyleSheet(
            "QSplitter::handle { background-color: lightgray; }"
        )
        main_splitter.addWidget(top_video_splitter)
        main_splitter.addWidget(right_widget)
        main_splitter.setSizes([900, 300])

        main_layout = QVBoxLayout()
        main_layout.addWidget(main_splitter)
        self.setLayout(main_layout)

        self.project_list.itemClicked.connect(self.load_project)
        self.play_pause_btn.clicked.connect(self.toggle_play_pause)
        self.skip_back_btn.clicked.connect(lambda: self.seek_relative(-5000))
        self.skip_forward_btn.clicked.connect(lambda: self.seek_relative(5000))
        self.prev_sub_btn.clicked.connect(self.prev_subtitle)
        self.repeat_sub_btn.clicked.connect(self.repeat_subtitle)
        self.next_sub_btn.clicked.connect(self.next_subtitle)

    def delete_selected_project(self):
        selected_item = self.project_list.currentItem()
        if not selected_item:
            QMessageBox.warning(
                self, "No Selection", "Please select a YouTube video to delete."
            )
            return

        project_name = selected_item.text()
        project_path = os.path.join("youtube_videos", project_name)

        # If this YouTube video is currently loaded, stop the video and release the file
        if self.project_folder == project_path:
            self.player.stop()
            self.player.set_media(None)

        confirm = QMessageBox.question(
            self,
            "Delete YouTube Video",
            f"Are you sure you want to permanently delete:\n\n{project_name}?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm == QMessageBox.Yes:
            try:
                shutil.rmtree(project_path)
                row = self.project_list.row(selected_item)
                self.project_list.takeItem(row)
                self.status_output.append(f"🗑️ Deleted YouTube Video: {project_name}")
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to delete YouTube video:\n{str(e)}"
                )

    def process_youtube_url(self):
        url = self.url_input.text().strip()
        if url:
            self.url_input.clear()
            self.status_output.append(f"🔄 Processing: {url}")
            def background_task():
                try:
                    run_transcription(url, "youtube_videos", log_callback=self.status_output.append)
                    self.status_output.append("✅ Done. Refreshing list...")
                    self.load_projects()
                except Exception as e:
                    self.status_output.append(f"❌ Error: {str(e)}")

            threading.Thread(target=background_task).start()

    def on_process_finished(self):
        self.load_projects()

    def update_status_output(self):
        data = (
            self.process.readAllStandardOutput() + self.process.readAllStandardError()
        )
        text = str(data, encoding="utf-8")
        self.status_output.append(text)

    def toggle_play_pause(self):
        if self.is_playing:
            self.player.pause()
            self.play_pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        else:
            self.player.play()
            self.play_pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.is_playing = not self.is_playing

    def load_projects(self):
        existing_projects = {
            self.project_list.item(i).text() for i in range(self.project_list.count())
        }
        updated_projects = set()
        if not os.path.exists("youtube_videos"):
            os.makedirs("youtube_videos")
        for name in os.listdir("youtube_videos"):
            folder = os.path.join("youtube_videos", name)
            subtitle_path = os.path.join(folder, "subtitle.srt")
            video_exists = any(f.startswith("video") for f in os.listdir(folder))
            if os.path.isdir(folder) and video_exists and os.path.exists(subtitle_path):
                updated_projects.add(name)
                if name not in existing_projects:
                    self.project_list.addItem(name)
        for i in reversed(range(self.project_list.count())):
            if self.project_list.item(i).text() not in updated_projects:
                self.project_list.takeItem(i)

    def load_project(self, item):
        project_title = item.text()
        self.project_folder = os.path.join("youtube_videos", project_title)
        video_file = next(
            (f for f in os.listdir(self.project_folder) if f.startswith("video")), None
        )
        subtitle_path = os.path.join(self.project_folder, "subtitle.srt")
        if not video_file:
            self.subtitle_display.setText("⚠️ No video found")
            return
        video_path = os.path.join(self.project_folder, video_file)
        media = self.instance.media_new(video_path)
        self.player.set_media(media)
        if sys.platform.startswith("linux"):
            self.player.set_xwindow(self.video_frame.winId())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winId())
        elif sys.platform == "darwin":
            self.player.set_nsobject(int(self.video_frame.winId()))
        self.subtitles = pysrt.open(subtitle_path)
        self.subtitle_list.clear()
        for sub in self.subtitles:
            item = QListWidgetItem(sub.text.strip())
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
            self.subtitle_list.addItem(item)
        self.subtitle_index = 0
        self.player.play()
        self.is_playing = True
        self.play_pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.poll_timer.start()
        QTimer.singleShot(1000, self.set_total_duration)

    def set_total_duration(self):
        self.total_duration = self.player.get_length()
        self.slider.setMaximum(self.total_duration)

    def format_time(self, ms):
        seconds = ms // 1000
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02}:{secs:02}"

    def sync_with_video(self):
        if not self.slider_was_pressed:
            current_ms = self.player.get_time()
            self.slider.setValue(current_ms)
            self.slider_label.setText(
                f"{self.format_time(current_ms)} / {self.format_time(self.total_duration)}"
            )
        current_ms = self.player.get_time()
        if self.loop_current and 0 <= self.subtitle_index < len(self.subtitles):
            sub = self.subtitles[self.subtitle_index]
            if current_ms > sub.end.ordinal:
                self.player.set_time(sub.start.ordinal)
        for i, sub in enumerate(self.subtitles):
            if sub.start.ordinal <= current_ms <= sub.end.ordinal:
                self.subtitle_index = i
                self.subtitle_list.setCurrentRow(i)
                self.subtitle_display.setText(sub.text.strip())
                break

    def slider_pressed(self):
        self.slider_was_pressed = True

    def slider_released(self):
        self.slider_was_pressed = False
        self.player.set_time(self.slider.value())

    def slider_moved(self, value):
        self.slider_label.setText(
            f"{self.format_time(value)} / {self.format_time(self.total_duration)}"
        )

    def change_speed(self, value):
        rate = value / 10.0
        self.player.set_rate(rate)
        self.speed_label.setText(f"Speed: {int(rate * 100)}%")

    def jump_to_selected_subtitle(self, item):
        index = self.subtitle_list.currentRow()
        if 0 <= index < len(self.subtitles):
            self.player.set_time(self.subtitles[index].start.ordinal)

    def seek_relative(self, offset_ms):
        current = self.player.get_time()
        self.player.set_time(max(0, current + offset_ms))

    def repeat_subtitle(self):
        if 0 <= self.subtitle_index < len(self.subtitles):
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)

    def prev_subtitle(self):
        if self.subtitle_index > 0:
            self.subtitle_index -= 1
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)

    def next_subtitle(self):
        if self.subtitle_index < len(self.subtitles) - 1:
            self.subtitle_index += 1
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)

    def toggle_loop(self):
        self.loop_current = not self.loop_current
        self.loop_toggle.setText("🔁 Loop ON" if self.loop_current else "🔁 Loop OFF")
        self.loop_toggle.setStyleSheet(
            "font-size: 12px; padding: 2px; background-color: lightgreen;"
            if self.loop_current
            else "font-size: 12px; padding: 2px;"
        )

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ShadowingApp()
    win.show()
    app.installEventFilter(win)
    sys.exit(app.exec_())
