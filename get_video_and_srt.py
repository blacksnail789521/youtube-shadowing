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


# === Settings ===
VIDEO_FORMAT = "mp4"
AUDIO_FORMAT = "m4a"


# === Real-time logger ===
class StreamLogger:
    def __init__(self, write_callback=None):
        self.write_callback = write_callback or (
            lambda x: sys.__stdout__.write(x + "\n")
        )

    def write(self, text):
        if text.strip():
            try:
                self.write_callback(text.strip())
            except Exception:
                sys.__stdout__.write("❌ Logging failed.\n")

    def flush(self):
        pass


# === Main Function ===
def run_transcription(youtube_url, model_size, output_folder, log_callback=print):
    def log(msg):
        if log_callback:
            log_callback(msg)

    # Step 1: Get video info & output path
    info = yt_dlp.YoutubeDL({"quiet": True}).extract_info(youtube_url, download=False)
    title_safe = re.sub(r"[\\/*?\"<>|:]", "_", info["title"])
    folder_path = os.path.join(output_folder, title_safe)
    os.makedirs(folder_path, exist_ok=True)

    # Step 2: Download video
    video_path = os.path.join(folder_path, f"video.{VIDEO_FORMAT}")
    log("📥 Downloading video...")
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "outtmpl": video_path,
        "merge_output_format": VIDEO_FORMAT,
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
    # Ensure sys.stdout/stderr are valid to prevent tqdm crash in Whisper
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
    sys.stdout = StreamLogger(log_callback)
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

    subtitles = []
    current_sentence = ""
    sentence_start = None
    last_end = None

    for (start, end), word in sorted(word_dict.items()):
        if sentence_start is None:
            sentence_start = start
        current_sentence += " " + word
        last_end = end
        if re.search(r"[.?!]$", word) and len(current_sentence.split()) >= 3:
            subtitles.append(
                {
                    "start": sentence_start,
                    "end": last_end,
                    "text": current_sentence.strip(),
                }
            )
            current_sentence = ""
            sentence_start = None

    if current_sentence:
        subtitles.append(
            {
                "start": sentence_start,
                "end": last_end,
                "text": current_sentence.strip(),
            }
        )

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
    # Save original YouTube URL for later playback.
    with open(os.path.join(folder_path, "url.txt"), "w", encoding="utf-8") as f:
        f.write(youtube_url)
    return folder_path


# === CLI support ===
if __name__ == "__main__":
    import argparse

    def print_line(text):
        sys.__stdout__.write(text + "\n")
        sys.__stdout__.flush()

    parser = argparse.ArgumentParser(
        description="Download + Transcribe a YouTube video."
    )
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument(
        "--model_size",
        help="Whisper model (tiny, base, small, medium, large, turbo)",
        default="turbo",
    )
    parser.add_argument(
        "--output_folder", default="youtube_videos", help="Output folder"
    )

    args = parser.parse_args()
    run_transcription(
        args.url, args.model_size, args.output_folder, log_callback=print_line
    )
