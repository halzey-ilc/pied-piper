#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    for path in (root / "src", root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
