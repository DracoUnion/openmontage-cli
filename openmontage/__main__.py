"""Allow running as a module: `python -m openmontage`. """

from openmontage.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
