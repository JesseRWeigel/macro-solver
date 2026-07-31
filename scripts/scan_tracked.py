#!/usr/bin/env python3
"""Scan every git-tracked file for secrets and for absolute home paths.

Read as bytes, not as text, and never through grep. A single NUL byte makes git
and grep classify a file as binary, after which `grep -I` skips it silently and
reports the same "nothing found" as a file it actually read. `grep -P '\\x00'` is
not available in every grep on this machine either, so detection is done in
Python where it is not optional.

Exit codes:
  0  nothing found
  1  something found
  2  the scan could not run, which is not the same as a clean result
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# Case-sensitive on purpose. AWS key ids are uppercase by definition, and a
# case-insensitive version matches ordinary base64 inside embedded images.
PATTERNS = [
    ("aws access key id", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("github token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("openai key", re.compile(rb"sk-[A-Za-z0-9]{32,}")),
    ("openrouter key", re.compile(rb"sk-or-v1-[a-f0-9]{64}")),
    ("google api key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("slack token", re.compile(rb"xox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("private key block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer literal", re.compile(rb"(?:api[_-]?key|secret|password)\s*[=:]\s*['\"][^'\"\s]{16,}['\"]")),
]

HOME_PATH = re.compile(rb"/home/[a-z][a-z0-9_-]*/")


def tracked_files(root):
    out = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"],
        capture_output=True, check=True,
    )
    return [p.decode() for p in out.stdout.split(b"\0") if p]


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        files = tracked_files(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"could not list tracked files: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("no tracked files, so this scan proves nothing", file=sys.stderr)
        return 2

    problems = []
    nul_files = []
    scanned = 0
    for rel in files:
        path = os.path.join(root, rel)
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            print(f"could not read {rel}: {exc}", file=sys.stderr)
            return 2
        scanned += 1
        if b"\0" in blob:
            nul_files.append(rel)
        for label, pattern in PATTERNS:
            for m in pattern.finditer(blob):
                line = blob[: m.start()].count(b"\n") + 1
                problems.append(f"{rel}:{line}: {label}")
        for m in HOME_PATH.finditer(blob):
            line = blob[: m.start()].count(b"\n") + 1
            snippet = blob[m.start(): m.start() + 40].decode("utf-8", "replace")
            problems.append(f"{rel}:{line}: absolute home path {snippet!r}")

    if nul_files:
        # Not a failure by itself, but it has to be visible, because it is the
        # condition under which a grep-based scan would have gone blind.
        print(f"note: {len(nul_files)} tracked file(s) contain a NUL byte and were "
              f"still scanned here: {nul_files}")

    if problems:
        print(f"SCAN FAILED: {len(problems)} finding(s) across {scanned} tracked files")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"scanned {scanned} tracked files as bytes: no credential-shaped strings, "
          f"no absolute home paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
