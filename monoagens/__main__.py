"""Entrypoint: `python -m monoagens` and the `monoagens` console script.

Everything lives in `monoagens.cli`; this module stays a shim so the process entry point and
the installed script resolve the same `main`.
"""


import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
