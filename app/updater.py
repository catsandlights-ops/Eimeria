"""Update checker and installer.

How updates are published (see tools/make_release.py):
  - The GitHub repo's release branch (default `claude/releases`) has `update.json`:
    {"version", "commit", "date", "notes": [...]}  ("tag" is also accepted instead of "commit").
  - "commit" is the exact git commit of that version. GitHub serves any commit as a zip archive,
    and a commit ID is a fingerprint of the whole version: it can't be changed after the fact.
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
import re
import shutil
import subprocess  # nosec B404 - used with fixed argument lists, never a shell
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
TAG_PATTERN = re.compile(r"^v\d{1,4}\.\d{1,4}\.\d{1,4}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
POINTER_FILES = {"update.json"}             # changes after a release is cut, so not in the fingerprint list
# Source release ~0.5 MB; Windows build (Python + Qt inside) ~150-250 MB. Refuse anything absurd.
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024
MAX_UNPACKED_BYTES = 1500 * 1024 * 1024      # guards against "zip bombs"
MAX_FILES = 20000


def is_frozen() -> bool:
    """True when running as the packaged Windows Eimeria.exe."""
    return bool(getattr(sys, "frozen", False))


def windows_asset_url(repo: str, version: str) -> str:
    """The Windows build is attached to a GitHub Release named v<version> by the build workflow."""
    return f"https://github.com/{repo}/releases/download/v{version}/Eimeria-windows-{version}.zip"


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
        if rel.name == MANIFEST or rel.suffix == ".db" or rel.as_posix() in POINTER_FILES:
            continue
        out.append(rel)
    return out


def archive_ref(info: dict) -> str:
    """The git ref to download for an update.json entry: a commit ID (preferred) or a version tag."""
    commit, tag = str(info.get("commit", "")), str(info.get("tag", ""))
    if commit:
        if not COMMIT_PATTERN.match(commit):
            raise ValueError(f"update.json has an invalid commit id: {commit!r}")
        return commit
    if TAG_PATTERN.match(tag) and tag.lstrip("v") == str(info.get("version", "")).lstrip("v"):
        return f"refs/tags/{tag}"
    raise ValueError(f"update.json has no valid commit or version tag ({tag!r})")


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
        norm = rel.replace("\\", "/")
        if ".." in Path(norm).parts or Path(norm).is_absolute() or norm.startswith("/") or ":" in norm:
            problems.append(f"Unsafe path in manifest: {rel}")
            continue
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
        branch = self.cfg.get("branch", "claude/releases")
        url = f"https://raw.githubusercontent.com/{self.repo}/refs/heads/{branch}/update.json"
        r = requests.get(url, timeout=20, headers={"Cache-Control": "no-cache"})
        if r.status_code == 404:
            return None                      # nothing published yet
        r.raise_for_status()
        info = r.json()
        if not isinstance(info, dict) or "version" not in info:
            raise ValueError("update.json is malformed")
        if not re.match(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$", str(info["version"])):
            raise ValueError(f"update.json has an invalid version: {info['version']!r}")
        archive_ref(info)                                 # raises if neither commit nor tag is valid
        if is_frozen() and is_newer(info["version"]):
            # The Windows build is made on GitHub a few minutes after a release is published:
            # only offer the update once it's actually there.
            h = requests.head(windows_asset_url(self.repo, info["version"]), timeout=20, allow_redirects=True)
            if h.status_code != 200:
                log.info("Update %s published, Windows build not ready yet", info["version"])
                return None
        if not isinstance(info.get("notes", []), list):
            raise ValueError("update.json notes must be a list")
        return info if is_newer(info["version"]) else None

    def download(self, info: dict, progress=lambda m: None) -> Path:
        """Download and verify a release. Returns the staging folder containing the new app files."""
        url = windows_asset_url(self.repo, str(info["version"])) if is_frozen() else \
            f"https://github.com/{self.repo}/archive/{archive_ref(info)}.zip"
        progress(f"Downloading Eimeria {info['version']}...")
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        if len(r.content) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Update download is unexpectedly large ({len(r.content):,} bytes); refused.")
        staging_root = Path(tempfile.mkdtemp(prefix="eimeria_update_"))
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            infos = z.infolist()
            if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise ValueError("Update archive is unexpectedly large when unpacked; refused.")
            for member in z.namelist():                 # refuse archives that write outside the folder
                norm = member.replace("\\", "/")
                if norm.startswith("/") or ".." in Path(norm).parts or re.match(r"^[A-Za-z]:", norm):
                    raise ValueError(f"Unsafe file in update archive: {member}")
            z.extractall(staging_root)
        tops = [p for p in staging_root.iterdir() if p.is_dir()]
        if len(tops) != 1:
            raise ValueError("Unexpected update archive layout")
        staging = tops[0]
        progress("Verifying files...")
        problems = verify_manifest(staging)
        got = _read_version(staging)
        if got != str(info["version"]):
            problems.append(f"The download is version {got}, but version {info['version']} was advertised.")
        if problems:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise ValueError("The download failed verification and was NOT installed:\n" + "\n".join(problems[:10]))
        return staging

    def launch_installer(self, staging: Path, restart: bool = True) -> None:
        """Start the helper that installs the update after Eimeria exits. The caller must then quit.
        Windows .exe: the NEW version's own Eimeria.exe (in the staging folder) does the installing.
        Normal install: a copy of apply_update.py run by the app's Python."""
        common = ["--source", str(staging), "--target", str(config.BASE_DIR),
                  "--wait-pid", str(_pid()), "--from-version", VERSION]
        if is_frozen():
            exe = staging / "Eimeria.exe"
            if not exe.exists():
                raise FileNotFoundError(f"{exe} is missing from the update")
            args, helper_dir = [str(exe), "--apply-update", *common], staging
        else:
            helper_dir = Path(tempfile.mkdtemp(prefix="eimeria_helper_"))
            helper = helper_dir / "apply_update.py"
            shutil.copy2(Path(__file__).with_name("apply_update.py"), helper)
            args = [python_for_helper(), str(helper), *common]
        if restart:
            args.append("--restart")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(args, creationflags=flags, close_fds=True, cwd=str(helper_dir))  # nosec B603 - fixed program and arguments (our own Python + script)

    def backups(self) -> list[Path]:
        root = config.BASE_DIR / "backups"
        return sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.exists() else []

    def launch_rollback(self, backup: Path) -> None:
        self.launch_installer(backup, restart=True)


def _read_version(root: Path) -> str:
    if (root / "VERSION.txt").exists():                   # Windows .exe builds (source code is compiled inside)
        return (root / "VERSION.txt").read_text(encoding="utf-8").strip()
    m = re.search(r'VERSION = "([^"]+)"', (root / "app" / "version.py").read_text(encoding="utf-8")) \
        if (root / "app" / "version.py").exists() else None
    return m.group(1) if m else "unknown"


def python_for_helper() -> str:
    """The console Python next to the running one (pythonw has no console; either works for the helper)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def can_self_update() -> bool:
    """Both the normal install (run.bat) and the Windows .exe can update themselves."""
    return True


def _pid() -> int:
    import os
    return os.getpid()


def write_update_json(root: Path, version: str, notes: list[str], date_str: str, commit: str) -> Path:
    if not COMMIT_PATTERN.match(commit):
        raise ValueError(f"not a full commit id: {commit!r}")
    p = root / "update.json"
    p.write_text(json.dumps({"version": version, "commit": commit, "date": date_str, "notes": notes},
                            indent=2) + "\n", encoding="utf-8")
    return p
