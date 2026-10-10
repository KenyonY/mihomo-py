"""Synchronize the versions of the main and optional Web distributions."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "core_project": ROOT / "pyproject.toml",
    "core_runtime": ROOT / "src/mihomo_py/__init__.py",
    "web_project": ROOT / "web/pyproject.toml",
    "web_runtime": ROOT / "web/src/mihomo_py_web/__init__.py",
}
VERSION_RE = re.compile(r"^0\.[0-9]+\.[0-9]+$")
PATTERNS = {
    "core_project": (r'(?m)^(version\s*=\s*)"[^"]+"$', 'version = "{version}"'),
    "core_runtime": (r'(?m)^(__version__\s*=\s*)"[^"]+"$', '__version__ = "{version}"'),
    "web_project": (r'(?m)^(version\s*=\s*)"[^"]+"$', 'version = "{version}"'),
    "web_runtime": (r'(?m)^(__version__\s*=\s*)"[^"]+"$', '__version__ = "{version}"'),
}
WEB_DEPENDENCY_RE = re.compile(r'(?m)^web\s*=\s*\["mihomo-py-web==([^" ]+)"')


def read_versions() -> dict[str, str]:
    versions = {}
    for key, path in FILES.items():
        text = path.read_text()
        match = re.search(PATTERNS[key][0], text)
        if not match:
            raise SystemExit(f"cannot find version assignment in {path}")
        versions[key] = text[match.start():match.end()].split('"', 1)[1].rsplit('"', 1)[0]
    core_text = FILES["core_project"].read_text()
    dependency = WEB_DEPENDENCY_RE.search(core_text)
    if not dependency:
        raise SystemExit(f"cannot find the mihomo-py-web dependency in {FILES['core_project']}")
    if dependency.group(1) != versions["web_project"]:
        raise SystemExit(
            "version mismatch: pyproject.toml pins "
            f"mihomo-py-web=={dependency.group(1)}, web/pyproject.toml is {versions['web_project']}"
        )
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{key}={value}" for key, value in versions.items())
        raise SystemExit(f"version mismatch: {details}")
    return versions


def next_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def update(version: str) -> None:
    for key, path in FILES.items():
        text = path.read_text()
        pattern, replacement = PATTERNS[key]
        updated, count = re.subn(pattern, replacement.format(version=version), text)
        if count != 1:
            raise SystemExit(f"expected one version assignment in {path}, found {count}")
        path.write_text(updated)
    core_path = FILES["core_project"]
    core_text = core_path.read_text()
    updated, count = re.subn(
        r'(?m)^(web\s*=\s*\["mihomo-py-web==)[^" ]+(".*)$',
        rf'\g<1>{version}\g<2>',
        core_text,
    )
    if count != 1:
        raise SystemExit(f"expected one mihomo-py-web dependency in {core_path}, found {count}")
    core_path.write_text(updated)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="target version; defaults to the next patch version")
    parser.add_argument("--check", action="store_true", help="check that all version sources agree")
    args = parser.parse_args()
    versions = read_versions()
    current = next(iter(versions.values()))
    if args.check:
        print(current)
        return
    target = args.version or next_patch(current)
    if not VERSION_RE.fullmatch(target):
        raise SystemExit("version must use MAJOR.MINOR.PATCH, for example 0.1.6")
    if target == current:
        raise SystemExit(f"version is already {target}")
    update(target)
    print(f"updated {current} -> {target} in {len(FILES)} files")


if __name__ == "__main__":
    main()
