"""The three-source library worked out from its charts: songs, arrangements and their IDs.

build() is the whole of it, and touches nothing: it takes every chart the intake found (with
its analysis, see charts.py), the hand-fixed .cho files, the corrections and the ID store's
last state, and returns the arrangements plus the ID store's next state. See CONTEXT.md for
the words and docs/adr/0002 for the rules:

  songs         charts whose titles match (keys, brackets, punctuation, pinyin codes and
                Traditional/Simplified aside) and whose artists don't contradict
  arrangements  within one source, a song's charts with the same music; an as-is chart (which
                can't be read) joins that source's first arrangement of its song as another key
  merges        across sources, arrangements whose charts have the same shape become one,
                tagged with every source
  charts shown  one per key, ranked: hand-fixed > readable > as-is > fewer pages > real PDF
                over Word/Google Doc > newest; but the chart shown comes from one that can be
                transposed, in Simplified rather than Traditional Chinese, one page first
                (shown_rank; key_rank for a key's own chart).
                A set plays the shown chart transposed; the other keys' charts are only for
                a song that can't be transposed in its own look (setlist.render_song)
  IDs           kept by Drive file ID; today's songs keep today's slug; slugs of arrangements
                folded into another become aliases; an ID with no charts left is "removed"
                and comes back if its charts do
"""

import re
from collections import defaultdict
from functools import lru_cache

from charts import _containment, file_path, same_music, same_shape, song_title_keys
from pdf2chordpro import CJK_RE, to_simplified
from import_drive import artist_words, slugify, title_key


def rank(chart):
    """Sort key for charts: larger is better."""
    return (chart.get("hand_fixed", False), not chart["as_is"], -chart["pages"], not chart.get("document", False),
            chart.get("modified", ""))


@lru_cache(maxsize=4096)
def _traditional(cho):
    """Are a chart's Chinese lyrics in Traditional characters? One in ten or more of its Chinese
    characters being Traditional is (a Simplified chart has at most a credit or a title in it)."""
    han = [ch for ch in cho if CJK_RE.match(ch)]
    return bool(han) and sum(1 for ch in han if to_simplified(ch) != ch) >= len(han) / 10


def traditional(chart):
    """A readable chart in Traditional Chinese (a picture's characters can't be read)."""
    return not chart["as_is"] and _traditional(chart.get("cho") or "")


def shown_rank(chart):
    """Sort key for the chart an arrangement is shown and transposed from: one that can be
    transposed (or that someone hand-fixed), then one in Simplified rather than Traditional
    Chinese (the team reads Simplified), then one page (over a hand-fixed longer one too), then
    as rank."""
    usable = not chart["as_is"] or chart.get("hand_fixed", False)
    return (usable, not traditional(chart), chart["pages"] == 1) + rank(chart)


def key_rank(chart):
    """Sort key for a chart used as it is in its own key: one page first, then as rank."""
    return (chart["pages"] == 1,) + rank(chart)


class _UnionFind:
    """Union-find over indices."""

    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)

    def groups(self, items):
        out = defaultdict(list)
        for i, item in enumerate(items):
            out[self.find(i)].append(item)
        return list(out.values())


# ------------------------------------------------------------------ corrections

def _matches(key, chart):
    """Is a corrections row keyed by `key` about this chart (by its path, or bare file name)?"""
    full = file_path(chart)
    return key in (f"{chart['source']}/{full}", full, chart["name"])


class Corrections:
    """The corrections Sheet's rows, looked up by arrangement ID or by a chart's file path."""

    COLUMNS = ("title", "artist", "original_key", "label", "same_as", "different_song", "leave_out")

    def __init__(self, rows=()):
        self.rows = [{c: (r.get(c) or "").strip() for c in ("id",) + self.COLUMNS} for r in rows]
        self.rows = [r for r in self.rows if r["id"]]
        self.aliases = {}  # a row keyed by an alias is about the arrangement it stands for

    @staticmethod
    def _yes(value):
        return value.strip().lower() in ("yes", "y", "true", "1", "x", "✓", "✔")

    def for_chart(self, chart):
        return [r for r in self.rows if _matches(r["id"], chart)]

    def for_id(self, slug):
        return [r for r in self.rows if self.aliases.get(r["id"], r["id"]) == slug]

    def value(self, rows, column):
        return next((r[column] for r in rows if r[column]), None)

    def flag(self, rows, column):
        return any(self._yes(r[column]) for r in rows)


