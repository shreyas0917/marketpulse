"""Check that every alerting rule has a runbook_url pointing at a file (and anchor) that exists.

URLs of the form https://github.com/<owner>/marketpulse/blob/main/<path>[#anchor] are mapped to
<repo>/<path>; relative paths are resolved from the repository root.
Usage: uv run python scripts/check_runbooks.py [rules files...]
"""

from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOB = re.compile(r"^https://github\.com/[^/]+/marketpulse/blob/main/")
ALERT = re.compile(r"^\s*- alert:\s*(\S+)", re.MULTILINE)
RUNBOOK = re.compile(r"runbook_url:\s*(\S+)")


def check_file(rules: Path) -> list[str]:
    text = rules.read_text(encoding="utf-8")
    starts = [m.start() for m in ALERT.finditer(text)] + [len(text)]
    errors = []
    for begin, end in itertools.pairwise(starts):
        block = text[begin:end]
        name = ALERT.match(block)
        alert = name.group(1) if name else "?"
        url = RUNBOOK.search(block)
        if url is None:
            errors.append(f"{rules.name}: {alert} has no runbook_url")
            continue
        path, _, anchor = BLOB.sub("", url.group(1)).partition("#")
        target = ROOT / path
        if not target.is_file():
            errors.append(f"{rules.name}: {alert} -> {path} does not exist")
        elif anchor and f'id="{anchor}"' not in target.read_text(encoding="utf-8"):
            errors.append(f"{rules.name}: {alert} -> {path}#{anchor} anchor missing")
        else:
            print(f"ok  {alert:28} {path}{'#' + anchor if anchor else ''}")
    return errors


def main(argv: list[str]) -> int:
    rules_dir = ROOT / "observability" / "prometheus" / "rules"
    files = [Path(a) for a in argv] or sorted(rules_dir.glob("*.yml"))
    errors = [e for f in files for e in check_file(f)]
    for e in errors:
        print(f"ERR {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
