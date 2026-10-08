"""Replace a library song's source chart with a better PDF (e.g. a SongSelect chord chart).

    .venv/bin/python tools/replace_source.py <slug> <new.pdf> [--key F] [--preview-dir DIR] [--out DIR]

- Reads the song from the Google Drive library (synced into data/drive-library).
- Converts the PDF to ChordPro with pdf2chordpro, keeping the library's own metadata
  (title, source_folder, original_key, ...) from the old .cho.
- Keeps the PDF untouched as <slug>.pdf (the "original layout" setlists use).
  Never store a PDF that was already transposed with faithful: it can't be transposed again.
- The chart key becomes the new PDF's key (updated in the .cho and in _original_keys.csv).
  The slug stays the same, so setlists and links keep working.
- Renders previews (chart key, original key, uniform layout) as PNGs to eyeball.
- Writes the files to upload to --out (default ~/Downloads/drive-upload):
  "cho Files/" (<slug>.cho, _original_keys.csv) and "PDF Files/" (<slug>.pdf).
  Upload them over the old ones in Drive, then press "Sync now" on the site.

The .cho is a draft: check section order, wrapped lines and bar lines before uploading.
"""

import argparse
import csv
import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pdfplumber  # noqa: E402

import config  # noqa: E402,F401  (GOOGLE_API_KEY from .env)
import library as lib  # noqa: E402
import setlist  # noqa: E402
from pdf2chordpro import convert_file  # noqa: E402

DIRECTIVE = re.compile(r"^\{\s*([\w-]+)\s*(?::\s*(.*?))?\s*\}$")
# Library-only metadata worth carrying over from the old .cho.
KEEP_META = ("source_folder", "original_key", "transpose", "lyrics")


def header_and_body(cho):
    """Split into the leading metadata directives and the song body."""
    lines = cho.splitlines()
    for i, ln in enumerate(lines):
        m = DIRECTIVE.match(ln.strip())
        if ln.strip() and (not m or m.group(1) in ("comment", "c") or m.group(1).startswith("start_of")):
            return lines[:i], lines[i:]
    return lines, []


def meta_value(lines, name):
    for ln in lines:
        m = DIRECTIVE.match(ln.strip())
        if not m:
            continue
        if m.group(1) == name:
            return m.group(2)
        if m.group(1) == "meta" and m.group(2) and m.group(2).split(" ", 1)[0] == name:
            return m.group(2).split(" ", 1)[1] if " " in m.group(2) else ""
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--key", help="the new PDF's key, if it isn't detected")
    ap.add_argument("--preview-dir", type=Path, default=Path("/tmp"))
    ap.add_argument("--out", type=Path, default=Path.home() / "Downloads" / "drive-upload",
                    help="where to put the files to upload to Drive")
    args = ap.parse_args()

    drive_lib = lib.SongLibrary()
    if not drive_lib.get(args.slug):
        sys.exit(f"no such song in the Drive library: {args.slug}")
    old_head, _ = header_and_body(drive_lib.cho_path(args.slug).read_text(encoding="utf-8"))

    # Build the new files in a scratch library of their own, for the previews.
    work = Path(tempfile.mkdtemp(prefix="replace-source-"))
    cho_path, pdf_dest, csv_path = work / f"{args.slug}.cho", work / f"{args.slug}.pdf", work / "_original_keys.csv"
    shutil.copyfile(drive_lib.folder / "_original_keys.csv", csv_path)

    conv = convert_file(args.pdf, argparse.Namespace(
        ocr=False, columns="auto", title=None, key=args.key, no_guess_key=False, no_snap=False))
    new_head, body = header_and_body(conv)
    key = args.key or meta_value(new_head, "key")
    if not key:
        sys.exit("couldn't detect the new PDF's key; pass --key")

    head = [f"{{title: {meta_value(old_head, 'title') or meta_value(new_head, 'title')}}}",
            f"{{meta: source_file {args.pdf.name}}}"]
    head += [f"{{meta: {name} {v}}}" for name in KEEP_META if (v := meta_value(old_head, name))]
    for ln in new_head:
        m = DIRECTIVE.match(ln.strip())
        if m and m.group(1) not in ("title",) and ln.strip():
            head.append(ln.strip())
    if meta_value(old_head, "pdf_pages"):
        print("note: old .cho had pdf_pages (page range of the OLD pdf); dropped - re-add if needed")
    cho_path.write_text("\n".join(head) + "\n\n" + "\n".join(body).strip() + "\n", encoding="utf-8")
    shutil.copyfile(args.pdf, pdf_dest)

    # Chart key in the team CSV.
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        fields, rows = rd.fieldnames, list(rd)
    for r in rows:
        if r["slug"] == args.slug:
            r["chart_key"] = key
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    # Previews.
    library = lib.SongLibrary(work)
    song = library.get(args.slug)
    print(f"chart key {song['key']}, original key {song['original_key']}")
    print("sections:", " > ".join(re.findall(r"\{(?:comment|start_of_\w+): ([^}]+)\}", cho_path.read_text())))
    args.preview_dir.mkdir(parents=True, exist_ok=True)
    targets = {(song["key"], "original"), (song["original_key"] or song["key"], "original"),
               (song["original_key"] or song["key"], "uniform")}
    for to, layout in sorted(targets):
        data, k = setlist.render_song(library, args.slug, to, layout)
        out = args.preview_dir / f"{args.slug}_{layout}_{k}.png"
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pdf.pages[0].to_image(resolution=90).save(out)
        print("preview:", out)

    for sub, files in (("cho Files", (cho_path, csv_path)), ("PDF Files", (pdf_dest,))):
        (args.out / sub).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(f, args.out / sub / f.name)
    print(f"To upload: {args.out}/cho Files -> Drive's cho folder, {args.out}/PDF Files -> the PDF folder "
          "(replace the old files), then press Sync now on the site.")


if __name__ == "__main__":
    main()
