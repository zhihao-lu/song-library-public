#!/usr/bin/env python3
"""Test run: build the three-source library without serving it, and compare it with today's.

Writes one report (HTML) to read once before the switch-over:

  songs missing        today's songs the new library has no ID or alias for
  new songs            songs today's library doesn't have
  merges               arrangements shared by several sources, and today's songs folded together
  new arrangements     a source's v2, v3…
  aliases              today's slugs that now open another arrangement
  only in the flat PDF folder   files the sources don't have (move them into YFM first)
  skipped              files not taken in, and why

Every item links to its Drive file. It's safe while the site is running: it keeps its own
local copy (data/test-run; files the site's copies already have are copied, not downloaded)
and works on a copy of the sets database's IDs, so neither what the site serves nor its
copies change. Re-running carries on where a Google download block stopped it.

Usage:
  .venv/bin/python library_report.py [--out data/test-run/report.html]
"""

import argparse
import html
import re
from collections import defaultdict
from pathlib import Path

import config  # noqa: F401  (loads .env)
from charts import file_path
from library import parse_song

ROOT = Path(__file__).parent


def drive_link(file_id, rk=None):
    if not file_id or str(file_id).startswith("fix:"):
        return None
    return f"https://drive.google.com/file/d/{file_id}/view" + (f"?resourcekey={rk}" if rk else "")


def todays_songs(folder):
    """Today's library (the mirror's local copy): {slug: {"title", "path"}}."""
    folder = Path(folder)
    out = {}
    for path in sorted(folder.glob("*.cho")) + sorted((folder / "auto").glob("*.cho")):
        song = parse_song(path)
        out.setdefault(song["slug"], {"title": song["title"],
                                      "path": file_path({"path": song["folder"], "name": song["source_file"]})})
    return out


def findings(library, today, aliases_before=()):
    """What the report lists. `library` is a synced SourcesLibrary, `today` todays_songs()."""
    manifest = library._manifest
    files = manifest["files"]

    def chart_item(c):
        e = files.get(c.get("pdf_id") or c.get("id"), {})
        return {"name": f"{c.get('source', '')}/{file_path(c)}",
                "link": drive_link(c.get("pdf_id") or c.get("id"), e.get("rk"))}

    arrangements = library.arrangements
    by_slug = {a["slug"]: a for a in arrangements}
    aliases = dict(library._aliases)
    flat = manifest["flat"]

    def flat_link(slug):
        """Today's PDF of a song in the flat folder (<slug>.pdf, a Word file, or an @<Key> PDF)."""
        names = sorted(n for n in flat if re.match(re.escape(slug) + r"(?:@[^.]*)?\.(?:pdf|docx?)$", n, re.I)
                       or n == slug)
        f = flat[names[0]] if names else None
        return drive_link(f["id"], f.get("rk")) if f else None

    missing = [{"slug": s, "title": t["title"], "name": t["path"], "link": flat_link(s)}
               for s, t in today.items() if library.resolve(s) not in by_slug]
    kept = {library.resolve(s) for s in today}
    songs = defaultdict(list)
    for a in arrangements:
        songs[a["song_id"]].append(a)
    new_songs = [{"title": arrs[0]["title"], "arrangements": [
                     {"tag": a["tag"], "slug": a["slug"], **chart_item(a["best"])} for a in arrs]}
                 for arrs in songs.values() if not any(a["slug"] in kept for a in arrs)]
    folded = defaultdict(list)
    for alias, slug in aliases.items():
        if alias in today:
            folded[slug].append(alias)
    merges = [{"title": a["title"], "tag": a["tag"], "slug": a["slug"], "folded": sorted(folded.get(a["slug"], [])),
               "charts": [chart_item(c) for c in a["charts"] if not c.get("fix_only")]}
              for a in arrangements if len(a["sources"]) > 1 or folded.get(a["slug"])]
    new_arrangements = [{"title": a["title"], "tag": a["tag"], "slug": a["slug"], **chart_item(a["best"])}
                        for a in arrangements if a["number"] > 1]
    alias_list = [{"alias": al, "slug": s, "title": by_slug[s]["title"] if s in by_slug else s,
                   "new": al not in aliases_before}
                  for al, s in sorted(aliases.items())]
    in_sources = {e["md5"] for e in files.values() if e.get("md5")} | set(files)
    flat_only = [{"name": name, "link": drive_link(f["id"], f.get("rk"))} for name, f in sorted(flat.items())
                 if f["id"] not in in_sources and f.get("md5") not in in_sources
                 and re.search(r"\.(?:pdf|docx?)$", name, re.I)]
    skipped = sorted(({"name": f"{e['source']}/{file_path(e)}",
                       "reason": e["record"]["skipped"], "link": drive_link(i, e.get("rk"))}
                      for i, e in files.items() if e["record"].get("skipped")), key=lambda r: (r["reason"], r["name"]))
    skipped += sorted(({"name": f"{e['source']}/{file_path(e)}", "reason": e["reason"], "link": drive_link(i, e.get("rk"))}
                       for i, e in manifest.get("failed", {}).items()), key=lambda r: r["name"])
    return {"today": len(today), "arrangements": len(arrangements), "songs": len(songs),
            "sources": library.status()["sources"], "error": (library.last_result or {}).get("error"),
            "missing": missing, "new_songs": new_songs, "merges": merges, "new_arrangements": new_arrangements,
            "aliases": alias_list, "flat_only": flat_only, "skipped": skipped}


# ------------------------------------------------------------------ the page

