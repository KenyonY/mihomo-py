"""Maintainer-only asset restoration; pip/build/runtime never invoke this script."""

import argparse
import gzip
import hashlib
import json
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", action="store_true", help="Restore optional dashboard resources.")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    root = repository / ("web/src/mihomo_py_web/_vendor" if args.web else "src/mihomo_py/_vendor")
    manifest = json.loads((root / "manifest.json").read_text())
    for asset in manifest["assets"]:
        path = root / asset["path"]
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]:
            continue
        with urllib.request.urlopen(asset["url"], timeout=120) as response:
            raw = response.read()
        if hashlib.sha256(raw).hexdigest() != asset["download_sha256"]:
            raise ValueError(f"Download checksum mismatch: {asset['path']}")
        content = gzip.decompress(raw) if asset.get("gzip") else raw
        if hashlib.sha256(content).hexdigest() != asset["sha256"]:
            raise ValueError(f"Asset checksum mismatch: {asset['path']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_bytes(content)
            temporary.chmod(0o755 if path.name == "mihomo" else 0o644)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        print(asset["path"])


if __name__ == "__main__":
    main()
