"""PyInstaller entry point for the desktop app. Equivalent to: python -m bot.ui"""
import multiprocessing
import sys

from bot.ui.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
