import subprocess
import os
import shutil
import platform

HOOKS_DIR = "hooks"


def build_exe():
    # Ensure hooks directory exists if needed
    if not os.path.exists(HOOKS_DIR):
        os.makedirs(HOOKS_DIR)

    # PyInstaller build command
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "gui.py",
        "--distpath",
        ".",
        "--workpath",
        "build",
        "--additional-hooks-dir",
        HOOKS_DIR,
    ]

    print("🚀 Building standalone executable...")
    subprocess.run(cmd)

    # Clean up temporary build files
    if os.path.exists("build"):
        shutil.rmtree("build")
    if os.path.exists("gui.spec"):
        os.remove("gui.spec")

    print("✅ Build complete. Check the current directory for 'gui.exe'.")


if __name__ == "__main__":
    build_exe()