# ------------------------------------------------------------------ songs

def _songs(charts, corrections, ids_of_chart, forced=()):
    """Charts grouped into songs: lists of charts. `forced` is pairs of charts to put together."""
    by_title = defaultdict(list)  # title key -> [(artist words, chart index)]
    sets = _UnionFind(len(charts))
    order = sorted(range(len(charts)), key=lambda i: rank(charts[i]), reverse=True)
    for i in order:
        c = charts[i]
        rows = corrections.for_chart(c) + [r for s in ids_of_chart(c) for r in corrections.for_id(s)]
        # "Different song": a song of its own (with the other charts that row is about).
        split = next((r["id"] for r in rows if corrections.flag([r], "different_song")), None)
        keys = [f"{key}|{split}" if split else key for key in song_title_keys(c["title"])]
        words = artist_words(c["artist"])
        found = None
        for key in keys:
            for other_words, j in by_title.get(key, []):
                if not words or not other_words or words & other_words:
                    found = j
                    break
            if found is not None:
                break
        if found is not None:
            sets.union(found, i)
        for key in keys:
            by_title[key].append((words, i))
    index = {id(c): i for i, c in enumerate(charts)}
    for a, b in forced:
        sets.union(index[id(a)], index[id(b)])
    return sets.groups(charts)


# Charts with one title are different songs ("我相信", "Hosanna", "Forever" are each several)
# when two readable ones share almost none of their lyrics. Tuned on the real sources: different
# songs shared at most 6% of their lyric runs, one song's charts at least 12% (a hymn with another
# set of verses). Chords can't decide it: common progressions line up across different songs.
SAME_SONG_LYRICS = 0.1
LYRIC_RUNS = 20  # a chart with fewer lyric runs than this (a picture, a short fragment) can't say


REAL_WORDS = 0.5  # share of a chart's lyric tokens that must be words: fewer is a garbled reading


@lru_cache(maxsize=4096)
def _real_words(cho):
    """The share of a chart's lyric tokens that are words (a Chinese character, or two letters or
    more), not the stray symbols and digits of a page read wrongly."""
    text = " ".join(re.sub(r"\[[^\]]*\]", " ", line) for line in cho.splitlines() if not line.lstrip().startswith("{"))
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z']+|[^\sA-Za-z'\u4e00-\u9fff]+", text)
    return sum(1 for t in tokens if CJK_RE.match(t) or len(t) >= 2 and t[0].isalpha()) / len(tokens) if tokens else 0


def _lyric_runs(chart):
    runs = (chart.get("music") or {}).get("lyrics") or []
    readable = (not chart["as_is"] and chart.get("lyrics_ok", True) and len(runs) >= LYRIC_RUNS
                and _real_words(chart.get("cho") or "") >= REAL_WORDS)
    return set(runs) if readable else None


def _same_song(runs_a, runs_b):
    return _containment(runs_a, runs_b) >= SAME_SONG_LYRICS


def _split_by_lyrics(song, forced=()):
    """One title's charts -> the songs they really are. Readable charts whose lyrics agree stay
    together; a chart that can't be read goes with the charts from its own folder, else with
    the best chart's song; charts a "same as" correction put together stay together."""
    runs = [_lyric_runs(c) for c in song]
    readable = [i for i, r in enumerate(runs) if r is not None]
    if len(readable) < 2:
        return [song]
    sets = _UnionFind(len(song))
    for x, i in enumerate(readable):
        for j in readable[x + 1:]:
            if sets.find(i) != sets.find(j) and _same_song(runs[i], runs[j]):
                sets.union(i, j)
    if len({sets.find(i) for i in readable}) == 1:
        return [song]
    best = max(readable, key=lambda i: rank(song[i]))
    placed = {id(c) for pair in forced for c in pair}  # a "same as" row says where these go
    for i, c in enumerate(song):
        if runs[i] is None and id(c) not in placed:
            near = [j for j in readable if song[j]["source"] == c["source"] and song[j].get("path") == c.get("path")]
            sets.union(i, max(near, key=lambda j: rank(song[j])) if near else best)
    index = {id(c): i for i, c in enumerate(song)}
    for a, b in forced:
        if id(a) in index and id(b) in index:
            sets.union(index[id(a)], index[id(b)])
    return sets.groups(song)


