# PyInstaller spec: SAR Manifest ground station, ONE-FOLDER build (spec 4.5).
# One-file mode is avoided on purpose: QtWebEngine's resources unpack slowly on every start.
# The detection pipeline (torch/CUDA) is a separate program and is excluded here.
#
#   .venv\Scripts\python -m pip install pyinstaller
#   .venv\Scripts\pyinstaller packaging\ground_station.spec --noconfirm
#   -> dist\SARManifest\SARManifest.exe

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
GS = ROOT / "ground_station"

datas = [
    (str(GS / "db" / "schema.sql"), "ground_station/db"),
    (str(GS / "db" / "migrations"), "ground_station/db/migrations"),
    (str(GS / "ui" / "web"), "ground_station/ui/web"),
]

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + collect_submodules("zeroconf")
    + ["pymavlink.dialects.v20.ardupilotmega", "pymavlink.dialects.v20.common", "pynmea2"]
)

excludes = ["torch", "torchvision", "ultralytics", "matplotlib", "scipy", "pandas", "polars", "IPython",
            "tkinter", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
            "PySide6.QtMultimedia", "PySide6.QtQuick3D"]

a = Analysis(
    [str(ROOT / "packaging" / "launch_ground_station.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SARManifest",
    console=False,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SARManifest")
