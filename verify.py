#!/usr/bin/env python3
"""Sanity-check a bootstrapped data/ directory: file consistency plus
spot-checks of well-known app ids (incl. dedicated servers that exist in no
public dump). Run from the repo root: python verify.py"""

import json
import sys
from pathlib import Path

DATA = Path("data")
# (appid, allowed types, name fragment). Valve types dedicated servers
# inconsistently — Rust DS is "game", Valheim/Palworld DS are "tool" — which
# is exactly why the installable subset spans both.
SPOT_CHECKS = [
    (730, {"game"}, "counter-strike"),
    (252490, {"game"}, "rust"),
    (258550, {"game", "tool"}, "rust dedicated server"),
    (896660, {"game", "tool"}, "valheim dedicated server"),
    (4020, {"game", "tool"}, "garry's mod dedicated server"),
    (2394010, {"game", "tool"}, "palworld dedicated server"),
]

def fail(msg):
    print(f"FAIL {msg}")
    return 1

def main() -> int:
    bad = 0
    meta = json.loads((DATA / "meta.json").read_text())
    if meta["changenumber"] <= 0:
        bad += fail("meta.changenumber is not set")
    print(f"ok   meta: changenumber={meta['changenumber']}, scanned_to={meta['scanned_to']:,}")

    allapps = json.loads((DATA / "all.json").read_text())
    byid = {a["appid"]: a for a in allapps}
    if len(byid) != len(allapps):
        bad += fail("duplicate appids in all.json")

    per_type_total = 0
    for f in sorted(DATA.glob("*.json")):
        if f.name in ("all.json", "applist.json", "meta.json"):
            continue
        entries = json.loads(f.read_text())
        per_type_total += len(entries)
        for e in entries[:: max(1, len(entries) // 50)]:  # sample
            master = byid.get(e["appid"])
            if not master or master["type"] != f.stem or master["name"] != e["name"]:
                bad += fail(f"{f.name}: {e['appid']} inconsistent with all.json")
                break
    if per_type_total != len(allapps):
        bad += fail(f"per-type files sum to {per_type_total}, all.json has {len(allapps)}")
    else:
        print(f"ok   {len(allapps):,} apps consistent across all.json and per-type files")

    combined = json.loads((DATA / "applist.json").read_text())["applist"]["apps"]
    installable = {a["appid"] for a in allapps if a["type"] in ("game", "tool", "application")}
    if {a["appid"] for a in combined} != installable:
        bad += fail("applist.json does not equal the game+tool+application subset")
    else:
        print(f"ok   applist.json: {len(combined):,} installable apps, classic shape")

    for appid, want_types, fragment in SPOT_CHECKS:
        a = byid.get(appid)
        if not a:
            bad += fail(f"appid {appid} missing entirely")
        elif a["type"] not in want_types or fragment not in a["name"].lower():
            bad += fail(f"appid {appid}: got {a['type']!r} {a['name']!r}")
        else:
            print(f"ok   {appid}: {a['name']} ({a['type']})")

    print("ALL GOOD" if bad == 0 else f"{bad} problem(s) found")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