def _arrangements(song_charts, sources):
    """One song's charts -> per-source arrangements (lists of charts), each source's in rank order."""
    out = []
    for source in sources:
        mine = sorted((c for c in song_charts if c["source"] == source), key=rank, reverse=True)
        clusters = []
        for c in (c for c in mine if not c["as_is"]):
            home = next((cl for cl in clusters if any(same_music(c["music"], o["music"]) for o in cl
                                                        if not o["as_is"])), None)
            if home is None:
                clusters.append([c])
            else:
                home.append(c)
        as_is = [c for c in mine if c["as_is"]]
        if as_is:
            if clusters:
                clusters[0] += as_is  # other keys of the source's first arrangement
            else:
                clusters.append(as_is)
        out += clusters
    return out


def _alike(a, b):
    """Charts from two sources that are one sheet: the same shape, and (when they can be read)
    the same music, so a sheet with other chords over the same lyric lines stays apart."""
    if not same_shape(a, b):
        return False
    return a["as_is"] or b["as_is"] or not a["music"] or not b["music"] or same_music(a["music"], b["music"]) \
        or a.get("md5") and a.get("md5") == b.get("md5")


def _merge_across(arrangements):
    """Arrangements from different sources whose charts are alike, merged. A merged arrangement
    never holds two arrangements of one source."""
    groups = [[a] for a in arrangements]

    def sources_of(group):
        return {arr[0]["source"] for arr in group}
    for i, a in enumerate(arrangements):
        for j in range(i + 1, len(arrangements)):
            b = arrangements[j]
            ga = next(g for g in groups if any(x is a for x in g))
            gb = next(g for g in groups if any(x is b for x in g))
            if ga is gb or sources_of(ga) & sources_of(gb):
                continue
            if any(_alike(x, y) for x in a for y in b):
                ga += gb
                groups.remove(gb)
    return [[c for arr in group for c in arr] for group in groups]


# ------------------------------------------------------------------ IDs

def tag_of(label, number):
    """An arrangement's name in its tag: its label, else "v2", "v3"… (nothing for the first)."""
    return label or (f"v{number}" if number > 1 else "")


