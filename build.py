import subprocess
import os
import shutil
import sys

# === Config ===
HOOKS_DIR = "hooks"
TOOLS_DIR = "tools"
FFMPEG_PATH = os.path.join(TOOLS_DIR, "ffmpeg.exe")
VLC_DLLS = [
    os.path.join(TOOLS_DIR, "libvlc.dll"),
    os.path.join(TOOLS_DIR, "libvlccore.dll"),
]
VLC_PLUGINS = os.path.join(TOOLS_DIR, "plugins")


def build_exe():
    # Check hooks folder
    if not os.path.exists(HOOKS_DIR):
        print(f"⚠️ Hooks directory '{HOOKS_DIR}' not found. Creating it...")
        os.makedirs(HOOKS_DIR)

    # Check ffmpeg
    if not os.path.exists(FFMPEG_PATH):
        print(
            f"❌ ERROR: '{FFMPEG_PATH}' not found! Please place ffmpeg.exe in the 'tools/' folder."
        )
        sys.exit(1)

    # Check VLC DLLs
    for dll in VLC_DLLS:
        if not os.path.exists(dll):
            print(f"❌ ERROR: Missing VLC file: {dll}")
            sys.exit(1)

    # Check VLC plugins folder
    if not os.path.exists(VLC_PLUGINS):
        print("❌ ERROR: VLC 'plugins' folder is missing in tools/")
        sys.exit(1)

    # PyInstaller command
    app_name = "shadowing"
    cmd = [
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        app_name,
        "--icon",
        os.path.join("tools", "icon.ico"),
        "gui.py",
        "--distpath",
        ".",  # Output here
        "--workpath",
        "build",
        "--additional-hooks-dir",
        HOOKS_DIR,
        "--add-binary",
        f"{FFMPEG_PATH};.",
    ]

    # Add VLC DLLs
    for dll in VLC_DLLS:
        cmd.extend(["--add-binary", f"{dll};."])

    # Add VLC plugins folder
    cmd.extend(["--add-data", f"{VLC_PLUGINS};plugins"])

    # === Run Build ===
    print("🚀 Building standalone executable...")
    result = subprocess.run(cmd)

    # Clean up
    if os.path.exists("build"):
        shutil.rmtree("build")
    if os.path.exists(f"{app_name}.spec"):
        os.remove(f"{app_name}.spec")

    if result.returncode == 0:
        print(f"✅ Build complete. Check the current directory for '{app_name}.exe'.")
    else:
        print("❌ Build failed. See errors above.")


if __name__ == "__main__":
    build_exe()
