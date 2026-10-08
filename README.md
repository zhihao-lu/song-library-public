# Song Sheet Converter

A small web app for worship teams. It turns chord-sheet PDFs into ChordPro, transposes them to
any key while keeping the original page layout, and builds a whole setlist into one PDF.

- **Setlist builder** (`/`): search the song library, pick a key (and capo) for each song, drag to
  reorder, and download everything as **one PDF** with a bookmark per song.
- **Unsaved work**: the setlist being built is kept as a draft on the device and, once someone
  has typed their name, under the name on the server. A draft from the last 14 days is offered
  back (Continue it / Discard).
- **Share links**: with no date picked, **Share link** saves the chosen songs as a hidden
  snapshot and copies a link to it. Links are forgotten 30 days after they were last opened.
- **Sets** (`/sets`): setlists saved under their date and shared with the team, so a key can
  change during practice and the PDF is downloaded only once it's final. Past sets are what the
  team sang, for leaders to plan from.
- **Usage stats** (`/stats`, private): pages opened, searches (including ones with no results),
  songs added, PDFs downloaded, per visit. Needs `STATS_PASSWORD`.
- **Songs we sing** (`/analytics`, hidden, no password): every song in the past sets, ranked by how
  often it was sung, by year, quarter, month, worship leader or key, with a CSV download.
- **Single-PDF converter** (`/convert`): upload any chord-sheet PDF and get **ChordPro** or a
  **transposed PDF**.