def _new_slug(title, tag, taken):
    base = slugify(title) + (f"-{slugify(tag)}" if tag else "")
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def build(charts, sources, state, fixes=None, corrections=None, legacy=None):
    """Work out the library. Returns (arrangements, next state).

    charts       [{"id", "source", "path", "name", "document", "modified", "md5", + analysis}]
    sources      source names in display order
    state        the ID store's state (library_ids.IdStore.load())
    fixes        {slug: {"cho", "title", "key", "source_folder", "source_file", ...}}: hand-fixed .cho files
    corrections  Corrections
    legacy       {"paths": {"folder/file": slug}, "md5s": {md5: slug}, "file_ids": {id: slug}}:
                 today's songs, for keeping their slugs
    """
    fixes = fixes or {}
    corrections = corrections or Corrections()
    corrections.aliases = dict(state["aliases"])
    legacy = legacy or {"paths": {}, "md5s": {}, "file_ids": {}}
    aliases = dict(state["aliases"])
    known = state["ids"]
    file_slugs = {f: s for f, (s, _) in state["files"].items()}
    md5_slugs = {m: s for f, (s, m) in state["files"].items() if m}

    def resolve(slug):
        return aliases.get(slug, slug)

    def claims(c):
        """The IDs a chart has been, or is today, in the order to trust them."""
        found = []
        if c["id"] in file_slugs:
            found.append(file_slugs[c["id"]])
        elif c.get("md5") in md5_slugs and known.get(md5_slugs[c["md5"]], {}).get("removed"):
            found.append(md5_slugs[c["md5"]])  # the same file uploaded again
        if c["source"] == sources[0]:
            path = file_path(c)
            for slug in (legacy["paths"].get(path), legacy["file_ids"].get(c["id"]), legacy["md5s"].get(c.get("md5"))):
                if slug:
                    found.append(slug)
        return [resolve(s) for s in found]

    # Files left out by a correction (by path, or all of an arrangement's).
    kept = []
    for c in charts:
        rows = corrections.for_chart(c) + [r for s in claims(c) for r in corrections.for_id(s)]
        if corrections.flag(rows, "leave_out"):
            continue
        mine = corrections.for_chart(c)
        fixed = {k: v for k in ("title", "artist") if (v := corrections.value(mine, k))}
        kept.append({**c, **fixed})
    charts = kept

    # Hand-fixed charts whose arrangement has no uploads: an arrangement of their own.
    claimed = {s for c in charts for s in claims(c)}
    lone = []
    for slug, fix in fixes.items():
        if resolve(slug) not in claimed:
            lone.append({"id": f"fix:{slug}", "source": sources[0], "path": fix.get("source_folder") or "",
                         "name": fix.get("source_file") or f"{slug}.cho", "document": False, "modified": "",
                         "md5": None, "title": fix["title"], "artist": fix.get("artist", ""), "key": fix.get("key"),
                         "as_is": fix.get("as_is", False), "pages": 1, "music": None, "shape": [],
                         "lyrics_ok": True, "fix_only": slug})
    for c in lone:
        claimed.add(c["fix_only"])

    def claims_all(c):
        return [c["fix_only"]] if c.get("fix_only") else claims(c)

    def arrange(forced):
        songs = [part for song in _songs(charts + lone, corrections, claims_all, forced)
                 for part in _split_by_lyrics(song, forced)]
        result = []
        for song in songs:
            # A music book's charts (only_new) count only for a song nobody else has, as one arrangement.
            if any(not c.get("only_new") for c in song):
                song = [c for c in song if not c.get("only_new")]
            elif len(song) > 1:
                first = _arrangements(song, sources)[0]
                song = first
            parts = [p for p in _arrangements([c for c in song if not c.get("fix_only")], sources)]
            parts += [[c] for c in song if c.get("fix_only")]
            result.append(_merge_across(parts))
        return result

    taken_extra = (set(legacy["paths"].values()) | set(legacy["md5s"].values()) | set(legacy["file_ids"].values())
                   | set(fixes))
    songs = arrange(())
    named_by = []  # charts whose song's title a "same as" song takes
    # "Same as <id>": put a chart's (or an arrangement's) song together with that ID's song.
    same_as = [r for r in corrections.rows if r["same_as"]]
    if same_as:
        first, _ = _assign_ids(songs, sources, state, corrections, claims_all, taken_extra)

        def charts_of(key):
            key = resolve(key)
            return [c for a in first for c in a["charts"] if a["slug"] == key or _matches(key, c)]
        forced = []
        for r in same_as:
            ours, theirs = charts_of(r["id"]), charts_of(r["same_as"])
            forced += [(ours[0], theirs[0])] if ours and theirs else []
            named_by += theirs[:1]
        if forced:
            songs = arrange(forced)

    titles = []
    for song in songs:
        target = next((c for c in named_by if any(c is x for arr in song for x in arr)), None)
        titles.append((target or max((c for arr in song for c in arr), key=rank))["title"])
    arrangements, next_state = _assign_ids(songs, sources, state, corrections, claims_all, taken_extra, titles)
    fixes_of = defaultdict(list)  # a hand-fixed chart named by an alias is its arrangement's too
    for slug, fix in sorted(fixes.items()):
        fixes_of[next_state["aliases"].get(slug, slug)].append(fix)
    return _finish(arrangements, sources, fixes_of, corrections, next_state), next_state


