# steam-appdb

Open database of **all** Steam app ids — games, dedicated-server tools, DLC,
software, music, videos, hardware — split into per-type JSON files. Built
over anonymous PICS (the keyless Steam-client protocol steamcmd itself uses):
no account, no API key.

## Why

Valve removed the keyless `ISteamApps/GetAppList`, and its key-gated
replacement exposes no tools — so dedicated servers can't be listed through
the Web API at all. Anonymous PICS still sees everything; this repo keeps
that data public and fresh.

## How it updates

`.github/workflows/update.yml` runs the pipeline **every 2 hours** (cron
`17 */2 * * *`, plus manual `workflow_dispatch`):

1. checkout + `pip install -r requirements.txt`;
2. `python dump_applist.py --data-dir data` — anonymous PICS login, asks the
   changes feed what changed since the changenumber stored in
   `data/meta.json`, re-probes only those app ids (seconds per run), rewrites
   the files in `data/`;
3. commits and pushes `data/` if anything changed.

New releases emit PICS changes, so they arrive automatically; the id-space
scan below is only ever needed once. Manual run with a `scan_to` input does a
backfill scan instead.

## Repository layout

```
dump_applist.py               the dumper (anonymous PICS, incremental)
verify.py                     sanity checks for a bootstrapped data/
requirements.txt
.github/workflows/update.yml  the update pipeline (every 7 hours)
data/                         generated:
  all.json                    [{appid, name, type}] — master list
  game.json, tool.json, dlc.json, application.json,
  music.json, video.json, demo.json, hardware.json, ...
                              [{appid, name}] per product type
  applist.json                {"applist":{"apps":[...]}} — the
                              steamcmd-installable subset (game+tool+application)
  meta.json                   PICS changenumber + scanned ceiling
```

## Setup

1. Create a public repo and copy this directory's contents into it.
2. Bootstrap the database once (locally is fine, takes roughly 30–60
   minutes — batches of 5000 ids per PICS call, checkpoint every 100k):

   ```sh
   pip install -r requirements.txt
   python dump_applist.py --scan-to auto
   git add data/ && git commit -m "bootstrap" && git push
   ```

   `auto` scans the id space from the beginning and stops by itself once
   200k consecutive ids turn out empty — i.e. past the end of Steam's
   assigned id range (an explicit integer ceiling is also accepted). If the
   run dies, re-run the same command: it resumes from the last checkpoint.
   Ids created later arrive through the changes feed, so the scan is never
   repeated.

   Check the result before pushing: `python verify.py` (file consistency +
   spot checks of well-known apps and dedicated servers) — expect `ALL GOOD`.

3. Done — the pipeline keeps `data/` fresh from here on.

## Wiring into gamefetcher

Point at the installable subset (single URL, classic GetAppList shape), or at
individual per-type files — both shapes are parsed:

```yaml
# gamefetcher.yaml
app_list_urls:
  - https://raw.githubusercontent.com/<owner>/steam-appdb/master/data/applist.json
```

Once this repo is live it can replace all of gamefetcher's default sources.
