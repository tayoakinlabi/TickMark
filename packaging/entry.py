"""Frozen-build entry point.

PyInstaller needs a real script to analyse, and ``tickmark.cli.main`` is reached
through a console-script entry point that does not exist once frozen. This file
is that script and deliberately contains nothing else — every behaviour lives in
the package, so the frozen build and the ``pip``-installed one run identical code.
"""

from __future__ import annotations

import multiprocessing
import sys

from tickmark.cli.main import main

if __name__ == "__main__":
    # Harmless today and necessary the moment anything here uses a process pool:
    # without it a frozen Windows build re-runs the whole program in each child.
    multiprocessing.freeze_support()
    sys.exit(main())