def _assign_ids(songs, sources, state, corrections, claims, taken_extra=(), titles=None):
    """Give every arrangement its ID and number. Returns (arrangements, the ID store's next state).

    An arrangement keeps the ID most of its charts had (the strongest claims are served first);
    other IDs its charts had become aliases of it. A new arrangement takes back a removed ID of
    the same song and source, or gets a new one from its title. Numbers stay as given; a new
    arrangement gets the next one in its first source.
    """
    known, aliases = state["ids"], state["aliases"]
    flat = [(song_i, arr) for song_i, song in enumerate(songs) for arr in song]
    votes = []
    for _, arr in flat:
        counts = defaultdict(int)
        for c in arr:
            for s in claims(c):
                counts[s] += 1
        votes.append(counts)
    strongest = sorted(range(len(flat)), key=lambda i: (-max(votes[i].values(), default=0),
                                                         _best_first(rank(max(flat[i][1], key=rank)))))
    given, used = {}, set()
    for i in strongest:
        mine = [s for s, n in sorted(votes[i].items(), key=lambda kv: -kv[1]) if s not in used]
        if mine:
            given[i] = mine[0]
            used.add(mine[0])
    next_state = {"ids": {s: {**r, "removed": 1} for s, r in known.items()}, "files": dict(state["files"]),
                  "aliases": dict(aliases)}
    for i, counts in enumerate(votes):  # several of today's songs in one arrangement
        for s in counts:
            if s not in used and i in given:
                next_state["aliases"][s] = given[i]
                next_state["ids"].pop(s, None)
                used.add(s)
    taken = set(known) | set(aliases) | set(taken_extra) | used
    corrections.aliases = next_state["aliases"]

    arrangements, song_keys = [], set()
    # A song split in two (charts of one title that turned out to be different songs): the part
    # with the old song's first arrangement keeps its numbers; the others start again from v1.
    parts = defaultdict(int)
    for song_i in range(len(songs)):
        for key in {known[given[i]]["song"] for i, (si, _) in enumerate(flat) if si == song_i and given.get(i) in known}:
            parts[key] += 1
    def split_off(mine):
        known_here = [known[given[i]] for i in mine if given.get(i) in known]
        return any(parts[r["song"]] > 1 for r in known_here) and not any(r["number"] == 1 for r in known_here)
    for song_i, song in enumerate(songs):
        mine = [i for i, (si, _) in enumerate(flat) if si == song_i]
        song_charts = [c for _, arr in (flat[i] for i in mine) for c in arr]
        title = titles[song_i] if titles else max(song_charts, key=rank)["title"]
        # The song as the ID store knows it (most of its known IDs say), else by its title.
        stored = [known[given[i]]["song"] for i in mine if given.get(i) in known]
        song_key = title_key(title)
        if stored and song_key not in stored:
            song_key = max(stored, key=stored.count)
        if song_key in song_keys or split_off(mine):  # another song with this title
            song_key += "|" + min(c["id"] for c in song_charts)
        song_keys.add(song_key)
        occupied = defaultdict(set)
        for r in known.values():
            if r["song"] == song_key:
                occupied[r["source"]].add(r["number"])
        for i in mine:
            arr_sources = [s for s in sources if any(c["source"] == s for c in flat[i][1])]
            for s in arr_sources[1:]:
                occupied[s].add(1)  # merged into another source's arrangement: that's its first
        # This song's known IDs first, then IDs from another song (moved by a correction), then
        # today's songs (claimed), then by their best chart. A known ID keeps its number; one that
        # moved song, or whose number is taken, gets the next free one.
        taken_numbers = defaultdict(set)
        for i in sorted(mine, key=lambda i: (given.get(i) not in known,
                                             known.get(given.get(i), {}).get("song") != song_key, i not in given,
                                             _best_first(rank(max(flat[i][1], key=rank))))):
            arr = flat[i][1]
            arr_sources = [s for s in sources if any(c["source"] == s for c in arr)]
            slug = given.get(i)
            if (slug in known and known[slug]["song"] == song_key
                    and not any(known[slug]["number"] in taken_numbers[s] for s in arr_sources)):
                number, source = known[slug]["number"], known[slug]["source"]
            else:
                source = arr_sources[0]
                number = max(occupied[source], default=0) + 1
                if slug is None:
                    back = sorted((r for r in known.values() if r["removed"] and r["song"] == song_key
                                   and r["source"] == source and r["slug"] not in used),
                                  key=lambda r: r["number"])
                    if back:
                        slug, number = back[0]["slug"], back[0]["number"]
            # A number taken in any source the arrangement is in (MM's copy of YFM's v2 makes it an
            # MM arrangement too) is skipped, so no two arrangements in one source share a "v2".
            while any(number in taken_numbers[s] for s in arr_sources):
                number += 1
            occupied[source].add(number)
            for s in arr_sources:
                taken_numbers[s].add(number)
                occupied[s].add(number)
            label = corrections.value(corrections.for_id(slug), "label") if slug else None
            if slug is None:
                slug = _new_slug(title, tag_of(label, number), taken)
            used.add(slug)
            taken.add(slug)
            arrangements.append({"slug": slug, "song": song_i, "title": title, "song_key": song_key, "charts": arr, "sources": arr_sources,
                                 "number": number, "source": source, "label": label})
            next_state["ids"][slug] = {"slug": slug, "title": title, "song": song_key, "source": source,
                                       "number": number, "removed": 0}
            next_state["aliases"].pop(slug, None)
            for c in arr:
                if not c.get("fix_only"):
                    next_state["files"][c["id"]] = (slug, c.get("md5"))
    for alias, slug in list(next_state["aliases"].items()):  # no chains
        next_state["aliases"][alias] = next_state["aliases"].get(slug, slug)
    return arrangements, next_state


