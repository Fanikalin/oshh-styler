#!/usr/bin/env python3
"""Точка входа: запускается через python3 -I, поэтому путь к пакету добавляем сами."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oshh_style.app import StyleApp  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        StyleApp("setup", " ".join(sys.argv[2:]) or None).run()
    else:
        StyleApp().run()
