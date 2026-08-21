"""Allow running as a module: `python -m openmontage_cli`. """

from openmontage_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
