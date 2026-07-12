#!/usr/bin/env python3
"""Maintain a full Steam app-id database — every product type, split into
per-type files (game, tool, dlc, application, music, video, demo, hardware,
...), plus a combined applist.json with the steamcmd-installable subset
(game + tool + application) in the classic GetAppList shape gamefetcher
consumes.

Uses anonymous PICS — the same keyless Steam-client protocol steamcmd uses;
no account, no Web API key. This exists because Valve removed the keyless
ISteamApps/GetAppList and its replacement (IStoreService, key-gated) exposes
no tools, so no public database carries dedicated servers released after
mid-2023.

Files under --data-dir:
    all.json      [{appid, name, type}]      — master, source of truth
    <type>.json   [{appid, name}]            — one per product type
    applist.json  {"applist":{"apps":[...]}} — game+tool+application combined
    meta.json     {changenumber, scanned_to} — incremental-update state

Bootstrap (one-time, takes hours — scans the whole id space):
    python dump_applist.py --scan-to 5200000
Daily incremental (fast — only apps changed since the last run):
    python dump_applist.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

from steam.client import SteamClient
from steam.enums import EResult

# The subset steamcmd can install; what gamefetcher's combined file carries.
# Dedicated servers appear as both "game" (Rust DS) and "tool" (Palworld DS).
# Every other type still gets its own per-type file.
INSTALLABLE_TYPES = {"game", "tool", "application"}
# 5000 ids per PICS call measured at ~1s (vs 0.7s for 500) — bigger batches
# are nearly free, the pause dominates politeness.
BATCH = 5000
BATCH_PAUSE = 0.5


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)


def probe(client: SteamClient, ids: list[int], apps: dict[int, dict]) -> int:
    """Fetch product info for ids in batches; record every named app with
    its product type. Returns how many apps the probed range contained."""
    found = 0
    for i in range(0, len(ids), BATCH):
        chunk = ids[i : i + BATCH]
        info = client.get_product_info(apps=chunk) or {}
        for appid, data in (info.get("apps") or {}).items():
            common = data.get("common") or {}
            name = common.get("name")
            if not name:
                continue
            app_type = (common.get("type") or "unknown").lower()
            apps[int(appid)] = {"name": name, "type": app_type}
            found += 1
        done = min(i + BATCH, len(ids))
        if done % 100000 < BATCH or done == len(ids):
            print(f"  probed {done}/{len(ids)} ids, kept {len(apps)} apps", file=sys.stderr)
        time.sleep(BATCH_PAUSE)
    return found


def write_outputs(data_dir: Path, apps: dict[int, dict]) -> None:
    ordered = sorted(apps.items())
    save_json(data_dir / "all.json",
              [{"appid": a, "name": v["name"], "type": v["type"]} for a, v in ordered])

    by_type: dict[str, list] = {}
    for appid, v in ordered:
        by_type.setdefault(v["type"], []).append({"appid": appid, "name": v["name"]})
    for app_type, entries in sorted(by_type.items()):
        save_json(data_dir / f"{app_type}.json", entries)

    installable = [{"appid": a, "name": v["name"]}
                   for a, v in ordered if v["type"] in INSTALLABLE_TYPES]
    save_json(data_dir / "applist.json", {"applist": {"apps": installable}})

    counts = ", ".join(f"{t}:{len(v)}" for t, v in sorted(by_type.items()))
    print(f"wrote {len(apps)} apps ({counts}); installable: {len(installable)}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--scan-to", default="0",
                    help="bootstrap/backfill scan of the id space: 'auto' to scan until the "
                         "ids run out (stops after 200k consecutive empty ids), or an explicit "
                         "integer ceiling")
    ap.add_argument("--scan-from", type=int, default=0,
                    help="override scan start (default: continue after the stored ceiling)")
    args = ap.parse_args()

    apps: dict[int, dict] = {
        entry["appid"]: {"name": entry["name"], "type": entry.get("type", "unknown")}
        for entry in load_json(args.data_dir / "all.json", [])
    }
    meta = load_json(args.data_dir / "meta.json", {"changenumber": 0, "scanned_to": 0})

    client = SteamClient()
    for attempt in range(1, 6):
        if client.anonymous_login() == EResult.OK:
            break
        print(f"anonymous login failed (attempt {attempt}), retrying", file=sys.stderr)
        time.sleep(5 * attempt)
    else:
        print("anonymous login failed after 5 attempts", file=sys.stderr)
        return 1

    try:
        # Incremental pass: everything that changed since the last run.
        # App creation also emits a PICS change, so new releases are covered
        # once a baseline changenumber exists.
        if meta["changenumber"]:
            changes = client.get_changes_since(
                meta["changenumber"], app_changes=True, package_changes=False)
            changed = [c.appid for c in (changes.app_changes or [])]
            if getattr(changes, "force_full_app_update", False):
                print("warning: changenumber too old, PICS demands a full "
                      "update — re-run with --scan-to to rebuild", file=sys.stderr)
            print(f"incremental: {len(changed)} changed apps since "
                  f"{meta['changenumber']}", file=sys.stderr)
            probe(client, changed, apps)
            meta["changenumber"] = changes.current_change_number
        else:
            # First run: record the baseline before scanning so nothing
            # slips between the scan and the next incremental run.
            meta["changenumber"] = client.get_changes_since(
                1, app_changes=True, package_changes=False).current_change_number
            print(f"baseline changenumber: {meta['changenumber']}", file=sys.stderr)

        # Bootstrap / backfill scan of the raw id space. Progress is
        # checkpointed every CHECKPOINT ids: if the run dies, re-running with
        # the same arguments resumes right after the last checkpoint.
        # 'auto' keeps scanning until two consecutive checkpoints (200k ids)
        # contain nothing — i.e. past the end of the assigned id space (a
        # hard 100M backstop guards against the heuristic ever misfiring).
        CHECKPOINT = 100_000
        auto = args.scan_to == "auto"
        ceiling = 100_000_000 if auto else int(args.scan_to)
        if ceiling:
            start = args.scan_from or meta["scanned_to"] + 1
            print(f"scanning ids from {start}"
                  + (" until they run out" if auto else f" to {ceiling}"), file=sys.stderr)
            empty_streak = 0
            for chunk_start in range(start, ceiling + 1, CHECKPOINT):
                chunk_end = min(chunk_start + CHECKPOINT - 1, ceiling)
                found = probe(client, list(range(chunk_start, chunk_end + 1)), apps)
                meta["scanned_to"] = max(meta["scanned_to"], chunk_end)
                write_outputs(args.data_dir, apps)
                save_json(args.data_dir / "meta.json", meta)
                print(f"checkpoint: scanned to {chunk_end} ({found} apps in this chunk)",
                      file=sys.stderr)
                if auto:
                    empty_streak = empty_streak + 1 if found == 0 else 0
                    if empty_streak >= 2:
                        print("id space exhausted, stopping", file=sys.stderr)
                        break
    finally:
        client.logout()

    write_outputs(args.data_dir, apps)
    save_json(args.data_dir / "meta.json", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
