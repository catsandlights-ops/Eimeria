"""Release tool (used by the developer, not the app). Two steps:

1. Prepare the release (version, changelog, fingerprints), then commit it:
       python tools/make_release.py prepare 0.1.2 "First change" "Second change"
       git add -A && git commit -m "Eimeria 0.1.2"

2. Publish: point update.json at that commit, commit, and push the release branch:
       python tools/make_release.py publish
       git add update.json && git commit -m "Publish 0.1.2" && git push origin HEAD:claude/releases

Installed copies read update.json from the release branch, download exactly that commit from GitHub,
check every file against MANIFEST.sha256 and check the version inside matches.
"""
import json
import re
import subprocess  # nosec B404 - runs git with fixed arguments
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.updater import parse_version, verify_manifest, write_manifest, write_update_json  # noqa: E402


def current_version() -> str:
    return re.search(r'VERSION = "([^"]+)"', (ROOT / "app" / "version.py").read_text()).group(1)


def prepare(version: str, notes: list[str]) -> int:
    version = version.lstrip("v")
    cur = current_version()
    if parse_version(version) <= parse_version(cur):
        print(f"New version {version} must be higher than {cur}")
        return 1
    vfile = ROOT / "app" / "version.py"
    vfile.write_text(re.sub(r'VERSION = "[^"]+"', f'VERSION = "{version}"', vfile.read_text()))
    cl = ROOT / "CHANGELOG.md"
    old = cl.read_text(encoding="utf-8") if cl.exists() else "# Changelog\n"
    head, _, rest = old.partition("\n")
    entry = f"\n## {version} ({date.today().isoformat()})\n" + "".join(f"- {n}\n" for n in notes)
    cl.write_text(head + "\n" + entry + rest, encoding="utf-8")
    write_manifest(ROOT)
    print(f"Release {version} prepared. Commit it, then run: python tools/make_release.py publish")
    return 0


def publish() -> int:
    git = lambda *a: subprocess.check_output(["git", *a], cwd=ROOT, text=True).strip()  # noqa: E731  # nosec B603 B607
    if git("status", "--porcelain"):
        print("Commit the prepared release first (working tree isn't clean).")
        return 1
    problems = verify_manifest(ROOT)
    if problems:
        print("The committed release doesn't match its fingerprints:", *problems[:10], sep="\n  ")
        return 1
    version = current_version()
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)} \(([^)]+)\)\n((?:- .*\n?)+)", text, re.M)
    if not m:
        print(f"No changelog entry for {version}")
        return 1
    notes = [line[2:].strip() for line in m.group(2).splitlines() if line.startswith("- ")]
    commit = git("rev-parse", "HEAD")
    write_update_json(ROOT, version, notes, m.group(1), commit)
    print(json.dumps(json.loads((ROOT / "update.json").read_text()), indent=2))
    print(f"update.json now points at {commit}. Commit it and push to the release branch.")
    return 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "prepare":
        return prepare(sys.argv[2], sys.argv[3:])
    if len(sys.argv) == 2 and sys.argv[1] == "publish":
        return publish()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
