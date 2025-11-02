import sys

if getattr(sys, "frozen", False):
    import tqdm.std

    def noop(*args, **kwargs):
        pass

    tqdm.std.tqdm.__init__ = noop
    tqdm.std.tqdm.__enter__ = lambda self: self
    tqdm.std.tqdm.__exit__ = noop
    tqdm.std.tqdm.update = noop
    tqdm.std.tqdm.close = noop

import os
import re
import yt_dlp
import traceback
import whisper


# === Handle PyInstaller Frozen Mode ===
if getattr(sys, "frozen", False):
    exe_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    exe_dir = os.path.dirname(__file__)

# 🛠️ Add VLC and ffmpeg to PATH
os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")

# 🎛️ Set VLC plugin path
vlc_plugin_path = os.path.join(exe_dir, "plugins")
if os.path.exists(vlc_plugin_path):
    os.environ["VLC_PLUGIN_PATH"] = vlc_plugin_path

# 🧠 Whisper asset path (e.g. mel_filters.npz)
os.environ["WHISPER_ASSETS_DIR"] = os.path.join(exe_dir, "whisper", "assets")

VIDEO_FORMAT = "mp4"
AUDIO_FORMAT = "m4a"


# === Real-time logger ===
class StreamLogger:
    def __init__(self, write_callback=None, total_duration=0):
        self.write_callback = write_callback or (
            lambda x: sys.__stdout__.write(x + "\n")
        )
        self.total_duration = total_duration

    def _format_time(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02}:{m:02}:{s:02}"

    def write(self, text):
        if not text.strip():
            return
        text = text.strip()
        if self.total_duration and text.startswith("[") and "-->" in text:
            match = re.search(r"-->\s*(\d+:\d+\.\d+|\d+:\d+:\d+\.\d+)", text)
            if match:
                ts = match.group(1)
                ts_parts = ts.split(":")
                if len(ts_parts) == 3:
                    h, m, s = ts_parts
                    end_sec = int(h) * 3600 + int(m) * 60 + float(s)
                elif len(ts_parts) == 2:
                    m, s = ts_parts
                    end_sec = int(m) * 60 + float(s)
                else:
                    end_sec = float(ts_parts[0])
                progress = min(end_sec, self.total_duration)
                percent = progress / self.total_duration * 100
                progress_msg = (
                    f"⏳ {self._format_time(progress)} / {self._format_time(self.total_duration)} "
                    f"({percent:.1f}%)"
                )
                try:
                    self.write_callback(progress_msg)
                except Exception:
                    sys.__stdout__.write("❌ Logging failed.\n")
        try:
            self.write_callback(text)
        except Exception:
            sys.__stdout__.write("❌ Logging failed.\n")

    def flush(self):
        pass


# === Subtitle-splitting logic ===
def split_subtitles(word_dict, max_words=15):
    """Split recognized words into subtitle segments.

    Logic:
      - Always split at strong punctuation (. ? !)
      - Optionally split at soft punctuation (, ; :) if sentence is long enough
      - Never split by word count alone (no punctuation → no split)
    """
    subtitles = []
    current_sentence = ""
    sentence_start = None
    last_end = None

    for (start, end), word in sorted(word_dict.items()):
        if sentence_start is None:
            sentence_start = start
        current_sentence += " " + word
        last_end = end

        # === punctuation rules ===
        strong_punct = r"[.?!]$"
        soft_punct = r"[,;:]$"
        words_count = len(current_sentence.split())
        end_sentence = False

        # Strong punctuation → always end
        if re.search(strong_punct, word) and words_count >= 3:
            end_sentence = True
        # Soft punctuation → end only if sentence already long enough
        elif re.search(soft_punct, word) and words_count >= max_words:
            end_sentence = True

        if end_sentence:
            subtitles.append(
                {
                    "start": sentence_start,
                    "end": last_end,
                    "text": current_sentence.strip(),
                }
            )
            current_sentence = ""
            sentence_start = None

    # Add final leftover
    if current_sentence:
        subtitles.append(
            {
                "start": sentence_start,
                "end": last_end,
                "text": current_sentence.strip(),
            }
        )

    return subtitles



# === Main Function ===
def run_transcription(youtube_url, model_size, output_folder, log_callback=print, max_words=15):
    def log(msg):
        if log_callback:
            log_callback(msg)

    # Step 1: Get video info & output path
    info = yt_dlp.YoutubeDL({"quiet": True}).extract_info(youtube_url, download=False)
    title_safe = re.sub(r"[\\/*?\"<>|:]", "_", info["title"])
    folder_path = os.path.join(output_folder, title_safe)
    os.makedirs(folder_path, exist_ok=True)
    total_duration = info.get("duration") or 0
    log("⏱️ Video length: " + StreamLogger()._format_time(total_duration))

    # Step 2: Download video
    video_path = os.path.join(folder_path, f"video.{VIDEO_FORMAT}")
    log("📥 Downloading video...")
    ydl_opts = {
        "format": "bv*+ba/best",
        "outtmpl": video_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "prefer_ffmpeg": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
    log("✅ Video downloaded.")

    # Step 3: Extract audio
    audio_path_template = os.path.join(folder_path, "audio.%(ext)s")
    log("🔊 Extracting audio...")
    ydl_opts_audio = {
        "format": "bestaudio/best",
        "outtmpl": audio_path_template,
        "quiet": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": AUDIO_FORMAT,
                "preferredquality": "128",
            }
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
        ydl.download([youtube_url])
    audio_file = audio_path_template.replace("%(ext)s", AUDIO_FORMAT)
    log("✅ Audio extracted.")

    # Step 4: Transcribe with Whisper
    if sys.stdout is None:
        sys.stdout = sys.__stdout__
    if sys.stderr is None:
        sys.stderr = sys.__stderr__
    try:
        log(f"🧠 Loading Whisper model ({model_size})...")
        model = whisper.load_model(model_size)
    except Exception as e:
        log("❌ Failed to load Whisper model:")
        log(str(e))
        log(traceback.format_exc())
        return

    log("📄 Transcribing audio (verbose=True)...")
    original_stdout = sys.stdout
    sys.stdout = StreamLogger(log_callback, total_duration)
    try:
        result = model.transcribe(audio_file, word_timestamps=True, verbose=True)
    finally:
        sys.stdout = original_stdout

    # Step 5: Generate subtitles
    word_dict = {}
    for segment in result["segments"]:
        for word in segment.get("words", []):
            start = round(word["start"], 3)
            end = round(word["end"], 3)
            word_text = word["word"].strip()
            word_dict[(start, end)] = word_text

    # Use the standalone splitter
    subtitles = split_subtitles(word_dict, max_words)

    def format_timestamp(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    srt_path = os.path.join(folder_path, "subtitle.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, sub in enumerate(subtitles, 1):
            f.write(
                f"{idx}\n{format_timestamp(sub['start'])} --> {format_timestamp(sub['end'])}\n{sub['text']}\n\n"
            )

    os.remove(audio_file)
    log("✅ Subtitles saved.")
    return folder_path


# === CLI support ===
if __name__ == "__main__":
    import argparse

    def print_line(text):
        sys.__stdout__.write(text + "\n")
        sys.__stdout__.flush()

    parser = argparse.ArgumentParser(description="Download + Transcribe a YouTube video.")
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument(
        "--model_size",
        help="Whisper model (tiny, base, small, medium, large, turbo)",
        default="turbo",
    )
    parser.add_argument("--output_folder", default="youtube_videos", help="Output folder")

    args = parser.parse_args()
    run_transcription(args.url, args.model_size, args.output_folder, log_callback=print_line)
