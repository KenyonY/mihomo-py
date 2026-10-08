"""Build self-contained Linux distributions. Never download during a build."""

import hashlib
import json
import os
import platform
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BundleHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root) / "src/mihomo_py/_vendor"
        manifest = json.loads((root / "manifest.json").read_text())
        arch = os.environ.get("MIHOMO_BUILD_ARCH", platform.machine())
        if self.target_name == "wheel" and (
            sys.platform != "linux" or arch not in ("x86_64", "aarch64")
        ):
            raise ValueError("mihomo-py supports Linux x86_64 and aarch64 only")
        for asset in manifest["assets"]:
            name = asset["path"]
            if self.target_name == "wheel" and name.endswith("/mihomo"):
                if name != f"{arch}/mihomo":
                    continue
            path = root / name
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]
            ):
                raise ValueError(f"Missing or corrupt bundled asset: {name}; restore vendor files")
            if name.endswith("/mihomo") and not os.access(path, os.X_OK):
                raise ValueError(f"Bundled core must be executable: {name}")
            if self.target_name == "wheel":
                build_data["force_include"][str(path)] = f"mihomo_py/_vendor/{name}"
        if self.target_name == "wheel":
            for name in ("manifest.json", "NOTICE.txt"):
                build_data["force_include"][str(root / name)] = f"mihomo_py/_vendor/{name}"
            # Upstream CGO_ENABLED=0 cores are static ELF executables, independent of Python ABI.
            build_data["pure_python"] = False
            build_data["tag"] = f"py3-none-manylinux_2_17_{arch}.musllinux_1_2_{arch}"
