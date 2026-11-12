"""Launcher (also used as the entry point when building the .exe)."""
import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
