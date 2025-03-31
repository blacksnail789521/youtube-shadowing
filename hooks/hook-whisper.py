from PyInstaller.utils.hooks import copy_metadata, collect_data_files

datas = collect_data_files("whisper", includes=["assets/*"])
