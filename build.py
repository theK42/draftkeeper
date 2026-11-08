"""Build dist/Draftkeeper.oxt from extension/.  Usage: python3 build.py"""
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "extension")
OUT = os.path.join(ROOT, "dist", "Draftkeeper.oxt")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.endswith(".dist-info")]
        for name in filenames:
            if name.endswith((".pyc", ".DS_Store")):
                continue
            full = os.path.join(dirpath, name)
            z.write(full, os.path.relpath(full, SRC))
print("built", os.path.relpath(OUT, ROOT), "%.0f KB" % (os.path.getsize(OUT) / 1024))