> **Built for one church.** The pages still carry that church's name and logo (诸圣堂 All Saints'
> Church) and its worship team's name, **YFM**. Replace both with your own before your team
> uses it: see [Replacing the church's name, logo and "YFM"](#replacing-the-churchs-name-logo-and-yfm).

Transposed PDFs keep the original's look by default: the original page is kept, and only the
chords (and the "Key" line) are replaced at the same position, font size and baseline.
Superscript chords (F², Am⁷, Dm⁷⁽⁴⁾) stay superscript. A "uniform layout" option re-typesets songs
from ChordPro instead.

---

## Quick start (no Google account needed)

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
LIBRARY_DIR=examples/library .venv/bin/python app.py
```

Open <http://localhost:5050>. The library has two public-domain hymns in it
(`examples/library/`); search "grace", add it, pick a key and download the PDF.

To try your own songs, convert a chord-sheet PDF and drop the result in the same folder:

```bash
.venv/bin/python pdf2chordpro.py ~/Downloads/some-song.pdf -o examples/library/
cp ~/Downloads/some-song.pdf examples/library/   # optional: enables "original layout" output
```

Reload the page and it's searchable: the library notices new and changed `.cho` files by itself.

## Setting up your own copy

What the repo doesn't include, and you set up yourself:

| What | Why | Where |
| --- | --- | --- |
| **A song library** | No songs come with the repo (song sheets are usually copyrighted). | A local folder (`LIBRARY_DIR`) or two Google Drive folders. See [The song library](#the-song-library). |
| **`.env`** | Settings and secrets, never committed. | `cp .env.example .env`, then see [Configuration](#configuration). |
| **A Google API key** | Only if the library is in Google Drive. Free, read-only. | [Google Drive setup](#library-in-google-drive). |
| **Passwords** | `APP_PASSWORD` for the site (do set one if it's public), `STATS_PASSWORD` for `/stats`. | `.env` |
| **A server** | Anything that runs Docker: a home computer, a Raspberry Pi 4/5, a small VPS. | [Deploy](#deploy). |
| **Your branding** | The logo is All Saints' Church's and the page titles say "YFM". | [Replacing the church's name, logo and "YFM"](#replacing-the-churchs-name-logo-and-yfm). |

Sets, share links, drafts and usage stats are kept in SQLite files under `data/`, which start
empty and are never committed.

---

## The song library

The library is a flat folder of songs. Each song is a **`<slug>.cho`** ChordPro file, optionally
with the original **`<slug>.pdf`** next to it:

```
library/
  washed-chords-c.cho      ChordPro text: title, key, chords, lyrics (edit freely)
  washed-chords-c.pdf      the original PDF, used for original-layout output (optional)
  some-song@Bb.pdf         an as-is song's PDF in another key
  other-song.docx          a Word file or Google Doc works too: it's that song's PDF
  _original_keys.csv       optional: each song's original (recording) key, by slug
  _exclude.txt             optional: source files the Drive import should never bring back
```

A minimal `.cho`:

```
{title: Amazing Grace}
{artist: John Newton}
{key: G}

{start_of_verse: Verse 1}
A[G]mazing [G7]grace, how [C]sweet the [G]sound
{end_of_verse}
```

- Each song's key comes from its `{key: ...}` line. Transposing spells sharps or flats to suit the
  target key.
- Search covers titles (Chinese titles also by pinyin, e.g. `jiao wo tai`, or initials, e.g.
  `jwtqtds`), artists/composers, source folders and file names.
- A song can be just a `.cho`, with no PDF. It's then always shown in uniform layout.
- Editing a `.cho` shows in **uniform layout**. Original-layout output uses the `.pdf`, so if you fix
  chords in the `.cho`, either fix the PDF too or use uniform layout for that song.
- **Adding a PDF with no `.cho`** (Drive library only): the site converts it to ChordPro automatically and search marks
  it "converted automatically, may need checking". Sheet music and files without chords can't be
  converted.
- **As-is songs**: PDFs that can't be read (a picture of a sheet, or a chart drawn as vector
  outlines, like many SongSelect exports) are still searchable by title and can be added to
  setlists, but are included exactly as they are. If a song exists as several such PDFs in
  different keys, name them `<slug>@<Key>.pdf` and the key menu picks between them.
- **Scanned sheets** (a picture, no text) can't be converted automatically. Write their `.cho` by
  hand from the picture and mark it `{meta: pdf scan}`: in the chart's key the setlist shows the
  scan itself, in any other key it's typeset from the `.cho`.
- **Word files and Google Docs** are treated exactly like PDFs: they're converted to PDF first
  (`doc2pdf.py`: LibreOffice for `.doc`/`.docx`, Drive's own export for Google Docs). A real PDF of
  the same name wins. Chords stay above the right words only if the document lines them up.
- **Replacing a song's chart** with a better PDF: `tools/replace_source.py` (see its docstring)
  builds the new `.cho` and puts the files to upload in `~/Downloads/drive-upload/`.
- To remove a song, delete all its files (in Drive, with only the `.cho` gone, its PDF is converted again).

### Library in a local folder

Set `LIBRARY_DIR=/path/to/library` in `.env`. Simplest to start with. New and changed `.cho`
files are picked up by themselves; a PDF needs its `.cho` made first (`pdf2chordpro.py` or the
`/convert` page). The trade-off is that adding a song means copying files onto the server. In
Docker, mount the folder into the container (e.g. `-v ~/songs:/app/library` with
`LIBRARY_DIR=/app/library`).

### Library in Google Drive

The team adds and fixes songs by uploading to Drive, and the site picks them up by itself.
Use two Drive folders, each shared as **Anyone with the link → Viewer**:

- a **`.cho` folder** for the ChordPro files (plus `_original_keys.csv` / `_exclude.txt`), and
- a **PDF folder** for the original PDFs (and Word files / Google Docs).

Files must sit directly in the folders; subfolders are ignored. `_original_keys.csv` may also be a
Google Sheet named `_original_keys`, for editing in Drive.

**1. Create a Google API key.** It's free and read-only, and only reads files shared as "anyone
with the link".

1. Go to <https://console.cloud.google.com/> and create a project (e.g. "songsheets").
2. **APIs & Services → Library**: search for **Google Drive API** and click **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key**.
4. Click the new key. Under **API restrictions**, choose **Restrict key → Google Drive API**, then save.

**2. Put the key and the folder links in `.env`:**

```bash
GOOGLE_API_KEY=AIza...
DRIVE_CHO_FOLDER_URL=https://drive.google.com/drive/folders/<cho folder id>
DRIVE_PDF_FOLDER_URL=https://drive.google.com/drive/folders/<pdf folder id>
```

(The defaults in `drive_mirror.py` are placeholders, so these are required. Don't put
`LIBRARY_DIR` in `.env` at the same time: it wins over Drive.)

**How the site reads Drive** (`drive_mirror.py`): it keeps a copy in `data/drive-library/` and
serves that. It checks Drive every `DRIVE_SYNC_MINUTES` (default 5), or straight away when someone
presses **Sync now** (next to the song count). Files are compared by Drive's checksum, so only new
and changed ones are downloaded. Downloads go at a gentle pace: Google blocks a burst of downloads
from one IP for a while (its "Sorry… automated queries" page). While blocked, the site keeps
serving its copy and the next sync carries on.

The site can only *read* Drive: a PDF it converts by itself keeps its `.cho` on the server. To
correct one, upload a fixed `<name>.cho` to the `.cho` folder, which then replaces it.

### Filling the library from an existing folder of charts

`import_drive.py` converts a whole folder tree of chord charts into a library folder in one go
(then upload the result to your Drive folders, or point `LIBRARY_DIR` at it). It:

- converts every chord-chart PDF to ChordPro, and Word files and Google Docs too (Word needs
  LibreOffice: run it in the Docker image or `brew install --cask libreoffice`),
- stores the original PDF next to it,
- skips sheet music with staff notation (transposing only the chord symbols would leave the
  notes in the old key) and files without chords,
- skips duplicate copies of the same file in different folders, and when a song has several
  charts keeps the best one (a PDF beats a Word file of the same chart),
- never overwrites existing library files unless you pass `--overwrite`, so re-running only adds
  new songs and your edits are safe.

From local folders (no API key, no rate limits):

```bash
.venv/bin/python import_drive.py --from-folder ~/Downloads/"Chinese Songs" --from-folder ~/Downloads/"English Songs"
```

Or straight from a shared Drive folder (needs `GOOGLE_API_KEY`):

```bash
DRIVE_FOLDER_URL='https://drive.google.com/drive/folders/<id>' .venv/bin/python import_drive.py --limit 20
.venv/bin/python import_drive.py            # everything
```

`DRIVE_INCLUDE` (a regex on top-level folder names) picks which folders to import. Then check
`library/_import_report.txt` for skipped or failed files. Scanned PDFs need
[ocrmypdf](https://ocrmypdf.readthedocs.io/) installed.

---

## Configuration

All settings are environment variables, read from `.env` (see `.env.example`). `deploy.sh` passes
`.env` to the container.

| Variable | Purpose | Default |
| --- | --- | --- |
| `LIBRARY_DIR` | Serve this local folder as the library (wins over Drive). | unset: Drive |
| `GOOGLE_API_KEY` | Reads the library from Google Drive. | required for Drive |
| `DRIVE_CHO_FOLDER_URL`, `DRIVE_PDF_FOLDER_URL` | The library's two Drive folders. | placeholders: required for Drive |
| `DRIVE_SYNC_MINUTES` | How often to check Drive for changes. | 5 |
| `DRIVE_CACHE_DIR` | Where the local copy of Drive is kept. | `data/drive-library` |
| `APP_PASSWORD` | Require a password for the whole site; the browser asks, any user name works. | no password |
| `STATS_PASSWORD` | Turns on the private `/stats` page (any user name). Events are recorded either way. | no stats page |
| `SETS_DB` | Sets, share links, drafts, capos. | `data/sets.db` |
| `EVENTS_DB` | Usage events. | `data/events.db` |
| `SHARE_LINK_BASE` | The address copied share links start with, e.g. a redirect on your church's site (`https://example.org/songlibrary`; the `#s=<code>` part survives the redirect). | this site's own address |
| `UNICODE_FONT` | A font file for Chinese/other non-Latin lyrics in uniform layout. | WenQuanYi in Docker, system fonts locally |
| `HOST`, `PORT` | Where `app.py` listens when run directly. | `127.0.0.1`, 5050 (8080 in Docker) |
| `DRIVE_FOLDER_URL`, `DRIVE_INCLUDE`, `DATA_DIR` | `import_drive.py`: the folder to import, which top-level folders, where it caches. | |
| `LIBRARY_MODE`, `SOURCES_SYNC_MINUTES`, `SOURCES_CACHE_DIR` | The three-source library (see [Making it yours](#making-it-yours)). | `mirror` |

---

## Deploy

The app is one Docker container. The image installs everything it needs: fonts for redrawing
chords (Liberation Sans, metric-compatible with Arial), WenQuanYi Micro Hei for Chinese lyrics,
`ocrmypdf` for scanned sheets and LibreOffice for Word files.

```bash
cp .env.example .env     # fill it in
./deploy.sh              # build the image and (re)start the "yfm" container
```

`deploy.sh` serves the site at <http://127.0.0.1:8080> only, and keeps `data/` (the Drive copy,
sets, stats) in the `yfm-data` Docker volume, so a redeploy doesn't lose sets or download the
library again. To update: `git pull && ./deploy.sh`.

To reach it from other devices, put something in front of port 8080, e.g.
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel), a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/),
or Caddy/nginx with a domain. If it's on the internet, set `APP_PASSWORD`.

