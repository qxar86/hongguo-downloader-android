# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: hongguo downloader v2026-10-25.6, single file + console
# (interactive mode needs console input, so console=True is required)
# Build on Windows: double-click build_exe.bat, or:
#   pyinstaller --clean --noconfirm hongguo_downloader.spec
# Output: dist\hongguo_downloader.exe
# ffmpeg is NOT bundled into the exe: put ffmpeg.exe in the SAME folder
# as the exe and it will be used first (see _app_dir() in the .py).
# requests / pycryptodome are collected automatically; pure-Python AES
# fallback in the .py means the exe also works without pycryptodome.

a = Analysis(
    ['hongguo_downloader_v2026-10-25.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='hongguo_downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
