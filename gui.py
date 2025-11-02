import os
import sys
import shutil
import vlc
import pysrt
from PyQt5.QtCore import Qt, QTimer, QProcess, QSettings, pyqtSlot, QMetaObject, Q_ARG
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
    QComboBox,
)
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor
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
            elif event.key() == Qt.Key_P:
                self.auto_play_toggle.setChecked(not self.auto_play_toggle.isChecked())
                self.toggle_auto_play()
                return True
            elif event.key() == Qt.Key_Q:
                # Decrease speed by one step (10%)
                current = self.speed_slider.value()
                if current > self.speed_slider.minimum():
                    self.speed_slider.setValue(current - 1)
                return True
            elif event.key() == Qt.Key_E:
                # Increase speed by one step (10%)
                current = self.speed_slider.value()
                if current < self.speed_slider.maximum():
                    self.speed_slider.setValue(current + 1)
                return True

        return super().eventFilter(obj, event)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("English Shadowing Tool with YouTube Videos")
        
        # Detect correct base path for both PyInstaller and dev mode
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS  # PyInstaller temp folder (onefile)
        else:
            base_path = os.path.dirname(__file__)

        # Try both possible icon locations (tools/ for dev, dist/ for onedir)
        icon_path = os.path.join(base_path, "tools", "icon.ico")
        if not os.path.exists(icon_path):
            # Fallback: icon.ico next to the exe (onedir build)
            icon_path = os.path.join(os.path.dirname(sys.executable), "icon.ico")

        self.setWindowIcon(QIcon(icon_path))

        self.settings = QSettings("ShadowingApp", "WindowState")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.setGeometry(200, 200, 1200, 700)


        self.manual_jump = False

        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        self.subtitle_index = 0
        self.subtitles = []
        self.project_folder = ""
        self.is_playing = False
        self.total_duration = 0
        self.target_jump_ms = None

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
        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setMinimumHeight(400)
        
        # Theme: apply dark mode
        self.apply_theme()

        self.auto_play_toggle = QPushButton("🎵 Auto Play ON")
        self.auto_play_toggle.setFixedSize(100, 25)
        self.auto_play_toggle.setStyleSheet(
            "font-size: 12px; padding: 2px; background-color: #4682B4; color: white;"
        )
        self.auto_play_toggle.setCheckable(True)
        self.auto_play_toggle.setChecked(True)  # Default enabled
        self.auto_play_toggle.clicked.connect(self.toggle_auto_play)

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
        self.speed_slider.setMaximum(15)
        self.speed_slider.setValue(10)
        self.speed_slider.setTickInterval(1)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.valueChanged.connect(self.change_speed)
        self.speed_label = QLabel("Speed: 100%")
        self.speed_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.slider_was_pressed = False
        self.loop_current = False
        self.auto_play_enabled = True
        self.auto_play_paused_for_subtitle = False  # Reset pause flag for new project

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
        speed_hint = QLabel("Q: 10% Slower    E: 10% Faster")
        speed_hint.setStyleSheet("color: gray; padding-left: 10px;")
        speed_layout.addWidget(speed_hint)
        speed_layout.addStretch()  # Ensures items align left
        self.speed_slider.setFixedWidth(1140)

        # === Left status panel ===
        status_widget = QWidget()
        status_layout = QVBoxLayout()

        # --- Whisper Model row ---
        self.model_selector = QComboBox()
        self.model_selector.addItems(
            ["tiny", "base", "small", "medium", "large", "turbo"]
        )
        self.model_selector.setCurrentText("base")
        self.model_selector.setToolTip("Choose Whisper model to use")
        model_row = QHBoxLayout()
        model_label = QLabel("🧠 Whisper Model:")
        model_label.setFixedWidth(140)
        model_row.addWidget(model_label)
        model_row.addWidget(self.model_selector)
        status_layout.addLayout(model_row)

        # --- YouTube URL row ---
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL and press Enter")
        self.url_input.returnPressed.connect(self.process_youtube_url)
        url_row = QHBoxLayout()
        url_label = QLabel("🔗 YouTube URL:")
        url_label.setFixedWidth(140)
        url_row.addWidget(url_label)
        url_row.addWidget(self.url_input)
        status_layout.addLayout(url_row)

        # --- Max Words per Subtitle row ---
        self.max_words_selector = QComboBox()
        self.max_words_selector.addItems([str(i) for i in range(10, 31)])  # 10 → 30
        self.max_words_selector.setCurrentText("15")  # Default value
        self.max_words_selector.setToolTip("Maximum number of words before a subtitle may split")
        max_row = QHBoxLayout()
        max_label = QLabel("🧾 Max Words per Subtitle:")
        max_label.setFixedWidth(140)
        max_row.addWidget(max_label)
        max_row.addWidget(self.max_words_selector)
        status_layout.addLayout(max_row)

        # --- Status output area ---
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

        # Auto play toggle composite.
        auto_play_widget = QWidget()
        auto_play_layout = QVBoxLayout(auto_play_widget)
        auto_play_layout.setContentsMargins(0, 0, 0, 0)
        auto_play_layout.setSpacing(0)
        auto_play_layout.addWidget(self.auto_play_toggle, alignment=Qt.AlignCenter)
        auto_play_label = QLabel("P: Toggle Auto Play")
        auto_play_label.setAlignment(Qt.AlignCenter)
        auto_play_label.setStyleSheet("font-size: 10px; color: gray;")
        auto_play_layout.addWidget(auto_play_label)
        left_controls_layout.addWidget(auto_play_widget)

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

        # === Record Gain + Subtitle Font stacked vertically ===
        gain_font_widget = QWidget()
        gain_font_layout = QVBoxLayout(gain_font_widget)
        gain_font_layout.setContentsMargins(0, 0, 0, 0)
        gain_font_layout.setSpacing(2)

        # Record Gain row
        gain_row = QHBoxLayout()
        gain_label = QLabel("Record Gain:")
        gain_label.setFixedWidth(90)
        gain_label.setStyleSheet("padding-left: 10px;")
        gain_row.addWidget(gain_label)

        self.gain_selector = QComboBox()
        gain_options = ["0.1", "0.25", "0.5"] + [str(i) for i in range(1, 21)]
        self.gain_selector.addItems(gain_options)
        self.gain_selector.setCurrentText("10")
        self.gain_selector.setFixedWidth(60)
        gain_row.addWidget(self.gain_selector)
        gain_row.addStretch()
        gain_font_layout.addLayout(gain_row)

        # Subtitle Font row
        font_row = QHBoxLayout()
        font_label = QLabel("Subtitle Font:")
        font_label.setFixedWidth(90)
        font_label.setStyleSheet("padding-left: 10px;")
        font_row.addWidget(font_label)

        self.subtitle_font_size_selector = QComboBox()
        font_sizes = [str(i) for i in range(10, 25)]  # 10–24 pt
        self.subtitle_font_size_selector.addItems(font_sizes)
        self.subtitle_font_size_selector.setCurrentText("16")
        self.subtitle_font_size_selector.setFixedWidth(60)
        self.subtitle_font_size_selector.currentTextChanged.connect(self.change_subtitle_font_size)
        font_row.addWidget(self.subtitle_font_size_selector)
        font_row.addStretch()
        gain_font_layout.addLayout(font_row)

        # Add the combined widget to the left controls area
        left_controls_layout.addWidget(gain_font_widget)


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

    def toggle_auto_play(self):
        self.auto_play_enabled = self.auto_play_toggle.isChecked()
        self.auto_play_toggle.setText(
            "🎵 Auto Play ON" if self.auto_play_enabled else "🎵 Auto Play OFF"
        )
        self.auto_play_toggle.setStyleSheet(
            "font-size: 12px; padding: 2px; background-color: #4682B4; color: white;"
            if self.auto_play_enabled
            else "font-size: 12px; padding: 2px;"
        )
        # Force sync update so the bottom subtitle remains in sync.
        self.sync_with_video()

    def toggle_record(self):
        self.record_toggle.setText(
            "🎙️ Record ON" if self.record_toggle.isChecked() else "🎙️ Record OFF"
        )
        self.record_toggle.setStyleSheet(
            "font-size: 12px; padding: 2px; background-color: #FF8C00; color: white;"
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
            # Only set player time here if loop is on; for other cases we handle below
            if self.loop_current:
                self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)

        # Apply auto play logic after recording
        if not self.auto_play_enabled:
            if self.loop_current:
                # Jump back to start of current subtitle and pause
                if 0 <= self.subtitle_index < len(self.subtitles):
                    self.player.set_time(
                        self.subtitles[self.subtitle_index].start.ordinal
                    )
            else:
                # Loop is off: jump to start of next subtitle (if exists)
                next_idx = self.subtitle_index + 1
                if next_idx < len(self.subtitles):
                    self.subtitle_index = next_idx
                    self.subtitle_display.setText(
                        self.subtitles[self.subtitle_index].text.strip()
                    )
                    self.subtitle_list.setCurrentRow(self.subtitle_index)
                    self.player.set_time(
                        self.subtitles[self.subtitle_index].start.ordinal
                    )
            # Pause the video
            if self.is_playing:
                self.player.pause()
                self.is_playing = False
                self.play_pause_btn.setIcon(
                    self.style().standardIcon(QStyle.SP_MediaPlay)
                )
            # Mark that we've paused for this subtitle
            self.auto_play_paused_for_subtitle = True
        else:
            # If record is on and loop is off, then advance to next subtitle.
            if self.record_toggle.isChecked() and not self.loop_current:
                if self.subtitle_index < len(self.subtitles) - 1:
                    self.subtitle_index += 1
                    self.subtitle_display.setText(
                        self.subtitles[self.subtitle_index].text.strip()
                    )
                    self.subtitle_list.setCurrentRow(self.subtitle_index)
                    self.player.set_time(
                        self.subtitles[self.subtitle_index].start.ordinal
                    )
            if not self.is_playing:
                self.player.play()
                self.is_playing = True
                self.play_pause_btn.setIcon(
                    self.style().standardIcon(QStyle.SP_MediaPause)
                )

    def delete_selected_project(self):
        selected_item = self.project_list.currentItem()
        if not selected_item:
            QMessageBox.warning(
                self, "No Selection", "Please select a YouTube video to delete."
            )
            return
        project_name = selected_item.text()
        project_path = os.path.join("youtube_videos", project_name)
        if self.project_folder == project_path:
            # Stop and clear current playback and subtitles
            self.player.stop()
            self.player.set_media(None)
            self.subtitle_list.clear()
            self.subtitle_display.setText("--")
            self.project_folder = ""
            self.subtitles = []
            self.subtitle_index = 0
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
                    max_words = int(self.max_words_selector.currentText())
                    run_transcription(
                        url,
                        model_size,
                        "youtube_videos",
                        log_callback=self.status_output.append,
                        max_words=max_words,
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
            # Reset pause flag when manually starting playback
            if not self.auto_play_enabled:
                self.auto_play_paused_for_subtitle = False
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
        # Reset recorded subtitles when loading a new project.
        self.recorded_subtitles = set()
        self.auto_play_paused_for_subtitle = False  # Reset pause flag for new project
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
        current_ms = self.player.get_time()
        if not self.slider_was_pressed:
            self.slider.setValue(current_ms)
            self.slider_label.setText(
                f"{self.format_time(current_ms)} / {self.format_time(self.total_duration)}"
            )

        if self.manual_jump:
            return

        # ---- Auto Play Pause (check previous subtitle end) ----
        if (
            not self.auto_play_enabled
            and not self.record_toggle.isChecked()
            and 0 <= self.subtitle_index < len(self.subtitles)
            and not self.auto_play_paused_for_subtitle
        ):
            prev_sub = self.subtitles[self.subtitle_index]
            if current_ms >= prev_sub.end.ordinal:
                # Pause
                if self.is_playing:
                    self.player.pause()
                    self.is_playing = False
                    self.play_pause_btn.setIcon(
                        self.style().standardIcon(QStyle.SP_MediaPlay)
                    )
                # Jump logic
                if self.loop_current:
                    # Loop: jump back to start of same subtitle
                    self.player.set_time(prev_sub.start.ordinal)
                else:
                    # Not looping: jump to next subtitle start if exists
                    next_idx = self.subtitle_index + 1
                    if next_idx < len(self.subtitles):
                        self.player.set_time(self.subtitles[next_idx].start.ordinal)
                        self.subtitle_index = next_idx
                        self.subtitle_list.setCurrentRow(next_idx)
                        self.subtitle_display.setText(
                            self.subtitles[next_idx].text.strip()
                        )
                self.auto_play_paused_for_subtitle = True
                return

        # ---- 1. Handle Loop ----
        loop_jumped = False
        if self.loop_current and not self.record_toggle.isChecked():
            # If we have a valid subtitle
            if 0 <= self.subtitle_index < len(self.subtitles):
                sub = self.subtitles[self.subtitle_index]
                # If we've passed the end of the current subtitle, rewind to its start
                if current_ms >= sub.end.ordinal:
                    self.player.set_time(sub.start.ordinal)
                    # If auto play is disabled, pause after jumping back
                    if (
                        not self.auto_play_enabled
                        and not self.auto_play_paused_for_subtitle
                    ):
                        QTimer.singleShot(100, self.pause_after_loop_jump)
                        self.auto_play_paused_for_subtitle = True
                    return  # Avoid falling through to subtitle advancement

        # ---- 2. Handle Recording ----
        if (
            self.record_toggle.isChecked()
            and not self.recording
            and not self.playing_recorded
            and not self.just_finished_recording
        ):
            if 0 <= self.subtitle_index < len(self.subtitles):
                sub = self.subtitles[self.subtitle_index]
                if current_ms >= sub.end.ordinal:
                    self.player.set_time(sub.end.ordinal)
                    self.record_after_subtitle(sub)
                    return

        # ---- 3. Advance subtitle_index and handle auto play logic ----
        if not self.record_toggle.isChecked():
            subtitle_changed = False
            for i, sub in enumerate(self.subtitles):
                if sub.start.ordinal <= current_ms < sub.end.ordinal:
                    # Check if we moved to a new subtitle
                    if self.subtitle_index != i:
                        self.auto_play_paused_for_subtitle = False
                    self.subtitle_index = i
                    self.subtitle_list.setCurrentRow(i)
                    self.subtitle_display.setText(sub.text.strip())
                    break
            else:
                # We're not in any subtitle range - no additional auto play logic needed here
                pass

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

    def change_subtitle_font_size(self, size_str):
        try:
            size = int(size_str)
            current_font = self.subtitle_display.font()
            current_font.setPointSize(size)
            self.subtitle_display.setFont(current_font)
        except ValueError:
            pass

    def wait_for_seek(self, target_ms, retries=10):
        def check_seek():
            cur = self.player.get_time()
            if abs(cur - target_ms) < 500 or retries <= 0:  # allow 0.5s slack
                self.manual_jump = False
                self.target_jump_ms = None
            else:
                QTimer.singleShot(
                    50, lambda: self.wait_for_seek(target_ms, retries - 1)
                )

        QTimer.singleShot(50, check_seek)

    def jump_to_selected_subtitle(self, item):
        # Cancel previous jump
        self.manual_jump = False
        self.target_jump_ms = None

        index = self.subtitle_list.currentRow()
        if 0 <= index < len(self.subtitles):
            sub = self.subtitles[index]
            self.manual_jump = True
            self.target_jump_ms = sub.start.ordinal
            self.auto_play_paused_for_subtitle = False  # Reset pause flag when jumping

            state = self.player.get_state()
            if state in [vlc.State.Ended, vlc.State.Stopped]:
                self.player.stop()
                self.player.play()
                QTimer.singleShot(
                    200, lambda: self._seek_and_update_subtitle(index, sub)
                )
            else:
                self._seek_and_update_subtitle(index, sub)
            self.wait_for_seek(sub.start.ordinal)

    def _seek_and_update_subtitle(self, index, sub):
        self.player.set_time(sub.start.ordinal)
        self.subtitle_index = index
        self.subtitle_display.setText(sub.text.strip())
        self.subtitle_list.setCurrentRow(index)

    def seek_relative(self, offset_ms):
        current = self.player.get_time()
        self.player.set_time(max(0, current + offset_ms))

    def repeat_subtitle(self):
        if 0 <= self.subtitle_index < len(self.subtitles):
            self.auto_play_paused_for_subtitle = False  # Reset pause flag
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )

    def prev_subtitle(self):
        if self.subtitle_index > 0:
            self.subtitle_index -= 1
            self.auto_play_paused_for_subtitle = False  # Reset pause flag
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )
            self.subtitle_list.setCurrentRow(self.subtitle_index)

    def next_subtitle(self):
        if self.subtitle_index < len(self.subtitles) - 1:
            self.subtitle_index += 1
            self.auto_play_paused_for_subtitle = False  # Reset pause flag
            self.player.set_time(self.subtitles[self.subtitle_index].start.ordinal)
            self.subtitle_display.setText(
                self.subtitles[self.subtitle_index].text.strip()
            )
            self.subtitle_list.setCurrentRow(self.subtitle_index)

    def toggle_loop(self):
        self.loop_current = not self.loop_current
        self.loop_toggle.setText("🔁 Loop ON" if self.loop_current else "🔁 Loop OFF")
        self.loop_toggle.setStyleSheet(
            "font-size: 12px; padding: 2px; background-color: #3CB371; color: white;"
            if self.loop_current
            else "font-size: 12px; padding: 2px;"
        )

    def pause_after_loop_jump(self):
        """Pause the video after loop logic has jumped back to start of subtitle"""
        if self.is_playing:
            self.player.pause()
            self.is_playing = False
            self.play_pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())

    # -------------------
     # THEME: Dark (always on)
     # -------------------
    def apply_theme(self):
        """Apply dark mode palette globally (no toggle)."""
        app = QApplication.instance()
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(30, 30, 30))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.white)
        app.setPalette(palette)
        self.video_frame.setStyleSheet("background-color: black;")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ShadowingApp()
    win.show()
    app.installEventFilter(win)
    sys.exit(app.exec_())