Without the script, the equivalent is:

```bash
docker build -t songsheets .
docker run -d --name songsheets --restart unless-stopped -p 8080:8080 \
  --env-file .env -v songsheets-data:/app/data songsheets
```

Notes:

- **Keep a single worker process** (the `Dockerfile` does): the `/convert` page keeps uploads in
  memory for its previews, and saving a set checks its date is free under a lock that only works
  within one process.
- **Time zone**: the `Dockerfile` sets `TZ=Asia/Singapore`, which decides when a set becomes a past
  set (midnight local time). Change it to yours.
- **Backups**: everything that can't be rebuilt from the library is in `sets.db` (and `events.db`
  for stats). Copy them out with `docker cp yfm:/app/data/sets.db ./sets-backup.db`.
- `/healthz` answers `ok` without a password, for a host's health checks.

---

## Making it yours

### Replacing the church's name, logo and "YFM"

The app was built for the worship team ("YFM") of All Saints' Church (诸圣堂). Replace their
name and logo with yours:

**The logo and church name** (what people see in the top bar):

- `static/logo.svg`: the church's name drawn as a logo. Replace it with your own SVG (keep the
  file name). `static/nav.js` inlines it into the top bar at 30–34px high, and shapes inside a
  `<g class="ink">` are filled with the theme's text colour, so they work in light and dark. Or edit and run `tools/make_logo.py`, which draws the
  logo from the characters in its `for ch in "诸圣堂":` loop using a font on your machine.
