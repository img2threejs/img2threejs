#!/usr/bin/env python3
"""Print one line when a newer img2threejs release exists. At most one check per 24h; never fails."""
import http.client
import re
import time
from contextlib import closing
from pathlib import Path


def version_key(text):
    return tuple(int(n) for n in re.match(r"v?(\d+)\.(\d+)\.(\d+)", text).groups()) + ("-" not in text,)


def check(home, root=Path(__file__).resolve().parent.parent):
    root, stamp = root.resolve(), home / ".img2threejs" / "update-check.stamp"
    if stamp.is_file() and time.time() - stamp.stat().st_mtime < 86400:
        return None
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    installed = re.search(r"^version: (\S+)$", (root / "SKILL.md").read_text(encoding="utf-8"), re.M).group(1)
    with closing(http.client.HTTPSConnection("github.com", timeout=3)) as connection:
        connection.request("HEAD", "/img2threejs/img2threejs/releases/latest")
        location = connection.getresponse().getheader("Location", "")
    latest = re.search(r"/releases/tag/(v\d+\.\d+\.\d+)$", location).group(1)
    if version_key(latest) <= version_key(installed):
        return None
    releases = (home / ".img2threejs" / "releases").resolve()
    command = ("npx --yes img2threejs@latest update" if releases in root.parents
               else f"git -C {root} pull --ff-only")
    return f"img2threejs {latest} is available (installed {installed}); after this run: {command}"


if __name__ == "__main__":
    try:
        message = check(Path.home())
    except Exception:
        message = "update check skipped"
    if message:
        print(message)
