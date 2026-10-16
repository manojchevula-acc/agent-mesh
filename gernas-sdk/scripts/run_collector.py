"""Start the local GERNAS OTel Collector (collector/otel-collector-config.yaml).

Loads only the variables the Collector needs (OPIK_*, GERNAS_COLLECTOR_*, sampling
knobs) from an env file — never prints their values — then runs otelcol-contrib in
the foreground from the SDK root so relative output paths resolve to collector/out/.

    python scripts/run_collector.py [--env ../agent-mesh/.env] [--validate]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "collector" / "bin" / ("otelcol-contrib.exe" if os.name == "nt" else "otelcol-contrib")
CONFIG = ROOT / "collector" / "otel-collector-config.yaml"
_PREFIXES = ("OPIK_", "GERNAS_COLLECTOR_", "GERNAS_BASELINE_", "GERNAS_OPIK_", "GERNAS_TAIL_")


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key.startswith(_PREFIXES) and value:
            out[key] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=str(ROOT.parent / "agent-mesh" / ".env"))
    ap.add_argument("--validate", action="store_true", help="validate the config and exit")
    args = ap.parse_args()
    exe = BIN if BIN.exists() else shutil.which("otelcol-contrib")
    if not exe:
        print("otelcol-contrib not found: download it into collector/bin/ (see docs/onboarding-runbook.md)",
              file=sys.stderr)
        return 2
    env = dict(os.environ)
    loaded = load_env(Path(args.env))
    env.update(loaded)
    missing = [k for k in ("OPIK_API_KEY", "OPIK_WORKSPACE", "OPIK_PROJECT_NAME") if not env.get(k)]
    if missing:
        print(f"warning: {', '.join(missing)} not set — the Opik exporter will fail; data plane still works",
              file=sys.stderr)
        for k in missing:
            env.setdefault(k, "unset")
    (ROOT / "collector" / "out").mkdir(parents=True, exist_ok=True)
    print(f"collector: {exe}  config: {CONFIG.name}  env keys loaded: {sorted(loaded)}", flush=True)
    cmd = [str(exe), "validate" if args.validate else "", "--config", str(CONFIG)]
    return subprocess.call([c for c in cmd if c], cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
