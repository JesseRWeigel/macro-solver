#!/usr/bin/env python3
"""Resolve every outbound URL this project cites. Needs the network.

Not part of scripts/verify.sh, because a network failure would turn "could not
check" into a red verify. Run it by hand when the citations change.

One result is expected and is not a defect: the FoodData Central portal page at
/food-details/<id>/nutrients answers HTTP 404 while rendering the correct food,
because the portal is a single-page app whose server does not know that route.
Every food therefore also carries a /portal-data/external/<id> URL, which returns
that food's record and answers 200, and that is the one this script requires.

Exit codes:
  0  every required URL resolved
  1  a required URL did not
  2  the check could not run
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "macro-solver link check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(4096)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception as exc:  # network down, DNS, TLS
        return None, str(exc).encode()


def main() -> int:
    with open(os.path.join(ROOT, "data", "foods.json")) as fh:
        table = json.load(fh)

    required = [("dataset zip", table["dataset"]["url"])]
    for food in table["foods"]:
        required.append((food["id"], food["source"]["url"]))
    advisory = [(food["id"] + " portal page", food["source"]["portal_url"])
                for food in table["foods"]]

    bad = 0
    unreachable = 0
    for label, url in required:
        status, body = fetch(url)
        if status is None:
            print(f"  ????  {label}: could not reach {url} ({body.decode()[:80]})")
            unreachable += 1
            continue
        okish = status == 200
        if okish and label != "dataset zip":
            okish = b'"fdcId"' in body
        print(f"  {'ok  ' if okish else 'FAIL'}  {label}: HTTP {status} {url}")
        if not okish:
            bad += 1

    print()
    print("advisory, not required (single-page-app routes answer 404 while rendering):")
    for label, url in advisory[:3]:
        status, _ = fetch(url)
        print(f"  note  {label}: HTTP {status} {url}")
    print(f"  ...and {len(advisory) - 3} more of the same shape")

    if unreachable:
        print(f"\n{unreachable} URL(s) could not be reached at all, so this run "
              f"proves nothing")
        return 2
    if bad:
        print(f"\n{bad} required URL(s) did not resolve")
        return 1
    print(f"\nall {len(required)} required URLs resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
