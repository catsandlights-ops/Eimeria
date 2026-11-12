"""Installs an Eimeria update (or rolls back). Runs as a separate process, from a temporary copy,
after Eimeria has closed. Standard library only, so it works no matter which version is installed.

  1. wait for Eimeria to exit
  2. back up the current app files to backups/<version>_<time>/
  3. copy in the new files; remove files the old version had that the new one doesn't
  4. reinstall Python packages if requirements.txt changed
  5. restart Eimeria
If step 3 or 4 fails, the backup is restored. User data is never touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROTECTED = {"settings.yaml", ".env", "trader.db", "trader.db-journal", "cache", "logs", "backups", ".venv",
             "venv", "updates", ".selftest_write", "dist", "build", "__pycache__", ".git", ".pytest_cache"}
MANIFEST = "MANIFEST.sha256"


def app_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if rel.parts[0] in PROTECTED or "__pycache__" in rel.parts or p.is_dir() or rel.suffix == ".pyc":
            continue
        out.append(rel)
    return out


def manifest_files(root: Path) -> set[str]:
    m = root / MANIFEST
    if not m.exists():
        return set()
    return {line.split("  ", 1)[1] for line in m.read_text(encoding="utf-8").splitlines() if "  " in line}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def read_version(root: Path) -> str:
    try:
        for line in (root / "app" / "version.py").read_text(encoding="utf-8").splitlines():
            if line.startswith("VERSION"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return "unknown"


def wait_for_exit(pid: int, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            ctypes.windll.kernel32.CloseHandle(handle)
        time.sleep(1.0)                    # let Windows release file locks
        return
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.3)


def copy_tree(src_root: Path, dst_root: Path, files: list[Path]) -> None:
    for rel in files:
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_root / rel, dst)


def python_in(target: Path, windowed: bool) -> str:
    win = target / ".venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    nix = target / ".venv" / "bin" / "python"
    return str(win if win.exists() else nix if nix.exists() else sys.executable)


def install(source: Path, target: Path, from_version: str, log) -> str:
    new_version = read_version(source)
    old_files = app_files(target)
    old_listed = manifest_files(target) or {p.as_posix() for p in old_files}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target / "backups" / f"{from_version}_{stamp}"
    log(f"Backing up {len(old_files)} files to {backup}")
    copy_tree(target, backup, old_files)
    req_before = sha(target / "requirements.txt")
    try:
        new_files = app_files(source)
        log(f"Installing {len(new_files)} files (version {new_version})")
        copy_tree(source, target, new_files)
        new_set = {p.as_posix() for p in new_files}
        for rel in sorted(old_listed - new_set):           # files the old version had, the new one doesn't
            f = target / rel
            if f.exists() and Path(rel).parts[0] not in PROTECTED:
                f.unlink()
                log(f"Removed old file {rel}")
        if sha(target / "requirements.txt") != req_before:
            log("requirements.txt changed: installing Python packages...")
            r = subprocess.run([python_in(target, False), "-m", "pip", "install", "-r",
                                str(target / "requirements.txt")], capture_output=True, text=True, timeout=1800)
            log(r.stdout[-3000:] + r.stderr[-3000:])
            if r.returncode != 0:
                raise RuntimeError("Installing Python packages failed (see logs/update.log)")
        return new_version
    except Exception as e:
        log(f"UPDATE FAILED: {e}. Restoring the previous version from {backup}")
        added = {p.as_posix() for p in app_files(target)} - {p.as_posix() for p in old_files}
        for rel in added:
            f = target / rel
            if f.exists() and Path(rel).parts[0] not in PROTECTED:
                f.unlink()
        copy_tree(backup, target, app_files(backup))
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--wait-pid", type=int, default=0)
    ap.add_argument("--from-version", default="unknown")
    ap.add_argument("--restart", action="store_true")
    a = ap.parse_args(argv)
    source, target = Path(a.source), Path(a.target)
    (target / "logs").mkdir(parents=True, exist_ok=True)
    logf = open(target / "logs" / "update.log", "a", encoding="utf-8")

    def log(msg):
        logf.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        logf.flush()
    log(f"--- update from {a.from_version} using {source}")
    if a.wait_pid:
        wait_for_exit(a.wait_pid)
    result = {"from": a.from_version, "ok": False, "time": datetime.now().isoformat(timespec="seconds")}
    try:
        result["to"] = install(source, target, a.from_version, log)
        result["ok"] = True
        log(f"Done: now version {result['to']}")
    except Exception as e:
        result["error"] = str(e)
    (target / "logs" / "last_update.json").write_text(json.dumps(result), encoding="utf-8")
    logf.close()
    if a.restart:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen([python_in(target, True), str(target / "run_app.py")], cwd=str(target),
                         creationflags=flags, close_fds=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
