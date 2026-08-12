"""Append-only per-case checkpoint, so a run can die and lose nothing.

Why this exists: stage 4 costs ~3 API calls per case and ~105 for a full pass.
On a free tier that WILL hit a quota wall partway. Without a checkpoint the
report is written only after the last case, so a failure at case 30 discards 29
cases' worth of paid API calls.

Design: one JSONL line per completed case, flushed immediately. Consequences:

  RESUMABLE   re-running skips cases already recorded, so you resume where the
              quota cut you off — tomorrow, or after a --delay bump.
  BATCHABLE   `--only 1,2,3` then `--only 4,5,6` ACCUMULATE rather than
              overwrite, so you can spread a run across days.
  CRASH-SAFE  a 429, a segfault or a Ctrl-C keeps every case already paid for.

Metrics are computed from the union of all recorded cases, so a report always
reflects everything gathered so far, not just the latest invocation.
"""

import json
from pathlib import Path

RUNS_DIR = Path("data/eval/runs")


class CaseStore:
    def __init__(self, stage: str) -> None:
        self._path = RUNS_DIR / f"{stage}_cases.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    def append(self, key: str, row: dict) -> None:
        """Record one completed case and flush it to disk immediately.

        Flushed per case, not buffered: buffering would reintroduce exactly the
        data loss this class exists to prevent.
        """
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": str(key), **row}, ensure_ascii=False, default=str) + "\n")
            fh.flush()

    def rows(self) -> list[dict]:
        """All recorded cases, last write per key winning.

        Re-running a case (say at a different --top-k) supersedes the earlier
        record rather than double-counting it in the averages.
        """
        if not self._path.exists():
            return []
        by_key: dict[str, dict] = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn final line from a hard kill — skip it
            by_key[row["key"]] = row
        return list(by_key.values())

    def done_keys(self) -> set[str]:
        """Keys already recorded AND scored — errored cases are retried."""
        return {r["key"] for r in self.rows() if not r.get("error")}
