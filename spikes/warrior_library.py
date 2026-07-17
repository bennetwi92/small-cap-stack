"""Warrior Trading transcript library — backfill + incremental collector.

Pulls English auto-captions for Ross Cameron / Warrior Trading videos into a
gitignored library under ``data/warrior-library/``:

    data/warrior-library/
      raw/         <upload_date>--<id>.en*.json3   + <...>.info.json   (yt-dlp output)
      transcripts/ <upload_date>--<id>.txt          (clean timestamped text)
      index.json   {video_id: {date, title, url, transcript, words}}

The same code path serves the one-off 6-month backfill and the daily
"grab whatever is new" job — videos already present in ``index.json`` are
skipped, so re-running is cheap and idempotent.

YouTube now requires a JS runtime to hand over caption URLs; we point yt-dlp at
the local ``node`` (``--js-runtimes node``). Captions only — no video/audio
download, so this is light enough to run on the box.

Usage:
    python spikes/warrior_library.py --since 20260113        # backfill window
    python spikes/warrior_library.py --months 6              # rolling 6-month window
    python spikes/warrior_library.py --since 20260710 --limit 5   # smoke test

This is a spike (exempt from mypy/tests); it is still ruff-linted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

CHANNEL_ID = "UCBayuhgYpKNbhJxfExYkPfA"  # Ross Cameron - Warrior Trading
CHANNEL_VIDEOS_URL = f"https://www.youtube.com/channel/{CHANNEL_ID}/videos"

LIB = Path("data/warrior-library")
RAW = LIB / "raw"
TRANSCRIPTS = LIB / "transcripts"
INDEX = LIB / "index.json"


def _yt_dlp() -> str:
    """Resolve the yt-dlp entrypoint (venv shim, else PATH)."""
    venv = Path(sys.executable).parent / "yt-dlp"
    return str(venv) if venv.exists() else "yt-dlp"


def enumerate_window(since: str) -> list[tuple[str, str, str]]:
    """Return [(video_id, upload_date, title)] for uploads on/after ``since``.

    Flat enumeration is one cheap request for the whole channel; the
    ``approximate_date`` extractor arg is what makes upload dates available in
    flat mode (``--dateafter`` does NOT filter flat playlists, so we filter here).
    """
    cmd = [
        _yt_dlp(),
        "--js-runtimes",
        "node",
        "--flat-playlist",
        "--extractor-args",
        "youtubetab:approximate_date",
        "--print",
        "%(id)s\t%(upload_date)s\t%(title)s",
        CHANNEL_VIDEOS_URL,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    rows: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        # yt-dlp emits the \t in the template literally; handle both forms.
        parts = line.split("\t") if "\t" in line else line.split("\\t")
        if len(parts) < 3:
            continue
        vid, date, title = parts[0], parts[1], "\t".join(parts[2:])
        if date and date != "NA" and date >= since:
            rows.append((vid, date, title))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def fetch_captions(video_ids: list[str]) -> None:
    """Download English auto-captions + info.json for the given ids into RAW.

    ``--no-overwrites`` makes this resumable; ``--ignore-errors`` skips videos
    with no captions (live streams, very fresh uploads) without aborting the run.
    """
    if not video_ids:
        return
    urls = [f"https://www.youtube.com/watch?v={v}" for v in video_ids]
    cmd = [
        _yt_dlp(),
        "--js-runtimes",
        "node",
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-langs",
        "en.*",
        "--sub-format",
        "json3",
        "--write-info-json",
        "--no-overwrites",
        "--ignore-errors",
        "--sleep-requests",
        "1",
        "--sleep-subtitles",
        "1",
        "-o",
        str(RAW / "%(upload_date)s--%(id)s.%(ext)s"),
        *urls,
    ]
    subprocess.run(cmd, check=False)


def _fmt_ts(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def json3_to_transcript(path: Path) -> str:
    """Collapse a yt-dlp json3 caption file into clean timestamped sentences."""
    data = json.loads(path.read_text())
    events = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if text:
            events.append((ev.get("tStartMs", 0), text))

    out: list[str] = []
    buf: list[str] = []
    stamp = 0
    for start, text in events:
        if not buf:
            stamp = start
        buf.append(text)
        joined = " ".join(buf)
        if re.search(r"[.!?]$", text) or len(joined) > 220:
            out.append(f"[{_fmt_ts(stamp)}] {joined}")
            buf = []
    if buf:
        out.append(f"[{_fmt_ts(stamp)}] " + " ".join(buf))
    return "\n".join(out)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60]


def parse_and_index(window: list[tuple[str, str, str]]) -> dict:
    """Parse every raw json3 into a transcript and (re)build index.json."""
    index: dict = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    titles = {vid: title for vid, _date, title in window}

    for j3 in sorted(RAW.glob("*.json3")):
        # filename: <date>--<id>.en[-orig].json3  -> prefer en-orig, else en
        stem = j3.name
        m = re.match(r"(\d{8})--([\w-]+)\.(en[\w-]*)\.json3$", stem)
        if not m:
            continue
        date, vid, lang = m.group(1), m.group(2), m.group(3)
        # one transcript per video; prefer the original-language track
        if vid in index and index[vid].get("lang") == "en-orig":
            continue
        if vid in index and lang != "en-orig":
            continue

        transcript = json3_to_transcript(j3)
        if not transcript:
            continue
        title = titles.get(vid, "")
        out_name = f"{date}--{vid}.txt"
        (TRANSCRIPTS / out_name).write_text(
            f"# {title}\n# https://www.youtube.com/watch?v={vid}  ({date})\n\n{transcript}\n"
        )
        index[vid] = {
            "date": date,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "transcript": f"transcripts/{out_name}",
            "lang": lang,
            "words": len(transcript.split()),
        }

    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True))
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--since", help="cutoff upload date YYYYMMDD")
    g.add_argument("--months", type=int, help="rolling window: last N months")
    ap.add_argument("--limit", type=int, default=0, help="cap videos (smoke test)")
    ap.add_argument(
        "--enumerate-only", action="store_true", help="list the window and exit; download nothing"
    )
    args = ap.parse_args()

    if args.months:
        cutoff = dt.date.today() - dt.timedelta(days=30 * args.months)
        since = cutoff.strftime("%Y%m%d")
    else:
        since = args.since or "20260113"

    for d in (RAW, TRANSCRIPTS):
        d.mkdir(parents=True, exist_ok=True)

    window = enumerate_window(since)
    if args.limit:
        window = window[: args.limit]
    print(f"[enumerate] {len(window)} videos on/after {since}")

    if args.enumerate_only:
        for vid, date, title in window:
            print(f"  {date}  {vid}  {title[:70]}")
        return 0

    existing = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    todo = [vid for vid, _d, _t in window if vid not in existing]
    print(f"[fetch] {len(todo)} new (skipping {len(window) - len(todo)} already indexed)")

    fetch_captions(todo)
    index = parse_and_index(window)

    in_window = [v for v in window if v[0] in index]
    print(
        f"[done] library has {len(index)} transcripts; "
        f"{len(in_window)}/{len(window)} of this window captured"
    )
    missing = [(d, t) for vid, d, t in window if vid not in index]
    if missing:
        print(f"[note] {len(missing)} window videos have no English captions:")
        for d, t in missing[:10]:
            print(f"    {d}  {t[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
