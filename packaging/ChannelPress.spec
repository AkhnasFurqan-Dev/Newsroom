import os
from pathlib import Path

from PyInstaller.building.build_main import Analysis
from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
BROWSER_ROOT = Path(os.environ.get("CHANNEL_PRESS_PLAYWRIGHT_BROWSERS", ""))
if not BROWSER_ROOT.is_dir():
    raise SystemExit(
        "A matching Playwright Chromium cache is required. "
        "Run scripts/build-windows.ps1 to prepare the renderer."
    )

DATAS = [
    (str(ROOT / "alembic"), "alembic"),
    (str(ROOT / "alembic.ini"), "."),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "fixtures"), "fixtures"),
    (str(ROOT / "channel_press" / "web" / "templates"), "channel_press/web/templates"),
    (str(ROOT / "channel_press" / "web" / "static"), "channel_press/web/static"),
    (str(ROOT / "channel_press" / "layout" / "templates"), "channel_press/layout/templates"),
    (str(ROOT / "channel_press" / "publishing" / "templates"), "channel_press/publishing/templates"),
    (str(ROOT / "channel_press" / "ai" / "prompts"), "channel_press/ai/prompts"),
    (str(ROOT / "channel_press" / "ai" / "model_manifest.json"), "channel_press/ai"),
]
DATAS += [
    (source, os.path.dirname(destination))
    for destination, source, _typecode in Tree(
        str(BROWSER_ROOT),
        prefix="pw-browsers",
        excludes=[".links", "__dirlock"],
    )
]
WEBVIEW_DATA, WEBVIEW_BINARIES, WEBVIEW_HIDDEN = collect_all("webview")
DATAS += WEBVIEW_DATA
HIDDEN = WEBVIEW_HIDDEN + [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
]

desktop_analysis = Analysis(
    [str(ROOT / "channel_press" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=WEBVIEW_BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide6"],
    noarchive=False,
)
desktop_pyz = PYZ(desktop_analysis.pure)
desktop_exe = EXE(
    desktop_pyz,
    desktop_analysis.scripts,
    [],
    exclude_binaries=True,
    name="ChannelPress",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)

worker_analysis = Analysis(
    [str(ROOT / "channel_press" / "worker_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide6", "webview"],
    noarchive=False,
)
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz,
    worker_analysis.scripts,
    [],
    exclude_binaries=True,
    name="ChannelPressWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)

COLLECT(
    desktop_exe,
    worker_exe,
    desktop_analysis.binaries,
    desktop_analysis.datas,
    worker_analysis.binaries,
    worker_analysis.datas,
    name="ChannelPress",
    strip=False,
    upx=False,
    upx_exclude=[],
    contents_directory="_internal",
)