- The `aria-label="诸圣堂 All Saints' Church"` on the logo in `static/index.html`,
  `static/setlist.html`, `static/sets.html` and `static/view.html` (what screen readers say).
- The `aria-label` inside `tools/make_logo.py`, if you use it.

**"YFM"** (the team's name):

- **Page titles and headings**, which people see: the `<title>` in `static/setlist.html`,
  `static/sets.html`, `static/view.html`, `static/stats.html` and `static/analytics.html`, the
  hidden `<h1>` in `static/setlist.html`, the `document.title` line in `static/view.html`, and the
  password prompt's `realm="YFM stats"` in `analytics.py`. A search for `YFM` in `static/` and
  `analytics.py` finds them all.
- **Internal names**, which nobody sees and are safe to leave: the `yfm` container and image and
  the `yfm-data` volume (`deploy.sh`), and the `yfm_name` cookie that remembers someone's name
  (`static/common.js`, `analytics.py`, `sets_api.py`). If you rename the cookie, rename it in all
  three files and in the tests, and people will have to type their name once more.
- **The three-source library**, which you can ignore (see below): "YFM" there is one of the three
  Drive sources, with its own colour in `static/setlist.html` (`--yfm`), and appears throughout
  its tests and `docs/`.

### Other things to change

- **Time zone**: `TZ` in the `Dockerfile` (see Deploy).

### Parts you can ignore

- **The three-source library** (`LIBRARY_MODE=sources`: `sources_library.py`, `catalog.py`,
  `charts.py`, `library_ids.py`, `library_report.py`, `CONTEXT.md`, `docs/`). It reads three
  teams' Drive folders directly, subfolders included, and works out songs and their arrangements
  by itself. The folder links in `sources_library.py` are placeholders. The default
  (`LIBRARY_MODE=mirror`, the two folders above) doesn't use any of it.
- **`history_import.py`**: imports a team's past services from a spreadsheet export (date, song,
  key, leader) as past sets. Its docstring explains the CSV format if you have a history to bring
  in.

