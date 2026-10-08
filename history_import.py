"""Past sets from the team's history: the services written up from the Telegram chat, made into
sets so leaders can plan from the Sets tab.

The history is rows of {date, song, key, reprise, leader}: one per song sung at a service, in
order. Each service becomes the set for its date, with each song matched to the library by its
title.

Run it in the site's container, where the library and the sets are (the files go in the data
volume, which git doesn't see). First see what it would do, then save:

  docker cp songs-sung.csv yfm:/app/data/history/
  docker exec yfm python history_import.py data/history/songs-sung.csv --from 2025-10-01 --to 2026-09-30
  docker exec yfm python history_import.py data/history/songs-sung.csv --from 2025-10-01 --to 2026-09-30 --write

songs-sung.csv is the spreadsheet's "Songs Sung" sheet saved as CSV (Service Date, Worship
Leader, Song, Key, Is Reprise…). --mapping titles.csv (columns title, slug) says which library
song a title is; an empty slug means it isn't in the library. Fix the mapping and run again: a
date that already has a set is left alone, so only what's missing is added.

Who led is kept too (see SetStore.set_leaders), and shown on past sets: from the history's Worship Leader column,
and for coming Saturdays from a schedule (columns date, leader), which replaces whoever was down
for its dates:

  docker exec yfm python history_import.py --schedule data/history/schedule.csv --write
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import date as Date
from datetime import datetime

import chordtools as ct
import pdf2chordpro as p2c
from import_drive import title_keys

LAYOUT = {"layout": "original", "columns": 2, "paper": "A4"}  # the builder's own defaults


def import_history(rows, library, store, now, mapping=None, write=True):
    """Make each service in `rows` the set for its date. Returns a report of what was done.

    `mapping` ({title as written: slug, or None for "not in the library"}) says which library
    song a title is, where matching titles doesn't (or gets it wrong). A date that already has a
    set keeps it, so importing again changes nothing. Who led each service is recorded too
    (see SetStore.set_leaders). With write=False, it only reports."""
    mapping = mapping or {}
    unknown = sorted(slug for slug in mapping.values() if slug and not library.get(slug))
    if unknown:
        raise ValueError(f"The mapping names songs the library doesn't have: {', '.join(map(repr, unknown))}")
    titles = _title_index(library)
    services = {}
    for r in rows:
        if not r["reprise"]:  # sung again later in the service: it's in the set once
            services.setdefault(r["date"], []).append(r)
    report = {"created": [], "skipped": [], "matched": {}, "mapped": {}, "not_in_library": [], "keys_left_out": [],
              "leaders": {d: leader(songs[0]["leader"]) for d, songs in sorted(services.items()) if songs[0]["leader"]}}
    if write:
        store.set_leaders(report["leaders"])
    for date, songs in sorted(services.items()):
        if store.on_date(date):
            report["skipped"].append(date)
            continue
        entries = []
        for r in songs:
            title = r["song"]
            if title in mapping:
                slug = mapping[title]
                if slug:
                    report["mapped"][title] = slug
            else:
                slug = next((titles[k] for k in _keys(title) if k in titles), None)
                if slug:
                    report["matched"][title] = slug
            if not slug and title not in report["not_in_library"]:
                report["not_in_library"].append(title)
            to = _key(r["key"], library.get(slug) if slug else None)
            if r["key"] and not to:
                report["keys_left_out"].append((date, title, r["key"]))
            entries.append({"slug": slug, "to": to} if slug else {"title": title, "to": to})
        if write:
            store.create({"date": date, "songs": entries, **LAYOUT}, now)
        report["created"].append(date)
    report["not_in_library"].sort()
    return report


def import_schedule(rows, store, write=True):
    """Who leads on coming dates, from the maintainer's schedule ({date, leader} rows), replacing
    whoever was down for those dates. Returns {date: leader} as kept."""
    leaders = {r["date"]: leader(r["leader"]) for r in rows if r["leader"].strip()}
    if write:
        store.set_leaders(leaders)
    return leaders


def leader(name):
    """A leader's name as it's kept: a service led by two ("Bea & Cy") goes under the first."""
    return re.split(r"\s*(?:&|/|,|\band\b)\s*", name.strip())[0]


def _title_index(library):
    """{title key: slug}. A song with several arrangements is its first: v1 of the first source
    (in source order) that has it."""
    order = {s.name: i for i, s in enumerate(getattr(library, "sources", []))}
    first = sorted(library.songs().items(),
                   key=lambda kv: (order.get((kv[1].get("sources") or [""])[0], 0), kv[1].get("number", 1)))
    index = {}
    for slug, song in first:
        for key in _keys(song["title"]):
            index.setdefault(key, slug)
    return index


def _keys(title):
    """A title's loose identities (import_drive.title_keys), and for one in English and Chinese
    ("Beautiful Saviour 荣美的救主") its English words too: the chat often wrote only those."""
    keys = title_keys(title)
    english = "".join(ch for ch in title if not p2c.is_cjk(ch))
    if english != title and re.search(r"[A-Za-z]", english):
        keys += title_keys(english)
    return keys


def _key(written, song):
    """The key as written, spelt as the library spells keys. None (the chart's key) if it isn't a
    key, or the chart's own key isn't known, or it's major for a minor chart or the other way
    round: then how far to move is unclear."""
    parsed = ct.parse_key(written or "")
    chart = ct.parse_key(song["key"] or "") if song else None
    if not parsed or (song and not chart) or (chart and chart[1] != parsed[1]):
        return None
    return ct.key_name(*parsed)


# ---------------------------------------------------------------- the command


def read_history(path, start, end):
    """The "Songs Sung" sheet's rows (as CSV) for services from `start` to `end` (ISO dates)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [{"date": _iso_date(r["Service Date"]), "song": r["Song"].strip(), "key": (r["Key"] or "").strip(),
                 "reprise": r["Is Reprise"].strip().lower() == "yes", "leader": (r["Worship Leader"] or "").strip()}
                for r in csv.DictReader(f) if r["Song"].strip()]
    return [r for r in rows if start <= r["date"] <= end]


