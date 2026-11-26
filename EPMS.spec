# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import collect_submodules, get_module_file_attribute
from pathlib import Path

datas = [('dist', 'dist'), ('epms/submission_2027.json', 'epms'),
         ('epms/migrations/env.py', 'epms/migrations'),
         ('build/template/2027-공식양식.xlsx', 'samples')]
datas += [(str(file), 'epms/migrations/versions') for file in sorted(Path('epms/migrations/versions').glob('*.py'))]
binaries = []
hiddenimports = []
datas += collect_data_files('tzdata')
datas += collect_data_files('reportlab')
hiddenimports += collect_submodules('reportlab.pdfbase')
datas += [('epms/windows_ocr.ps1', 'epms'), ('epms/ocr_unipass.json', 'epms')]
tmp_ret = collect_all('pypdfium2_raw')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += collect_submodules('uvicorn')
get_module_file_attribute('webview')
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='공동정산 수출실적 관리',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='공동정산 수출실적 관리',
)
