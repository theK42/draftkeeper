"""Check with real LibreOffice that a normalized snapshot opens with identical text.

Usage: python3 tests/roundtrip_check.py path/to/some.odt
(Close LibreOffice first; headless soffice can't run alongside an open instance.)
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "extension", "pythonpath"))
from draftkeeper.normalize import normalize  # noqa: E402

SOFFICE = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def convert(src, fmt, outdir):
    subprocess.run([SOFFICE, "--headless", "--convert-to", fmt, "--outdir", outdir, src],
                   check=True, capture_output=True)
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(outdir, base + "." + fmt.split(":")[0])


def main(src):
    work = tempfile.mkdtemp(prefix="dk-roundtrip-")
    a_dir, b_dir = os.path.join(work, "a"), os.path.join(work, "b")
    os.makedirs(a_dir)
    os.makedirs(b_dir)
    original_txt = open(convert(src, "txt:Text", a_dir), "rb").read()
    fodt = convert(src, "fodt:OpenDocument Text Flat XML", a_dir)
    raw = open(fodt, "rb").read()
    norm = normalize(raw)
    norm_path = os.path.join(b_dir, os.path.basename(fodt))
    open(norm_path, "wb").write(norm)
    roundtrip_txt = open(convert(norm_path, "txt:Text", b_dir), "rb").read()
    print("fodt %d KB -> normalized %d KB, %d lines" % (len(raw) / 1024, len(norm) / 1024, norm.count(b"\n")))
    if original_txt == roundtrip_txt:
        print("OK: text identical after normalize (%d chars)" % len(original_txt))
        shutil.rmtree(work)
    else:
        print("MISMATCH: compare files in", work)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1])
