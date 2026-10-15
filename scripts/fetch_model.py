#!/usr/bin/env python3
"""Download the embedding model into MODELS_DIR and check its SHA-256. Standard library only.

Reads MODELS_DIR, EMBED_MODEL_FILE, EMBED_MODEL_URL and EMBED_MODEL_SHA256 from .env.
Safe to run twice: a file that already matches is left alone. A file that does not match is
never kept.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

from envfile import ROOT, parse_env


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not (ROOT / ".env").exists():
        print("FAIL: .env is missing. Run `make env-init`.")
        return 1
    env = parse_env()
    try:
        models_dir = (ROOT / env["MODELS_DIR"]).resolve()
        name, url, want = env["EMBED_MODEL_FILE"], env["EMBED_MODEL_URL"], env["EMBED_MODEL_SHA256"].lower()
    except KeyError as e:
        print(f"FAIL: {e.args[0]} is not set in .env")
        return 1

    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / name

    if target.exists():
        if sha256_of(target) == want:
            print(f"OK: {name} is already present and matches the expected SHA-256")
            return 0
        print(f"{name} exists but does not match. Downloading again.")
        target.unlink()

    part = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {name} ...")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done // (1 << 20)} / {total // (1 << 20)} MB", end="", flush=True)
        print()
    except OSError as e:
        part.unlink(missing_ok=True)
        print(f"FAIL: download failed: {e}")
        return 1

    got = sha256_of(part)
    if got != want:
        part.unlink(missing_ok=True)
        print("FAIL: the downloaded file does not match EMBED_MODEL_SHA256. It was deleted.")
        print(f"  expected {want}\n  got      {got}")
        return 1
    part.rename(target)
    print(f"OK: {name} downloaded and verified (SHA-256 {got})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
