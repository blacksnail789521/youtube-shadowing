import os
import whisper
import yt_dlp
import re
import sys


# 📁 Determine where the executable and bundled files are located
if getattr(sys, "frozen", False):
    exe_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    exe_dir = os.path.dirname(__file__)

# 🎞️ Add ffmpeg and VLC DLLs to PATH (for yt-dlp and libvlc)
os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")

# 🔊 Set VLC plugin path (needed for audio/video playback)
vlc_plugin_path = os.path.join(exe_dir, "plugins")
if os.path.exists(vlc_plugin_path):
    os.environ["VLC_PLUGIN_PATH"] = vlc_plugin_path

# 🧠 Tell Whisper where to find bundled model assets (mel_filters.npz, etc.)
os.environ["WHISPER_ASSETS_DIR"] = os.path.join(exe_dir, "whisper", "assets")


# === Settings ===
MODEL_SIZE = "turbo"
VIDEO_FORMAT = "mp4"
AUDIO_FORMAT = "m4a"


# === Real-time logger ===
class StreamLogger:
    def __init__(self, write_callback):
        self.write_callback = write_callback

    def write(self, text):
        if text.strip():
            self.write_callback(text.strip())

    def flush(self):
        pass  # Required for compatibility


# === Main Transcription Function ===
def run_transcription(youtube_url, output_folder, log_callback=print):
    def log(msg):
        if log_callback:
            log_callback(msg)

    # Step 1: Get video info and folder
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

    # Step 3: Download audio
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

    # Step 4: Transcribe with Whisper (live output)
    log("🧠 Loading Whisper model (turbo)...")
    model = whisper.load_model(MODEL_SIZE)

    log("📄 Transcribing audio (verbose=True)...")
    original_stdout = sys.stdout
    sys.stdout = StreamLogger(log)
    try:
        result = model.transcribe(audio_file, word_timestamps=True, verbose=True)
    finally:
        sys.stdout = original_stdout

    # Step 5: Build subtitles
    def extract_word_timestamps(whisper_result):
        word_dict = {}
        for segment in whisper_result["segments"]:
            for word in segment.get("words", []):
                start = round(word["start"], 3)
                end = round(word["end"], 3)
                word_text = word["word"].strip()
                word_dict[(start, end)] = word_text
        return word_dict

    def create_subtitles_from_words(word_dict):
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
        return subtitles

    def save_subtitles_as_srt(subtitles, output_path):
        def format_timestamp(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{h:02}:{m:02}:{s:02},{ms:03}"

        with open(output_path, "w", encoding="utf-8") as f:
            for idx, sub in enumerate(subtitles, 1):
                f.write(
                    f"{idx}\n{format_timestamp(sub['start'])} --> {format_timestamp(sub['end'])}\n{sub['text']}\n\n"
                )

    word_dict = extract_word_timestamps(result)
    subtitles = create_subtitles_from_words(word_dict)

    srt_path = os.path.join(folder_path, "subtitle.srt")
    save_subtitles_as_srt(subtitles, srt_path)
    os.remove(audio_file)
    log("✅ Subtitles saved.")

    return folder_path


if __name__ == "__main__":
    import argparse

    def print_line(text):
        sys.__stdout__.write(text + "\n")
        sys.__stdout__.flush()

    parser = argparse.ArgumentParser(
        description="Download + Transcribe a YouTube video."
    )
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument("--output", default="youtube_videos", help="Output folder")

    args = parser.parse_args()

    run_transcription(args.url, args.output, log_callback=print_line)
