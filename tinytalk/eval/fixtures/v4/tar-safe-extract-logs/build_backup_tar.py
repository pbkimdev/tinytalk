from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path


ENTRIES = {
    "var/log/a.log": b"archive a log should not overwrite sentinel\n",
    "var/log/b.log": b"new log from backup\n",
    "var/log/notes.txt": b"not a log\n",
    "etc/other.conf": b"outside log scope\n",
}


def main() -> None:
    target = Path(__file__).with_name("input") / "backup.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in ENTRIES.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(content))

    with target.open("wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
            gz.write(raw.getvalue())


if __name__ == "__main__":
    main()
