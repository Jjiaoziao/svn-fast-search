# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files

docx_datas = collect_data_files('docx')

a = Analysis(
    ['svn_search.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('bundled_cache', 'bundled_cache'),
        ('app_icon.ico', '.')
    ] + docx_datas,
    hiddenimports=[
        'docx',
        'docx.enum.text',
        'docx.enum.table',
        'docx.shared',
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        'winreg',
        'ctypes',
        'ctypes.wintypes',
        'win32con'
    ],
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
    name='SVN极速搜索工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
