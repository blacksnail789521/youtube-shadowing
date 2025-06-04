import os
import sys
import shutil
import re
import pysrt
from PyQt5.QtCore import Qt, QTimer, QProcess, QSettings, pyqtSlot, QMetaObject, Q_ARG, QUrl
from PyQt5.QtWebEngineWidgets import QWebEngineView
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
    QSplitter,
    QListWidgetItem,
    QTextEdit,
    QLineEdit,
    QSizePolicy,
    QMessageBox,
    QComboBox,
)
from PyQt5.QtGui import QFont, QIcon
import threading
from get_video_and_srt import run_transcription
import sounddevice as sd
from scipy.io.wavfile import write, read as read_wav
import numpy as np
import tempfile


class ClickableSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            x = event.pos().x()
            ratio = x / self.width()
            new_val = self.minimum() + ratio * (self.maximum() - self.minimum())
            self.setValue(int(new_val))
            self.sliderMoved.emit(int(new_val))
            self.sliderReleased.emit()
        super().mousePressEvent(event)


class VideoPlayer(QWebEngineView):
    """Minimal wrapper around QWebEngineView to control a YouTube iframe."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setZoomFactor(1.0)
        html_path = os.path.join(os.path.dirname(__file__), "player.html")
        self.load(QUrl.fromLocalFile(os.path.abspath(html_path)))
        self.loadFinished.connect(self._on_loaded)
        self._ready = False
        self._pending_id = None

    def _on_loaded(self, ok):
        self._ready = True
        if self._pending_id:
            self.set_video(self._pending_id)
            self._pending_id = None

    def set_video(self, video_id):
        if self._ready:
            self.page().runJavaScript(f"loadVideo('{video_id}')")
        else:
            self._pending_id = video_id

    def play(self):
        self.page().runJavaScript("playVideo()")

    def pause(self):
        self.page().runJavaScript("pauseVideo()")

    def set_time(self, ms):
        self.page().runJavaScript(f"seekTo({ms/1000.0})")

    def get_time(self, callback):
        self.page().runJavaScript("getCurrentTime()", lambda t: callback(int(float(t)*1000)))

    def get_length(self, callback):
        self.page().runJavaScript("getDuration()", lambda d: callback(int(float(d)*1000)))

    def set_rate(self, rate):
        self.page().runJavaScript(f"setPlaybackRate({rate})")


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
            elif event.key() == Qt.Key_R:
                self.record_toggle.setChecked(not self.record_toggle.isChecked())
                self.toggle_record()
                return True
        return super().eventFilter(obj, event)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("English Shadowing Tool with YouTube Videos")
        self.setWindowIcon(QIcon(os.path.join("tools", "icon.ico")))
        self.settings = QSettings("ShadowingApp", "WindowState")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.setGeometry(200, 200, 1200, 700)

        self.manual_jump = False

        self.player = VideoPlayer()

        self.subtitle_index = 0
        self.subtitles = []
        self.project_folder = ""
        self.is_playing = False
        self.total_duration = 0

        # Set to track recorded subtitles by index.
        self.recorded_subtitles = set()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(300)
        self.poll_timer.timeout.connect(self.sync_with_video)

        self.status_output = QTextEdit()
        self.status_output.setReadOnly(True)
        self.status_output.append(
            "👋 Welcome to English Shadowing Tool with YouTube Videos!"
        )

        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.readyReadStandardOutput.connect(self.update_status_output)
        self.process.readyReadStandardError.connect(self.update_status_output)
        self.process.finished.connect(self.on_process_finished)

        self.project_list = QListWidget()
        self.video_frame = self.player
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
        # Let the subtitle display expand horizontally.
        self.subtitle_display.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred
        )

        # Status indicator for recording/playback.
        self.record_status_label = QLabel("")
        self.record_status_label.setFixedWidth(100)
        self.record_status_label.setFont(QFont("Arial", 9))
        self.record_status_label.setStyleSheet("padding-left: 5px;")

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
        for btn in [
            self.skip_back_btn,
            self.play_pause_btn,
            self.skip_forward_btn,
            self.prev_sub_btn,
            self.repeat_sub_btn,
            self.next_sub_btn,
        ]:
            btn.setFixedWidth(400)

        self.slider = ClickableSlider(Qt.Horizontal)
        self.slider.setTracking(True)
        self.slider.sliderMoved.connect(self.slider_moved)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider_label = QLabel("00:00 / 00:00")
        self.slider_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setMinimum(5)
        self.speed_slider.setMaximum(12)
        self.speed_slider.setValue(10)
        self.speed_slider.setTickInterval(1)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.valueChanged.connect(self.change_speed)
        self.speed_label = QLabel("Speed: 100%")
        self.speed_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.slider_was_pressed = False
        self.loop_current = False

        self.record_toggle = QPushButton("🎙️ Record OFF")
        self.record_toggle.setFixedSize(100, 25)
        self.record_toggle.setStyleSheet("font-size: 12px; padding: 2px;")
        self.record_toggle.setCheckable(True)
        self.record_toggle.clicked.connect(self.toggle_record)

        self.recording = False
        self.playing_recorded = False
        self.just_finished_recording = False  # flag to prevent immediate re-trigger

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
        shadow_hint = QLabel("A: ◀ Prev    S: 🔁 Repeat    D: ▶ Next")
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

        self.model_selector = QComboBox()
        self.model_selector.addItems(
            ["tiny", "base", "small", "medium", "large", "turbo"]
        )
        self.model_selector.setCurrentText("turbo")
        self.model_selector.setToolTip("Choose Whisper model to use")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL and press Enter")
        self.url_input.returnPressed.connect(self.process_youtube_url)

        status_widget = QWidget()
        status_layout = QVBoxLayout()
        model_row = QHBoxLayout()
        model_label = QLabel("🧠 Whisper Model:")
        model_label.setFixedWidth(120)
        model_row.addWidget(model_label)
        model_row.addWidget(self.model_selector)
        status_layout.addLayout(model_row)
        url_row = QHBoxLayout()
        url_label = QLabel("🔗 YouTube URL:")
        url_label.setFixedWidth(120)
        url_row.addWidget(url_label)
        url_row.addWidget(self.url_input)
        status_layout.addLayout(url_row)
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

        # --- Bottom controls layout ---
        # Create a horizontal layout for the left compact controls.
        left_controls_layout = QHBoxLayout()
        left_controls_layout.setSpacing(5)
        # Loop toggle composite.
        loop_widget = QWidget()
        loop_layout = QVBoxLayout(loop_widget)
        loop_layout.setContentsMargins(0, 0, 0, 0)
        loop_layout.setSpacing(0)
        loop_layout.addWidget(self.loop_toggle, alignment=Qt.AlignCenter)
        loop_label = QLabel("L Toggle Loop")
        loop_label.setAlignment(Qt.AlignCenter)
        loop_label.setStyleSheet("font-size: 10px; color: gray;")
        loop_layout.addWidget(loop_label)
        left_controls_layout.addWidget(loop_widget)

        # Record toggle composite.
        record_widget = QWidget()
        record_layout = QVBoxLayout(record_widget)
        record_layout.setContentsMargins(0, 0, 0, 0)
        record_layout.setSpacing(0)
        record_layout.addWidget(self.record_toggle, alignment=Qt.AlignCenter)
        record_label = QLabel("R: Toggle Record")
        record_label.setAlignment(Qt.AlignCenter)
        record_label.setStyleSheet("font-size: 10px; color: gray;")
        record_layout.addWidget(record_label)
        left_controls_layout.addWidget(record_widget)

        # Gain label and dropdown.
        gain_label = QLabel("Record Gain:")
        gain_label.setStyleSheet("padding-left: 10px;")
        gain_label.setFixedWidth(80)
        left_controls_layout.addWidget(gain_label)
        self.gain_selector = QComboBox()
        gain_options = ["0.1", "0.25", "0.5"] + [str(i) for i in range(1, 21)]
        self.gain_selector.addItems(gain_options)
        self.gain_selector.setCurrentText("10")
        self.gain_selector.setFixedWidth(60)
        left_controls_layout.addWidget(self.gain_selector)

        # Recording indicator.
        left_controls_layout.addWidget(self.record_status_label)

        # Wrap the left controls in a fixed-size widget.
        left_controls_widget = QWidget()
        left_controls_widget.setLayout(left_controls_layout)
        left_controls_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # Main bottom layout: add left controls then add the subtitle display with a stretch factor.
        subtitle_with_loop_layout = QHBoxLayout()
        subtitle_with_loop_layout.addWidget(left_controls_widget)
        subtitle_with_loop_layout.addWidget(self.subtitle_display, 1)
        # --- End bottom controls layout ---

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
        top_video_splitter.setSizes([100, 1000])

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

    def toggle_record(self):
        self.record_toggle.setText(
            "🎙️ Record ON" if self.record_toggle.isChecked() else "🎙️ Record OFF"
        )
        self.record_toggle.setStyleSheet(
            "font-size: 12px; padding: 2px; background-color: orange;"
            if self.record_toggle.isChecked()
            else "font-size: 12px; padding: 2px;"
        )
        # Force sync update so the bottom subtitle remains in sync.
        self.sync_with_video()

    def record_after_subtitle(self, subtitle):
        self.recording = True
        self.record_status_label.setText("🔴 Recording...")
        # Pause video before recording.
        if self.is_playing:
            self.player.pause()
            self.is_playing = False
            self.play_pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        duration = (
            (subtitle.end.ordinal - subtitle.start.ordinal) * 1.1 / 1000.0
        ) + 2.0  # seconds
        samplerate = 44100
        self.temp_wav_file = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ).name

        def record_and_play():
            try:
                audio = sd.rec(
                    int(duration * samplerate),
                    samplerate=samplerate,
                    channels=1,
                    dtype="int16",
                )
                sd.wait()  # Wait until recording is finished.
                write(self.temp_wav_file, samplerate, audio)
                self.recording = False
                self.playing_recorded = True
                QMetaObject.invokeMethod(
                    self,
                    "play_recorded_audio_wrapper",
                    Qt.QueuedConnection,
                    Q_ARG(str, self.temp_wav_file),
                )
            except Exception as e:
                self.recording = False
                self.record_status_label.setText("⚠️ Rec Failed")
                self.status_output.append(f"❌ Recording error: {str(e)}")

        threading.Thread(target=record_and_play).start()

    @pyqtSlot(str)
    def play_recorded_audio_wrapper(self, filepath):
        self.record_status_label.setText("🔊 Playing...")
        self.play_recorded_audio(filepath)

    def play_recorded_audio(self, filepath):
        try:
            samplerate, data = read_wav(filepath)
            # Get the gain factor from the dropdown. Defaults to 10 if conversion fails.
            try:
                gain = float(self.gain_selector.currentText())
            except Exception:
                gain = 10.0
            # Increase volume by applying the selected gain factor.
            data = np.clip(data * gain, -32768, 32767).astype(np.int16)
            playback_duration = int((data.shape[0] / samplerate) * 1000)
            sd.play(data, samplerate)
            QTimer.singleShot(playback_duration, self.finish_playback)
        except Exception as e:
            self.status_output.append(f"❌ Playback error: {str(e)}")
            self.finish_playback()

    def finish_playback(self):
        self.playing_recorded = False
        self.just_finished_recording = True
        QTimer.singleShot(1000, lambda: setattr(self, "just_finished_recording", False))
        self.record_status_label.setText("")
        # Mark the current subtitle as recorded.
        self.recorded_subtitles.add(self.subtitle_index)
        # Update subtitle display and list without advancing if loop is on.
        if 0 <= self.subtitle_index < len(self.subtitles):
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )
            self.subtitle_list.setCurrentRow(self.subtitle_index)
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
        # If record is on and loop is off, then advance to next subtitle.
        if self.record_toggle.isChecked() and not self.loop_current:
            if self.subtitle_index < len(self.subtitles) - 1:
                self.subtitle_index += 1
                self.subtitle_display.setText(
                    self.subtitles[self.subtitle_index].text.strip()
                )
                self.subtitle_list.setCurrentRow(self.subtitle_index)
                self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
        if not self.is_playing:
            self.player.play()
            self.is_playing = True
            self.play_pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))

    def delete_selected_project(self):
        selected_item = self.project_list.currentItem()
        if not selected_item:
            QMessageBox.warning(
                self, "No Selection", "Please select a YouTube video to delete."
            )
            return
        project_name = selected_item.text()
        project_path = os.path.join("youtube_videos", project_name)

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
            model_size = self.model_selector.currentText()
            self.status_output.append(f"🔄 Processing: {url}")
            self.status_output.append(f"🧠 Using Whisper model: {model_size}")

            def background_task():
                try:
                    run_transcription(
                        url,
                        model_size,
                        "youtube_videos",
                        log_callback=self.status_output.append,
                    )
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
        subtitle_path = os.path.join(self.project_folder, "subtitle.srt")
        url_path = os.path.join(self.project_folder, "url.txt")
        if os.path.exists(url_path):
            with open(url_path, "r", encoding="utf-8") as f:
                url = f.read().strip()
        else:
            url = ""

        video_id_match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11})", url)
        video_id = video_id_match.group(1) if video_id_match else ""
        if not video_id:
            self.subtitle_display.setText("⚠️ Video URL not found")
            return
        self.player.set_video(video_id)
        self.subtitles = pysrt.open(subtitle_path)
        # Reset recorded subtitles when loading a new project.
        self.recorded_subtitles = set()
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
        QTimer.singleShot(2000, self.set_total_duration)

    def set_total_duration(self):
        self.player.get_length(self._update_total_duration)

    def _update_total_duration(self, length):
        self.total_duration = length
        self.slider.setMaximum(self.total_duration)

    def format_time(self, ms):
        seconds = ms // 1000
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02}:{secs:02}"

    def sync_with_video(self):
        if self.manual_jump:
            return

        def handle_time(current_ms):
            if not self.slider_was_pressed:
                self.slider.setValue(current_ms)
                self.slider_label.setText(
                    f"{self.format_time(current_ms)} / {self.format_time(self.total_duration)}"
                )

            if 0 <= self.subtitle_index < len(self.subtitles):
                current_sub = self.subtitles[self.subtitle_index]
                if current_ms >= current_sub.end.ordinal:
                    if self.loop_current and not self.record_toggle.isChecked():
                        self.player.set_time(current_sub.start.ordinal)
                        return

            for i, sub in enumerate(self.subtitles):
                if sub.start.ordinal <= current_ms < sub.end.ordinal:
                    if self.record_toggle.isChecked() and (
                        self.loop_current or self.subtitle_index not in self.recorded_subtitles
                    ):
                        return
                    self.subtitle_index = i
                    self.subtitle_list.setCurrentRow(i)
                    self.subtitle_display.setText(sub.text.strip())
                    return

            if (
                self.record_toggle.isChecked()
                and not self.recording
                and not self.playing_recorded
                and not self.just_finished_recording
            ):
                if 0 <= self.subtitle_index < len(self.subtitles):
                    sub = self.subtitles[self.subtitle_index]
                    if current_ms >= sub.end.ordinal:
                        self.record_after_subtitle(sub)

        self.player.get_time(handle_time)

    def slider_pressed(self):
        self.slider_was_pressed = True

    def slider_released(self):
        self.slider_was_pressed = False
        self.player.set_time(self.slider.value())

    def slider_moved(self, value):
        self.slider_label.setText(
            f"{self.format_time(value)} / {self.format_time(self.total_duration)}"
        )
        self.player.set_time(value)

    def change_speed(self, value):
        rate = value / 10.0
        self.player.set_rate(rate)
        self.speed_label.setText(f"Speed: {int(rate * 100)}%")

    def jump_to_selected_subtitle(self, item):
        index = self.subtitle_list.currentRow()
        if 0 <= index < len(self.subtitles):
            sub = self.subtitles[index]
            self.manual_jump = True
            self.player.set_time(sub.start.ordinal)
            self.subtitle_index = index
            self.subtitle_display.setText(sub.text.strip())
            self.subtitle_list.setCurrentRow(index)
            QTimer.singleShot(3000, lambda: setattr(self, "manual_jump", False))

    def seek_relative(self, offset_ms):
        def do_seek(current):
            self.player.set_time(max(0, current + offset_ms))
        self.player.get_time(do_seek)

    def repeat_subtitle(self):
        if 0 <= self.subtitle_index < len(self.subtitles):
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )

    def prev_subtitle(self):
        if self.subtitle_index > 0:
            self.subtitle_index -= 1
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )
            self.subtitle_list.setCurrentRow(self.subtitle_index)

    def next_subtitle(self):
        if self.subtitle_index < len(self.subtitles) - 1:
            self.subtitle_index += 1
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )
            self.subtitle_list.setCurrentRow(self.subtitle_index)

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
