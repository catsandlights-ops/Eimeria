"""Update checker and installer.

How updates are published (see tools/make_release.py):
  - The GitHub repo's main branch has `update.json`: {"version", "tag", "date", "notes": [...]}
  - Each version is a git tag (e.g. v1.1.0). GitHub serves every tag as a zip archive.
  - Every release contains MANIFEST.sha256: a fingerprint of every file. After downloading, each file
    is checked against it, so a damaged, incomplete or tampered download is rejected.

How an update is installed:
  1. Download + verify into a staging folder.
  2. Eimeria starts a small helper (apply_update.py, copied to a temp folder) and closes itself.
  3. The helper waits for Eimeria to exit, backs up the current app files, copies in the new ones,
     reinstalls Python packages if requirements.txt changed, and restarts Eimeria.
  4. If anything fails, it restores the backup.
Never touched: settings.yaml, .env (keys), trader.db (trade history), cache/, logs/, backups/, .venv/.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

from . import config
from .version import VERSION

log = logging.getLogger(__name__)

# Files and folders that belong to the user, not the app: never replaced or deleted by an update.
PROTECTED = {"settings.yaml", ".env", "trader.db", "trader.db-journal", "cache", "logs", "backups", ".venv",
             "venv", "updates", ".selftest_write", "dist", "build", "__pycache__", ".git", ".pytest_cache"}
MANIFEST = "MANIFEST.sha256"


def parse_version(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))[:3]


def is_newer(remote: str, local: str = VERSION) -> bool:
    return parse_version(remote) > parse_version(local)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def app_files(root: Path) -> list[Path]:
    """Every file that belongs to the app (relative paths), skipping user data and caches."""
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if rel.parts[0] in PROTECTED or "__pycache__" in rel.parts or p.is_dir():
            continue
        if rel.name == MANIFEST or rel.suffix in (".pyc", ".db"):
            continue
        out.append(rel)
    return out


def write_manifest(root: Path) -> Path:
    lines = [f"{file_sha256(root / rel)}  {rel.as_posix()}" for rel in app_files(root)]
    path = root / MANIFEST
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def verify_manifest(root: Path) -> list[str]:
    """Returns a list of problems (empty = every file matches its fingerprint)."""
    m = root / MANIFEST
    if not m.exists():
        return ["The update has no MANIFEST.sha256 file."]
    problems = []
    for line in m.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        f = root / rel
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            problems.append(f"Unsafe path in manifest: {rel}")
        elif not f.exists():
            problems.append(f"Missing file: {rel}")
        elif file_sha256(f) != digest:
            problems.append(f"File doesn't match its fingerprint: {rel}")
    return problems


class Updater:
    def __init__(self, settings: dict):
        self.cfg = settings.get("updates", {})

    @property
    def repo(self) -> str:
        return self.cfg.get("repo", "")

    def check(self) -> dict | None:
        """Returns the update info if a newer version is published, else None."""
        if not self.repo:
            return None
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.cfg.get('branch', 'main')}/update.json"
        r = requests.get(url, timeout=20, headers={"Cache-Control": "no-cache"})
        if r.status_code == 404:
            return None                      # nothing published yet
        r.raise_for_status()
        info = r.json()
        if not isinstance(info, dict) or "version" not in info or "tag" not in info:
            raise ValueError("update.json is malformed")
        return info if is_newer(info["version"]) else None

    def download(self, info: dict, progress=lambda m: None) -> Path:
        """Download and verify a release. Returns the staging folder containing the new app files."""
        tag = info["tag"]
        url = f"https://github.com/{self.repo}/archive/refs/tags/{tag}.zip"
        progress(f"Downloading Eimeria {info['version']}...")
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        staging_root = Path(tempfile.mkdtemp(prefix="eimeria_update_"))
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for member in z.namelist():                 # refuse archives that write outside the folder
                if member.startswith("/") or ".." in Path(member).parts:
                    raise ValueError(f"Unsafe file in update archive: {member}")
            z.extractall(staging_root)
        tops = [p for p in staging_root.iterdir() if p.is_dir()]
        if len(tops) != 1:
            raise ValueError("Unexpected update archive layout")
        staging = tops[0]
        progress("Verifying files...")
        problems = verify_manifest(staging)
        if problems:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise ValueError("The download failed verification and was NOT installed:\n" + "\n".join(problems[:10]))
        return staging

    def launch_installer(self, staging: Path, restart: bool = True) -> None:
        """Start the helper that installs the update after Eimeria exits. The caller must then quit."""
        helper_dir = Path(tempfile.mkdtemp(prefix="eimeria_helper_"))
        helper = helper_dir / "apply_update.py"
        shutil.copy2(Path(__file__).with_name("apply_update.py"), helper)
        args = [python_for_helper(), str(helper), "--source", str(staging), "--target", str(config.BASE_DIR),
                "--wait-pid", str(_pid()), "--from-version", VERSION]
        if restart:
            args.append("--restart")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(args, creationflags=flags, close_fds=True, cwd=str(helper_dir))

    def backups(self) -> list[Path]:
        root = config.BASE_DIR / "backups"
        return sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.exists() else []

    def launch_rollback(self, backup: Path) -> None:
        self.launch_installer(backup, restart=True)


def python_for_helper() -> str:
    """The console Python next to the running one (pythonw has no console; either works for the helper)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def can_self_update() -> bool:
    """Self-update works for the normal install (run.bat). A PyInstaller .exe can't replace itself."""
    return not getattr(sys, "frozen", False)


def _pid() -> int:
    import os
    return os.getpid()


def write_update_json(root: Path, version: str, notes: list[str], date_str: str) -> Path:
    p = root / "update.json"
    p.write_text(json.dumps({"version": version, "tag": f"v{version}", "date": date_str, "notes": notes},
                            indent=2) + "\n", encoding="utf-8")
    return p
