# PyInstaller spec for the AutoApply desktop app (macOS .app / Windows folder app).
# Build with packaging/build_macos.sh or packaging/build_windows.ps1 (they stage the
# dashboard, Node.js and Tectonic into packaging/stage/ first).
# Created by Abhiram (Challa Abhiram). MIT license.
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = os.path.abspath(SPECPATH)  # noqa: F821 (provided by PyInstaller)
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
STAGE = os.path.join(HERE, "stage")

datas = [
    (os.path.join(BACKEND, "alembic"), "alembic"),
    (os.path.join(BACKEND, "alembic.ini"), "."),
    (os.path.join(BACKEND, "prompts"), "prompts"),
    (os.path.join(BACKEND, "templates"), "templates"),
    (os.path.join(BACKEND, "portal_configs"), "portal_configs"),
    (os.path.join(STAGE, "dashboard"), "dashboard"),
    (os.path.join(ROOT, "LICENSE"), "."),
]
binaries = []
for name in os.listdir(os.path.join(STAGE, "node")):
    binaries.append((os.path.join(STAGE, "node", name), "node"))
for name in os.listdir(os.path.join(STAGE, "bin")):
    binaries.append((os.path.join(STAGE, "bin", name), "bin"))

hiddenimports = collect_submodules("app") + [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "apscheduler.jobstores.sqlalchemy", "apscheduler.schedulers.background",
    "langgraph.checkpoint.sqlite", "sqlalchemy.dialects.sqlite",
]
for pkg in ("litellm", "tiktoken", "tiktoken_ext", "playwright", "langgraph", "pymupdf",
            "email_validator", "alembic", "apscheduler"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(  # noqa: F821
    [os.path.join(HERE, "entry.py")],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["sentence_transformers", "torch", "tensorflow", "pytest", "mypy", "ruff",
              "IPython", "matplotlib", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
icon = os.path.join(HERE, "icons", "AutoApply.icns" if sys.platform == "darwin" else "AutoApply.ico")
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoApply",
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AutoApply")  # noqa: F821
if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="AutoApply.app",
        icon=icon,
        bundle_identifier="com.abhiram.autoapply",
        info_plist={
            "CFBundleName": "AutoApply",
            "CFBundleDisplayName": "AutoApply",
            "CFBundleShortVersionString": os.environ.get("AUTOAPPLY_VERSION", "1.0.0"),
            "NSHumanReadableCopyright": "Created by Abhiram (Challa Abhiram) · MIT license",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
