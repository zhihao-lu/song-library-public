# Switching to the three-source library

Today the site serves the **mirror**: the `.cho Files` folder and the flat PDF folder. The
**three-source library** reads YFM, ASE and MM directly (see `sources_library.py`,
`docs/adr/0001` and `0002`). One setting, `LIBRARY_MODE`, picks which one the site serves.
Each keeps its own local copy in the `yfm-data` volume, so switching either way is a restart
and downloads nothing again.

Do the steps in order. Steps 1–3 change Drive; the site doesn't mind, since the mirror only
reads `.cho Files` and the flat folder.

## 0. Deploy the new code, still on the mirror

```bash
./deploy.sh        # .env has no LIBRARY_MODE yet: the site serves the mirror, as before
```

## 1. Upload the fixed charts

- The 260 fixed `.cho` files in `~/Downloads/cho-fixes/cho Files/` → upload into `.cho Files`,
  choosing **Replace**.
- `~/Downloads/drive-upload/gsnxy-跟随祢心意-guitar-f.cho` (its intro, now as chords) → the same.

Press **Sync now**. In the new library, every `.cho` in `.cho Files` is a hand-fixed chart of
the song it's named after, so these fixes carry over.

## 2. Create the corrections Sheet

Follow "Creating the Sheet" in `docs/corrections.md` (`tools/corrections_sheet.py` writes
today's original keys and exclude list as its first rows).

## 3. Run the test run and read its report

```bash
docker exec yfm python library_report.py
docker cp yfm:/app/data/test-run/report.html ~/Downloads/library-test-run.html
open ~/Downloads/library-test-run.html
```

The first run takes a while: it lists about 450 folders (about 8 minutes) and takes in about
5,000 files. Files the mirror already has are copied, not downloaded, and the rest download at
the mirror's gentle pace. If Google pauses downloads, the report says the run didn't finish:
run it again later and it carries on. It never changes what the site serves.

Work through the report once:

- **Only in the flat PDF folder**: move each file into a song folder in YFM's resource folder
  (moving keeps its Drive ID, so saved sets keep opening it).
- **Songs missing**: move the file in, or add a `same_as` row.
- **New arrangements** that are really wrong matches: a `different_song` row; ones worth
  naming: a `label` row.
- **Skipped**: check nothing that should be a chart is there.

Run the test run again until the report reads right.

## 4. Switch

```bash
echo LIBRARY_MODE=sources >> .env
./deploy.sh
```

The first start takes over the test run's copy, so the library is there at once; the first
hourly check then runs in the background. Open a few saved sets and share links: they should
show the same songs. **Sync now** works as before; the sources are checked every hour
(`SOURCES_SYNC_MINUTES` to change it).

## 5. Try a rollback once

```bash
sed -i '' 's/^LIBRARY_MODE=sources$/LIBRARY_MODE=mirror/' .env && ./deploy.sh   # back to the mirror
sed -i '' 's/^LIBRARY_MODE=mirror$/LIBRARY_MODE=sources/' .env && ./deploy.sh   # and forward again
```

Sets saved while on the three-source library may name arrangements the mirror doesn't have;
the mirror shows those as no longer in the library until you switch forward again.

## Later

Once the three-source library has run cleanly for a while, retire the flat PDF folder and the
mirror code (`drive_mirror.py`, `LIBRARY_MODE=mirror`).
