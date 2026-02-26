"""BigLinux TTS — Text-to-Speech for Linux Desktop."""

__version__ = "1.0.0"

import sys
from pathlib import Path

# Support running without installation (development mode)
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from biglinux_tts.main import main  # noqa: E402, F401
