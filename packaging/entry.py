"""AutoApply desktop entry point (PyInstaller). Created by Abhiram. MIT license."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app.desktop import main

    sys.exit(main())
