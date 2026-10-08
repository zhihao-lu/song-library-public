# Corrections (three-source library)

With `LIBRARY_MODE=sources`, the site works out songs and arrangements from the charts in
the YFM, ASE and MM folders by itself. When it gets something wrong, fix it in Drive, never
on the site: one row in the corrections Sheet, or a hand-fixed `.cho` file. Both live in
YFM's `zz. Automation/.cho Files` folder. The site reads them at the next check (hourly), or
straight away when someone presses **Sync now**.

## Creating the Sheet (once, at the switch-over)

1. On the server, write today's original keys and exclude list as the Sheet's first rows:

   ```bash
   .venv/bin/python tools/corrections_sheet.py
   ```

   This reads the site's copy of `_original_keys.csv` and `_exclude.txt` and writes
   `~/Downloads/_corrections.csv`, with every row carried over unchanged.
2. In Google Drive, open `.cho Files`, then **New → Google Sheets**. Name it exactly
   `_corrections`.
3. **File → Import → Upload** `_corrections.csv`, and choose **Replace current sheet**.
4. Press **Sync now** on the site.

Leave `_original_keys.csv` and `_exclude.txt` where they are: today's library still reads
them, so switching back stays one restart away.

## The columns

| Column           | What it does                                                          |
|------------------|-----------------------------------------------------------------------|
| `id`             | What the row is about: an arrangement ID, or a file's path            |
| `title`          | The song's title                                                      |
| `artist`         | The song's artist                                                     |
| `original_key`   | The key of the original recording                                     |
| `label`          | The arrangement's name, shown instead of "v2": `piano intro · YFM`    |
| `same_as`        | An ID: this song is the same song as that one                         |
| `different_song` | `yes`: this file (or arrangement) isn't the song it was matched with  |
| `leave_out`      | `yes`: never take this file (or arrangement) in                       |
| `note`           | Anything, for people. The site ignores it                             |

**Finding an ID.** The test-run report lists each arrangement's ID. For one song, open
`/api/library/search?q=<title>` on the site: each result's `slug` is its ID.

**A file's path** is its folders inside its source, then its name, as shown under each
search result. It can start with the source's name:
`YFM/3. English Songs/Holy Ground/Holy Ground - G.pdf`,
`3. English Songs/Holy Ground/Holy Ground - G.pdf` and a bare `Holy Ground - G.pdf` all work.

## One row per problem

| Problem                                          | Row                                                         |
|--------------------------------------------------|-------------------------------------------------------------|
| A song's title is wrong                          | `id` = its ID, `title` = the right title                    |
| The artist is missing or wrong                   | `id` = its ID, `artist` = the artist                        |
| The original key is missing or wrong             | `id` = its ID, `original_key` = e.g. `Bb`                   |
| "v2" should say how it differs                   | `id` = that arrangement's ID, `label` = e.g. `piano intro`  |
| Two results are really one song                  | `id` = one ID, `same_as` = the other ID                     |
| Two songs got mixed up as one                    | `id` = the path of the wrong file, `different_song` = `yes` |
| A file isn't a chart (a tab, a song list…)       | `id` = its path, `leave_out` = `yes`                        |
| A whole arrangement shouldn't be on the site     | `id` = its ID, `leave_out` = `yes`                          |
| A file's title is read wrongly, so it matches the wrong song | `id` = its path, `title` = the right title      |

Title, artist and original key apply to the whole song, whichever of its arrangements'
IDs the row names. A label applies to the one arrangement.

## Hand-fixed charts

A `.cho` file in `.cho Files` named by an arrangement ID (`holy-ground-g.cho`) is that
arrangement's chart in its `{key}`. It always wins over any upload of the same music, and
search stops marking the song "converted automatically, may need checking". Original-layout
setlists still use the uploaded PDF it was made from (`{meta: source_file …}`), or the
arrangement's PDF in the same key. `tools/replace_source.py` makes a `.cho` to start from.

Today's `.cho` files are all hand-fixed charts in this sense: each names the YFM file it came
from, which is also how today's songs keep their IDs.
