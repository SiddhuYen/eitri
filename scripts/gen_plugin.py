"""Regenerate the eitri plugin bundle's generated files (per-domain skills, Cursor rule, plugin version).

    python scripts/gen_plugin.py          # write them
    python scripts/gen_plugin.py --check  # exit 1 if any is out of date (CI)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eitri import plugin_files  # noqa: E402

if "--check" in sys.argv:
    bad = plugin_files.stale()
    print("out of date: " + ", ".join(bad) if bad else "plugin bundle is up to date")
    sys.exit(1 if bad else 0)
changed = plugin_files.write()
print("wrote " + (", ".join(changed) if changed else "nothing (already up to date)"))
