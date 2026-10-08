"""An in-memory Google Drive, standing in for drive.DriveClient wherever the site reads Drive.

Folders nest; files have Drive IDs, MD5 checksums and modified times. Google Docs export as
PDF and Google Sheets as CSV. Word files hold a PDF in disguise (see word_to_pdf), since
LibreOffice isn't around in tests. `blocked = True` makes downloads fail the way Google's
"automated queries" page does, while listing keeps working, as it does for real.
"""

import hashlib
import itertools
from datetime import datetime, timedelta

from doc2pdf import GDOC
from drive import FOLDER, RateLimited
from drive_mirror import GSHEET
WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WORD_MAGIC = b"FAKE-WORD\n"


def word_to_pdf(data, name="document.docx"):
    """The stand-in for LibreOffice: a fake Word file is a PDF with a marker in front."""
    assert data.startswith(WORD_MAGIC), f"{name} isn't a fake Word file"
    return data[len(WORD_MAGIC):]


class FakeDrive:
    def __init__(self):
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)
        self.items = {}           # id -> {"id", "name", "mimeType", "parent", "content", ...}
        self.blocked = False
        self.downloads = []       # ids downloaded or exported, in order

    # ------------------------------------------------------------ setting Drive up
    def _stamp(self):
        """A modified time later than every earlier one."""
        return (datetime(2026, 9, 1) + timedelta(seconds=next(self._clock))).isoformat() + ".000Z"

    def _add(self, parent, name, mime, content=None):
        item_id = f"id{next(self._ids)}"
        self.items[item_id] = {"id": item_id, "name": name, "mimeType": mime, "parent": parent,
                               "content": content, "modifiedTime": self._stamp()}
        return item_id

    def folder(self, name, parent=None):
        """A folder (top-level with no parent); returns its ID."""
        return self._add(parent, name, FOLDER)

    def path(self, *names):
        """The ID of the folder at names[0]/names[1]/..., made as needed."""
        parent = None
        for name in names:
            found = [i for i, f in self.items.items()
                     if f["parent"] == parent and f["name"] == name and f["mimeType"] == FOLDER]
            parent = found[0] if found else self.folder(name, parent)
        return parent

    def url(self, folder_id):
        return f"https://drive.google.com/drive/folders/{folder_id}"

    def upload(self, parent, name, content, mime=None):
        """A file, as someone dropping it into a folder would; returns its ID."""
        if isinstance(content, str):
            content = content.encode()
        mime = mime or ("application/pdf" if name.lower().endswith(".pdf") else
                        WORD if name.lower().endswith((".doc", ".docx")) else "text/plain")
        if mime == WORD:
            content = WORD_MAGIC + content
        return self._add(parent, name, mime, content)

    def google_doc(self, parent, name, pdf):
        """A Google Doc, which exports as `pdf`."""
        return self._add(parent, name, GDOC, pdf)

    def sheet(self, parent, name, csv):
        """A Google Sheet, which exports as `csv` (text)."""
        return self._add(parent, name, GSHEET, csv.encode())

    def replace(self, file_id, content):
        """A new version of a file: new contents, checksum and modified time; same ID."""
        f = self.items[file_id]
        if isinstance(content, str):
            content = content.encode()
        f["content"] = WORD_MAGIC + content if f["mimeType"] == WORD else content
        f["modifiedTime"] = self._stamp()

    def rename(self, file_id, name):
        self.items[file_id]["name"] = name

    def move(self, file_id, parent):
        self.items[file_id]["parent"] = parent

    def delete(self, file_id):
        del self.items[file_id]

    # ------------------------------------------------------------ what the site calls
    def children(self, folder_id, rk=None):
        out = []
        for f in self.items.values():
            if f["parent"] != folder_id:
                continue
            listed = {k: f[k] for k in ("id", "name", "mimeType", "modifiedTime")}
            if f["mimeType"] not in (FOLDER, GDOC, GSHEET):
                listed["md5Checksum"] = hashlib.md5(f["content"]).hexdigest()
                listed["size"] = str(len(f["content"]))
            out.append(listed)
        return out

    def _file(self, file_id):
        if self.blocked:
            raise RateLimited("Google is rate-limiting downloads; try again later (re-running resumes).")
        self.downloads.append(file_id)
        return self.items[file_id]

    def download(self, file_id, rk=None):
        f = self._file(file_id)
        assert f["mimeType"] not in (FOLDER, GDOC, GSHEET), "Docs and Sheets can only be exported"
        return f["content"]

    def export(self, file_id, mime, rk=None):
        f = self._file(file_id)
        assert (f["mimeType"], mime) in ((GDOC, "application/pdf"), (GSHEET, "text/csv")), (f["mimeType"], mime)
        return f["content"]
