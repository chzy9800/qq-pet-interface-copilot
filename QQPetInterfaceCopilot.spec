# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

frida_tools_data = collect_data_files("frida_tools", includes=["bridges/java.js"])

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[("hooks/qqpet_mobile_read_agent.js", "hooks")] + frida_tools_data,
    hiddenimports=[
        "encodings.idna",
        "main",
        "frida",
        "frida_tools",
        "onepush",
        "onepush.providers.bark",
        "onepush.providers.pushplus",
        "onepush.providers.serverchanturbo",
        "onepush.providers.smtp",
        "onepush.providers.custom",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="QQ宠物助手",
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
