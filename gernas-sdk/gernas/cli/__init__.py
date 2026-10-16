"""``gernas`` command-line entry point: doctor | conformance | report | audit-report."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: gernas {doctor,conformance,report,audit-report} [options]\n\n"
              "  doctor        preflight: env vars, Collector reachability, test span + canonical context\n"
              "  conformance   check exported spans against the attribute registry\n"
              "  report        HTML observability report from local JSONL sinks\n"
              "  audit-report  audit / regulatory report from local JSONL sinks")
        return 0
    command, rest = argv[0], argv[1:]
    if command == "doctor":
        from gernas.cli.doctor import main as run
    elif command == "conformance":
        from gernas.cli.conformance import main as run
    elif command == "report":
        from gernas.core.html_reporter import main as legacy

        def run(args: list[str]) -> int:
            sys.argv = ["gernas report", *args]
            legacy()
            return 0
    elif command == "audit-report":
        from gernas.core.audit_report import main as legacy_audit

        def run(args: list[str]) -> int:
            sys.argv = ["gernas audit-report", *args]
            legacy_audit()
            return 0
    else:
        print(f"gernas: unknown command {command!r}", file=sys.stderr)
        return 2
    return int(run(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())
