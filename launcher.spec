# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata, get_package_paths


a = Analysis(
    ['launcher win.pyw'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/app_icon.ico', 'assets'),
        ('assets/horizon-dusk.png', 'assets'),
        (str(Path(get_package_paths('flet_desktop')[1]) / 'app'), 'flet_desktop/app'),
        *collect_data_files('minecraft_launcher_lib'),
        *copy_metadata('flet-desktop'),
    ],
    hiddenimports=['flet_desktop'],
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
    name='Crackrizon2.0.0',
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
    icon=['assets/app_icon.ico'],
)
