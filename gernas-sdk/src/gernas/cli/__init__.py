"""``gernas`` command-line entry point: doctor | conformance."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: gernas {doctor,conformance} [options]\n\n"
            "  doctor        preflight: configuration, Collector reachability, test span + context\n"
            "  conformance   check exported spans against the GERNAS semantic conventions"
        )
        return 0
    command, rest = argv[0], argv[1:]
    if command == "doctor":
        from gernas.cli.doctor import main as run
    elif command == "conformance":
        from gernas.cli.conformance import main as run
    else:
        print(f"gernas: unknown command {command!r}", file=sys.stderr)
        return 2
    return int(run(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())