def _a(text, link):
    return f'<a href="{html.escape(link)}">{html.escape(text)}</a>' if link else html.escape(text)


def render(f):
    e = html.escape
    sections = []

    def section(title, items, what, rows):
        body = "<p class=none>None.</p>" if not items else "<ul>" + "".join(f"<li>{rows(i)}</li>" for i in items) + "</ul>"
        sections.append(f"<section><h2>{e(title)} <span class=n>{len(items)}</span></h2><p class=what>{e(what)}</p>{body}</section>")

    section("Songs missing", f["missing"], "Today's songs with no ID or alias in the new library. Sets with them would "
            "show '(removed from library)'. Fix: move the file into YFM, or add a 'same as' row.",
            lambda i: f"<b>{e(i['title'])}</b> <code>{e(i['slug'])}</code> · {_a(i['name'] or 'today’s PDF', i['link'])}")
    section("New songs", f["new_songs"], "Songs today's library doesn't have.",
            lambda i: f"<b>{e(i['title'])}</b>: " + ", ".join(
                f"{_a(a['tag'], a['link'])} <code>{e(a['slug'])}</code>" for a in i["arrangements"]))
    section("Merges", f["merges"], "Arrangements several sources share (same shape), and today's songs folded into one.",
            lambda i: f"<b>{e(i['title'])}</b> · {e(i['tag'])} <code>{e(i['slug'])}</code>"
                      + (f" — folds in {', '.join(f'<code>{e(s)}</code>' for s in i['folded'])}" if i["folded"] else "")
                      + "<br><small>" + " · ".join(_a(c["name"], c["link"]) for c in i["charts"]) + "</small>")
    section("New arrangements", f["new_arrangements"], "A source's second, third… way of playing a song "
            "(different music). Name one with a 'label' row, or join it with 'same as' if it's a wrong match.",
            lambda i: f"<b>{e(i['title'])}</b> · {e(i['tag'])} <code>{e(i['slug'])}</code> · {_a(i['name'], i['link'])}")
    section("Aliases", f["aliases"], "Old IDs that now open another arrangement (saved sets keep working).",
            lambda i: f"<code>{e(i['alias'])}</code> → <code>{e(i['slug'])}</code> {e(i['title'])}"
                      + (" <span class=tag>new</span>" if i["new"] else ""))
    section("Only in the flat PDF folder", f["flat_only"], "Files no source has. Move each into YFM's resource "
            "folder (moving keeps its Drive ID) before switching over.", lambda i: _a(i["name"], i["link"]))
    section("Skipped", f["skipped"], "Files not taken in, and why.",
            lambda i: f"{_a(i['name'], i['link'])} — {e(i['reason'])}")
    per_source = " · ".join(f"{e(s['name'])} {s['songs']}" for s in f["sources"])
    warning = (f"<p class=warn>This run didn't finish: {e(f['error'])}. Run it again later to carry on.</p>"
               if f["error"] else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Library test run</title>
<style>
  :root {{ --bg: #f6f5f2; --text: #1d1d1f; --muted: #6b6b70; --accent: #1f5fae; --warn: #b3541e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #161618; --text: #ececef; --muted: #9a9aa2; --accent: #7fb0ff; --warn: #f0a070; }} }}
  body {{ background: var(--bg); color: var(--text); font: 15px/1.5 -apple-system, system-ui, sans-serif;
         max-width: 900px; margin: 0 auto; padding: 24px 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 17px; margin: 28px 0 2px; }}
  .n {{ color: var(--muted); font-weight: 400; }} .what, .none, small {{ color: var(--muted); }}
  .warn {{ color: var(--warn); font-weight: 600; }} a {{ color: var(--accent); }}
  code {{ font-size: 13px; }} li {{ margin: 4px 0; overflow-wrap: anywhere; }} .tag {{ color: var(--warn); font-size: 12px; }}
</style></head><body>
<h1>Library test run</h1>
<p>Today: {f['today']} songs. The three-source library: {f['songs']} songs in {f['arrangements']} arrangements ({per_source}).</p>
{warning}{''.join(sections)}
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", type=Path, default=ROOT / "data" / "test-run", help="the test run's own local copy")
    ap.add_argument("--today", type=Path, default=ROOT / "data" / "drive-library", help="today's library (its local copy)")
    ap.add_argument("--sets-db", type=Path, default=ROOT / "data" / "sets.db")
    ap.add_argument("--out", type=Path, default=None, help="default: <folder>/report.html")
    ap.add_argument("--downloads", type=Path, default=ROOT / "data" / "drive-downloads",
                    help="the source folders downloaded from Drive's web page and unzipped (optional): "
                         "files found there are copied instead of downloaded")
    args = ap.parse_args()

    from drive_mirror import paced_client
    from library_ids import IdStore
    from sources_library import SourcesLibrary
    ids = IdStore(":memory:", copy_of=args.sets_db)
    # Unlike the site, which gives up at once and carries on at its next check, a test run waits
    # out Google's "slow down" answers, at a gentler pace.
    client = paced_client()
    client.MIN_INTERVAL, client.BACKOFF = 0.5, (30, 60, 120, 240, 480)
    library = SourcesLibrary(args.folder, client, ids, background=False,
                             adopt_from=[ROOT / "data" / "sources-library", args.today, args.downloads])
    before = set(ids.load()["aliases"])
    print("Listing the sources and taking in new files (this can take a while)…", flush=True)
    library.sync(force=True)
    out = args.out or args.folder / "report.html"
    out.write_text(render(findings(library, todays_songs(args.today), before)), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
