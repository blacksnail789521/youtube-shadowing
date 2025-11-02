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

    app_name = "shadowing"

    # Remove previous dist folder for a clean build
    dist_app_folder = os.path.join("dist", app_name)
    if os.path.exists(dist_app_folder):
        print(f"🧹 Removing existing folder: {dist_app_folder}")
        shutil.rmtree(dist_app_folder)

    # Remove previous build folder and spec if exist
    if os.path.exists("build"):
        shutil.rmtree("build")
    if os.path.exists(f"{app_name}.spec"):
        os.remove(f"{app_name}.spec")

    # PyInstaller command: --onedir produces portable folder (not single exe)
    cmd = [
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--onedir",       # <-- changed from --onefile
        "--windowed",
        "--noupx",        # <== prevents UPX compression, keeps icon intact
        "--name",
        app_name,
        "--icon",
        os.path.abspath(os.path.join("tools", "icon.ico")),
        "gui.py",
        "--distpath",
        "dist",           # output folder is dist/
        "--workpath",
        "build",
        "--additional-hooks-dir",
        HOOKS_DIR,
        "--add-binary",
        f"{FFMPEG_PATH};.",   # put ffmpeg.exe into top-level of dist/<app>/
    ]

    # Add VLC DLLs as binaries (placed in top-level of dist/<app>/)
    for dll in VLC_DLLS:
        cmd.extend(["--add-binary", f"{dll};."])

    # Add VLC plugins folder (placed under dist/<app>/plugins)
    cmd.extend(["--add-data", f"{VLC_PLUGINS};plugins"])

    # === Run Build ===
    print("🚀 Building portable --onedir executable (this will create dist/shadowing/)...")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("❌ Build failed. See errors above.")
        # don't attempt to copy tools on failure
        sys.exit(result.returncode)

    # After successful build, ensure tools are present in the dist folder
    app_dist_dir = os.path.join("dist", app_name)
    os.makedirs(app_dist_dir, exist_ok=True)

    # Copy ffmpeg and VLC DLLs if they weren't placed by pyinstaller for some reason
    # (usually --add-binary already did this, but this step ensures completeness)
    try:
        # Ensure ffmpeg.exe present
        dest_ffmpeg = os.path.join(app_dist_dir, "ffmpeg.exe")
        if not os.path.exists(dest_ffmpeg):
            shutil.copy2(FFMPEG_PATH, dest_ffmpeg)
            print(f"Copied ffmpeg to {dest_ffmpeg}")

        # Ensure VLC DLLs present
        for dll in VLC_DLLS:
            dest_dll = os.path.join(app_dist_dir, os.path.basename(dll))
            if not os.path.exists(dest_dll):
                shutil.copy2(dll, dest_dll)
                print(f"Copied {dll} to {dest_dll}")

        # Ensure plugins folder present inside dist/app/plugins
        dest_plugins = os.path.join(app_dist_dir, "plugins")
        if os.path.exists(dest_plugins):
            print("Plugins folder already present in dist.")
        else:
            shutil.copytree(VLC_PLUGINS, dest_plugins)
            print(f"Copied plugins folder to {dest_plugins}")
        
        # Copy icon.ico into dist/<app> for reference
        src_icon = os.path.join(TOOLS_DIR, "icon.ico")
        dest_icon = os.path.join(app_dist_dir, "icon.ico")
        if os.path.exists(src_icon):
            shutil.copy2(src_icon, dest_icon)
            print(f"Copied icon.ico to {dest_icon}")
        else:
            print("⚠️ Warning: icon.ico not found to copy.")
            
    except Exception as e:
        print(f"⚠️ Warning: failed to copy extra tools into dist folder: {e}")

    # Final clean: remove build/ and spec file if you want a cleaner repo
    if os.path.exists("build"):
        shutil.rmtree("build")
    if os.path.exists(f"{app_name}.spec"):
        os.remove(f"{app_name}.spec")

    print(f"✅ Portable build complete. Check the folder: {app_dist_dir}")
    print("You can distribute the entire 'dist/shadowing/' folder. Defender is less likely to flag onedir builds.")

if __name__ == "__main__":
    build_exe()
