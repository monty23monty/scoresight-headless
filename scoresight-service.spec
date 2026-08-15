# PyInstaller specification for the web-service migration target.
from PyInstaller.utils.hooks import collect_submodules


hiddenimports = collect_submodules("uvicorn")
datas = [
    ("src/scoresight/web/templates", "scoresight/web/templates"),
    ("src/scoresight/web/static", "scoresight/web/static"),
    ("tesseract/tessdata", "tesseract/tessdata"),
]

a = Analysis(
    ["src/scoresight/cli.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="scoresight-service",
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="scoresight-service",
)