def _best_first(t):
    """A rank tuple turned around, for sorting best first."""
    out = []
    for x in t:
        if isinstance(x, str):
            out.append(tuple(-ord(ch) for ch in x) + (1,))
        else:
            out.append(-int(x))
    return tuple(out)


# ------------------------------------------------------------------ what's shown

def _finish(arrangements, sources, fixes, corrections, state):
    """Each arrangement's song title, artist and keys, its tag, and the chart shown per key.
    `fixes` is {slug: [hand-fixed charts]}."""
    by_song = defaultdict(list)
    for a in arrangements:
        by_song[a["song"]].append(a)
    out = []
    for arrs in by_song.values():
        arrs.sort(key=lambda a: (sources.index(a["sources"][0]), a["number"]))
        all_ids = [a["slug"] for a in arrs]
        song_rows = [r for s in all_ids for r in corrections.for_id(s)]
        fix = next((fixes[s][0] for s in all_ids if fixes.get(s)), None)
        title = corrections.value(song_rows, "title") or (fix or {}).get("title") or arrs[0]["title"]
        artist = corrections.value(song_rows, "artist") or (fix or {}).get("artist") or next(
            (c["artist"] for a in arrs for c in sorted(a["charts"], key=rank, reverse=True) if c["artist"]), "")
        original = corrections.value(song_rows, "original_key") or (fix or {}).get("original_key") or next(
            (c["original_key"] for a in arrs for c in a["charts"] if c.get("original_key")), None)
        for a in arrs:
            charts = list(a["charts"])
            charts += [_fixed_chart(fix, charts) for fix in fixes.get(a["slug"], [])]
            charts = [c for c in charts if not c.get("fix_only")]
            shown = max(charts, key=shown_rank)
            per_key = {}
            for c in sorted(charts, key=key_rank, reverse=True):
                if c.get("key") and c["key"] not in per_key:
                    per_key[c["key"]] = c
            tag = tag_of(a["label"], a["number"])
            out.append({**a, "song_id": arrs[0]["slug"], "title": title, "artist": artist,
                        "original_key": original, "best": shown, "per_key": per_key,
                        "tag": " · ".join([tag] + a["sources"] if tag else a["sources"])})
            state["ids"][a["slug"]]["title"] = title
    order = {s: i for i, s in enumerate(sources)}
    out.sort(key=lambda a: (a["title"].lower(), order[a["sources"][0]], a["number"]))
    return out


def _fixed_chart(fix, charts):
    """A hand-fixed .cho as a chart of its arrangement: it wins, and uses the PDF of the chart it
    was made from (or the arrangement's best chart in its key) for original layout."""
    path = file_path({"path": fix.get("source_folder"), "name": fix.get("source_file")})
    made_from = next((c for c in charts if path and file_path(c) == path), None)
    same_key = sorted((c for c in charts if c.get("key") == fix.get("key") and not c.get("fix_only")),
                      key=rank, reverse=True)
    pdf_of = made_from or (same_key[0] if same_key else None)
    return {**(pdf_of or {}), "hand_fixed": True, "fix": fix["slug"], "cho": fix["cho"], "key": fix.get("key"),
            "as_is": fix.get("as_is", False), "lyrics_ok": fix.get("lyrics_ok", True),
            "pdf_scan": fix.get("pdf_scan", False), "pdf_pages": fix.get("pdf_pages"),
            "title": fix["title"], "pages": (pdf_of or {}).get("pages", 1),
            "pdf_id": (pdf_of or {}).get("id") if pdf_of and not pdf_of.get("fix_only") else None,
            "source": (pdf_of or {}).get("source") or "fix", "fix_only": False}