---

## How it works

| File | What it does |
| --- | --- |
| `pdf2chordpro.py` | PDF → ChordPro. Uses `pdfplumber` character positions to line chords up with lyrics by x-coordinate (works with proportional fonts). Handles columns (even mixed layouts), headings, superscript chord extensions, `(C)` optional chords, `(Last x)` notes, repeated headers/footers, song-order lines and CCLI/copyright lines. Also a CLI. |
| `chordtools.py` | Key detection ([music21](https://web.mit.edu/music21/) Krumhansl–Schmuckler, with first/last chord tie-breaks), transposition (sharp/flat spelling follows the target key), section arrangement, uniform-layout PDF rendering. |
| `faithful.py` | Original-layout transposition: covers each chord on the page and draws the new one in the same place, in the sheet's own font if installed, otherwise Arial / Liberation Sans. |
| `setlist.py` | Builds the combined setlist PDF. Also detects sheet music. |
| `library.py` | Loads and searches the library's `.cho` files; picks the library from the environment. |
| `drive_mirror.py` | Keeps a local copy of the two Google Drive folders. |
| `drive.py`, `import_drive.py` | Google Drive client and crawler, and the one-time import. |
| `doc2pdf.py` | Word files and Google Docs as PDFs. |
| `app.py`, `pages.py`, `static/` | The Flask web app and its pages (plain HTML/JS, no build step). |
| `sets_api.py`, `sets_store.py` | Sets, share links, drafts and saved capos: the pages and API, and their SQLite store. |
| `analytics.py`, `static/track.js` | Usage events and the `/stats` page. |
| `song_stats.py` | The `/analytics` page. |
| `config.py` | Loads `.env`. |

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests drive the app through its HTTP API against a tiny song library they write into a temp
folder (and a fake Drive), so they need neither Google Drive nor real song sheets.

## Command line

```bash
.venv/bin/python pdf2chordpro.py song.pdf                  # writes song.cho next to it
.venv/bin/python pdf2chordpro.py songs/ -o out/            # a whole folder
.venv/bin/python faithful.py song.pdf C D                  # writes "song (D).pdf", original layout
```

## Limitations

- **Hidden old chords.** Original-layout PDFs cover the old chords with white boxes. The old chord
  text is still in the PDF's hidden text layer, so copy/paste finds both. Printing and viewing are
  correct.
- **Fonts.** If a sheet's font isn't installed on the server (common for embedded Type 3 fonts,
  e.g. SongSelect), new chords use Arial / Liberation Sans.
- **Sheet music.** Detection looks for five-line staves. Unusual engraving may slip through, so
  check the import report and remove anything that isn't a chord chart.
- **Copyright.** Most worship song sheets are copyrighted; keep them in your own Drive or server
  under your church's licence (e.g. CCLI), not in a public repo. `.gitignore` keeps `*.pdf` and
  `*.cho` out.
