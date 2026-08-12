"""Repair UTF-8-read-as-cp1252 damage in the imported ground truth files.

The two ground truth files were produced by a different pipeline and arrived
with lossy mojibake. This applies the context rules in common/text.py and then
REPORTS anything it refused to guess, so a human resolves the genuinely
ambiguous cases instead of the tool inventing characters.

    python -m eval repair --dry-run     # show what would change
    python -m eval repair               # write it
"""

import json
from pathlib import Path

from ..common.ground_truth import MANIFEST, TRANSCRIPTIONS
from ..common.text import repair_tree

TARGETS = [MANIFEST, TRANSCRIPTIONS]


def run(dry_run: bool = False) -> int:
    total_unresolved = 0

    for path in TARGETS:
        if not path.exists():
            print(f"  skip  {path} (not present)")
            continue

        before = path.read_text(encoding="utf-8")
        repaired, unresolved = repair_tree(json.loads(before))
        after = json.dumps(repaired, indent=2, ensure_ascii=False) + "\n"

        changed = after != before
        print(f"\n{path}")
        print(f"  repaired : {'yes' if changed else 'no changes needed'}")
        print(f"  unresolved: {len(unresolved)}")

        if unresolved:
            total_unresolved += len(unresolved)
            print("\n  These were NOT guessed — one glyph, several possible origins.")
            print("  Edit the file by hand, then re-run to confirm zero remain:\n")
            for fragment in unresolved:
                print(f"    … {fragment} …")

        if changed and not dry_run:
            path.write_text(after, encoding="utf-8")
            print(f"\n  written -> {path}")
        elif changed:
            print("\n  (dry run — not written)")

    if total_unresolved:
        print(
            f"\n{total_unresolved} fragment(s) still need a human decision. "
            "Stage 2b will still run; these are cosmetic in most scorers "
            "because common/text.py::normalize folds dash variants anyway."
        )
    return 0
