"""Validate pinned dashboard assets without downloading during builds."""

import hashlib
import json
import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DashboardHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root) / "src/mihomo_py_web/_vendor"
        manifest = json.loads((root / "manifest.json").read_text())
        for asset in manifest["assets"]:
            path = root / asset["path"]
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]
            ):
                raise ValueError(f"Missing or corrupt dashboard asset: {asset['path']}")
        portal = root.parent / "portal"
        index = portal / "index.html"
        if not index.is_file() or not (portal / "THIRD_PARTY_NOTICES.txt").is_file():
            raise ValueError(
                "Missing subscription portal; run npm ci && npm run build in web/frontend"
            )
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.read_text())
        if not assets or any(not (portal / path.lstrip("/")).is_file() for path in assets):
            raise ValueError("Missing subscription portal assets; rebuild web/frontend")
