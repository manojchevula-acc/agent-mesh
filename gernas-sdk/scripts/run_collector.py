"""Start the GERNAS OTel Collector locally (collector/otel-collector-config.yaml).

Loads only the variables the Collector needs (OPIK_*, ADX_*, AZURE_*, GERNAS_COLLECTOR_*,
sampling knobs) from an env file — never prints their values — then runs otelcol-contrib
in the foreground from the SDK root. Without ADX_CLUSTER_URI the data plane is replaced
by local files (collector/local-dataplane-file.yaml); with it, traces, metrics and logs
go to Azure Data Explorer exactly as in AKS.

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
_PREFIXES = ("OPIK_", "ADX_", "AZURE_", "GERNAS_COLLECTOR_", "GERNAS_BASELINE_", "GERNAS_OPIK_", "GERNAS_TAIL_")
LOCAL_OVERLAY = ROOT / "collector" / "local-dataplane-file.yaml"


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
        print(
            "otelcol-contrib not found: download it into collector/bin/ (see docs/getting-started.md)",
            file=sys.stderr,
        )
        return 2
    env = dict(os.environ)
    loaded = load_env(Path(args.env))
    env.update(loaded)
    missing = [k for k in ("OPIK_API_KEY", "OPIK_WORKSPACE", "OPIK_PROJECT_NAME") if not env.get(k)]
    if missing:
        print(
            f"warning: {', '.join(missing)} not set — the Opik exporter will fail",
            file=sys.stderr,
        )
        for k in missing:
            env.setdefault(k, "unset")
    env.setdefault("GERNAS_COLLECTOR_BIND", "127.0.0.1")  # local runs listen on loopback only
    configs = [CONFIG]
    if not env.get("ADX_CLUSTER_URI"):
        configs.append(LOCAL_OVERLAY)
        print(
            "warning: ADX_CLUSTER_URI not set — data plane goes to LOCAL FILES (collector/out/), "
            "not Azure Data Explorer",
            file=sys.stderr,
        )
    (ROOT / "collector" / "out").mkdir(parents=True, exist_ok=True)
    print(f"collector: {exe}  configs: {[c.name for c in configs]}  env keys loaded: {sorted(loaded)}", flush=True)
    cmd = [str(exe)] + (["validate"] if args.validate else [])
    for c in configs:
        cmd += ["--config", str(c)]
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
