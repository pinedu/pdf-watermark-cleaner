# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

project = r'C:\Users\Administrator\WorkBuddy\2026-09-03-09-55-26\pdf-watermark-cleaner'
datas = [(project + r'\templates', 'templates'), (project + r'\static', 'static')]
binaries = []
hiddenimports = ['webview.platforms.edgechromium', 'clr', 'pythonnet']
for package in ('pymupdf', 'PIL', 'webview'):
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [project + r'\app.py'],
    pathex=[project],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PDFWatermarkCleaner-Release-v2.1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[project + r'\app.ico'],
)