def _iso_date(text):
    """'2025-10-04' (or '2025-10-04 00:00:00') -> '2025-10-04'; anything else is refused."""
    try:
        return Date.fromisoformat(text.strip()[:10]).isoformat()
    except ValueError:
        sys.exit(f"Service Date {text!r} isn't a date written as YYYY-MM-DD: save the sheet with dates like that.")


def read_schedule(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{"date": _iso_date(r["date"]), "leader": r["leader"]}
                for r in csv.DictReader(f) if r["date"].strip()]


def read_mapping(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["title"].strip(): (r["slug"] or "").strip() or None for r in csv.DictReader(f) if r["title"].strip()}


def _local_copy_only(library):
    """Read the library's local copy as it is, without checking Drive: the site does that."""
    for lib in (library, getattr(library, "mirror", None)):
        if lib is not None and hasattr(lib, "synced_at"):
            lib.synced_at = time.time()
            lib.first_sync.set()


def print_report(report, library, write):
    title = lambda slug: library.get(slug)["title"]
    n = len(report["created"])
    print(f"{'Created' if write else 'Would create'} {n} set{'s' * (n != 1)}.")
    if report["skipped"]:
        print(f"\nLeft alone, already a set: {', '.join(report['skipped'])}")
    loose = {t: s for t, s in report["matched"].items() if t != title(s)}
    sections = [
        ("Not in the library", [f"  {t}" for t in report["not_in_library"]]),
        ("Matched by a title written differently: check these", [f"  {t}  ->  {title(s)}  ({s})" for t, s in sorted(loose.items())]),
        ("Matched by the mapping", [f"  {t}  ->  {title(s)}  ({s})" for t, s in sorted(report["mapped"].items())]),
        ("Keys left out (the chart's key is used)", [f"  {d}  {t}: {k!r}" for d, t, k in report["keys_left_out"]]),
    ]
    for heading, lines in sections:
        if lines:
            print(f"\n{heading} ({len(lines)}):")
            print("\n".join(lines))
    print(f"\n{len(report['matched']) - len(loose)} more titles matched the library exactly.")
    print_leaders(report["leaders"], write)


def print_leaders(leaders, write):
    if leaders:
        print(f"\n{'Recorded' if write else 'Would record'} who led on {len(leaders)} date{'s' * (len(leaders) != 1)}:")
        print("\n".join(f"  {d}  {name}" for d, name in sorted(leaders.items())))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make the team's past services into sets.")
    ap.add_argument("history", nargs="?", help='the "Songs Sung" sheet as CSV')
    ap.add_argument("--from", dest="start", type=Date.fromisoformat, help="first service date")
    ap.add_argument("--to", dest="end", type=Date.fromisoformat, help="last service date")
    ap.add_argument("--mapping", help="CSV of title, slug (empty slug: not in the library)")
    ap.add_argument("--schedule", help="CSV of date, leader: who leads on coming dates")
    ap.add_argument("--write", action="store_true", help="save (without it, only report)")
    args = ap.parse_args(argv)
    if not (args.history or args.schedule):
        ap.error("give the history, a --schedule, or both")
    if args.history and not (args.start and args.end):
        ap.error("the history needs --from and --to")

    import sets_api
    from library import library_from_env
    from sets_store import SetStore

    store = SetStore(os.environ.get("SETS_DB") or sets_api.DEFAULT_DB)
    if args.history:
        library = library_from_env()
        _local_copy_only(library)
        rows = read_history(args.history, args.start.isoformat(), args.end.isoformat())
        if not rows:
            sys.exit("No services in those dates.")
        mapping = read_mapping(args.mapping) if args.mapping else None
        report = import_history(rows, library, store, datetime.now(), mapping=mapping, write=args.write)
        print_report(report, library, args.write)
    if args.schedule:
        print_leaders(import_schedule(read_schedule(args.schedule), store, write=args.write), args.write)
    if not args.write:
        print("\nNothing saved: run again with --write to save.")


if __name__ == "__main__":
    main()
