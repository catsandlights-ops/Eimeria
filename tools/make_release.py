"""Prepare a release (used by the developer, not the app).

    python tools/make_release.py 1.1.0 "First change" "Second change"

Sets app/version.py, adds the notes to CHANGELOG.md, writes update.json and MANIFEST.sha256 (the
fingerprints, computed LAST so they match exactly what's published). Then commit, tag and push:

    git add -A && git commit -m "Release v1.1.0" && git tag v1.1.0 && git push origin main v1.1.0
"""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.updater import parse_version, write_manifest, write_update_json  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    version, notes = sys.argv[1].lstrip("v"), sys.argv[2:]
    vfile = ROOT / "app" / "version.py"
    current = re.search(r'VERSION = "([^"]+)"', vfile.read_text()).group(1)
    if parse_version(version) <= parse_version(current) and version != current:
        print(f"New version {version} must be higher than {current}")
        return 1
    vfile.write_text(re.sub(r'VERSION = "[^"]+"', f'VERSION = "{version}"', vfile.read_text()))
    today = date.today().isoformat()
    cl = ROOT / "CHANGELOG.md"
    old = cl.read_text(encoding="utf-8") if cl.exists() else "# Changelog\n"
    head, _, rest = old.partition("\n")
    entry = f"\n## {version} ({today})\n" + "".join(f"- {n}\n" for n in notes)
    cl.write_text(head + "\n" + entry + rest, encoding="utf-8")
    write_update_json(ROOT, version, notes, today)
    write_manifest(ROOT)
    print(f"Release {version} prepared. Now commit, tag v{version} and push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
