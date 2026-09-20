import os
import tempfile

# Isolate every module-level path constant away from the real HOME before any
# minder module is imported (proxy.py / minder.py read env at import time).
_SESSION_TMP = tempfile.mkdtemp(prefix="minder-session-")
os.environ.setdefault("MINDER_STATE_DIR", os.path.join(_SESSION_TMP, "state"))
os.environ.setdefault("MINDER_CONFIG", os.path.join(_SESSION_TMP, "minder.json"))
os.environ.setdefault("MINDER_CAPS", os.path.join(_SESSION_TMP, "caps.json"))
os.environ.setdefault("MINDER_CONFIG_DIR", _SESSION_TMP)
os.environ.setdefault("MINDER_UPSTREAM", "http://127.0.0.1:1")
os.environ.setdefault("MINDER_PORT", "0")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
