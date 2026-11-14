"""Publish a new version that installed copies pick up via Check for Updates.

1. Bump <version> in extension/description.xml and commit your changes.
2. python3 release.py

It builds dist/Draftkeeper.oxt, writes update.xml, commits and tags it,
uploads the .oxt as a GitHub release, and only then pushes main, so
update.xml never points at a download that doesn't exist yet.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = "theK42/draftkeeper"
GH = "gh" if subprocess.run(["which", "gh"], capture_output=True).returncode == 0 else "/opt/homebrew/bin/gh"

UPDATE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<description xmlns="http://openoffice.org/extensions/update/2006"
             xmlns:xlink="http://www.w3.org/1999/xlink">
  <identifier value="com.draftkeeper"/>
  <version value="{version}"/>
  <update-download>
    <src xlink:href="https://github.com/{repo}/releases/download/v{version}/Draftkeeper.oxt"/>
  </update-download>
</description>
"""


def run(*cmd, **kw):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, text=True, **kw)


def main():
    with open(os.path.join(ROOT, "extension", "description.xml"), encoding="utf-8") as f:
        version = re.search(r'<version value="([^"]+)"', f.read()).group(1)
    tag = "v" + version
    if run("git", "status", "--porcelain", capture_output=True).stdout.strip():
        sys.exit("Commit your changes first.")
    if run("git", "tag", "-l", tag, capture_output=True).stdout.strip():
        sys.exit("%s is already released: bump <version> in extension/description.xml." % tag)
    run(sys.executable, "build.py")
    with open(os.path.join(ROOT, "update.xml"), "w", encoding="utf-8") as f:
        f.write(UPDATE_XML.format(version=version, repo=REPO))
    run("git", "add", "update.xml")
    run("git", "commit", "-m", "Release %s" % tag)
    run("git", "tag", tag)
    run("git", "push", "origin", tag)
    run(GH, "release", "create", tag, os.path.join(ROOT, "dist", "Draftkeeper.oxt"),
        "--repo", REPO, "--verify-tag", "--title", "Draftkeeper " + version, "--generate-notes")
    run("git", "push", "origin", "HEAD:main")
    print("Released %s. Installed copies see it under Tools > Extension Manager > Check for Updates." % tag)


if __name__ == "__main__":
    main()
