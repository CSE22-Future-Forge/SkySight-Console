"""PyInstaller entry point (a plain script, so the package imports resolve as usual)."""

import sys

from ground_station.app import main

if __name__ == "__main__":
    sys.exit(main())
