"""Pytest config — put the app package dir on sys.path so `services`,
`config`, `ui`, `utils` import like they do at runtime."""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "usr" / "share" / "biglinux" / "tts-biglinux"
sys.path.insert(0, str(APP_DIR))
