"""Download and unpack the offline Jamdict SQLite database into data/jamdict.db."""

from __future__ import annotations

import lzma
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
JAMDICT_DB_PATH = DATA_DIR / "jamdict.db"
PYPI_META_URL = "https://pypi.org/pypi/jamdict-data/json"


def ensure_jamdict_db(force: bool = False) -> Path:
    """Ensure data/jamdict.db exists and is non-empty, downloading if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not force and JAMDICT_DB_PATH.is_file() and JAMDICT_DB_PATH.stat().st_size > 10 * 1024 * 1024:
        print(f"Jamdict database already present: {JAMDICT_DB_PATH}")
        return JAMDICT_DB_PATH

    print("Fetching jamdict-data release metadata from PyPI...")
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        meta_resp = client.get(PYPI_META_URL)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        sdist_url = None
        for file_info in meta.get("urls", []):
            if file_info.get("packagetype") == "sdist":
                sdist_url = file_info["url"]
                break
        if not sdist_url:
            raise RuntimeError("Could not find jamdict-data sdist URL on PyPI.")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tar_path = tmp / "jamdict-data.tar.gz"
            xz_path = tmp / "jamdict.db.xz"
            out_tmp = tmp / "jamdict.db"

            print(f"Downloading {sdist_url}...")
            with client.stream("GET", sdist_url) as dl:
                dl.raise_for_status()
                with tar_path.open("wb") as f:
                    for chunk in dl.iter_bytes(chunk_size=1024 * 256):
                        f.write(chunk)

            print("Extracting jamdict.db.xz from archive...")
            with tarfile.open(tar_path, "r:gz") as tf:
                member = next(
                    (m for m in tf.getmembers() if m.name.endswith("jamdict.db.xz")),
                    None,
                )
                if member is None:
                    raise RuntimeError("jamdict.db.xz not found inside jamdict-data sdist.")
                src = tf.extractfile(member)
                if src is None:
                    raise RuntimeError("Failed to read jamdict.db.xz from tar archive.")
                with src, xz_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

            print("Decompressing jamdict.db...")
            with lzma.open(xz_path, "rb") as compressed, out_tmp.open("wb") as dst:
                shutil.copyfileobj(compressed, dst)

            shutil.move(str(out_tmp), str(JAMDICT_DB_PATH))

    print(f"Saved offline dictionary database to {JAMDICT_DB_PATH}")
    return JAMDICT_DB_PATH


if __name__ == "__main__":
    ensure_jamdict_db()
