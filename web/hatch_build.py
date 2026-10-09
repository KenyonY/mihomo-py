"""Validate pinned dashboard assets without downloading during builds."""

import hashlib
import json
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
