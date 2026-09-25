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
# dsh's own session store: the capture/session readers must never pick up the
# running machine's real ~/.dsh during tests (that would make results depend on
# whatever the developer ran today). Tests that exercise the readers point
# MINDER_DSH_HOME at their own fixture instead.
os.environ.setdefault("MINDER_DSH_HOME", os.path.join(_SESSION_TMP, "dsh"))
# The sink URL is discovered from the *installed* hook command when the env
# does not set one. Point that lookup at a non-existent file so a test never
# picks up — or writes to — the developer's live sink.
os.environ.setdefault("MINDER_HOOKS_JSON",
                      os.path.join(_SESSION_TMP, "no-hooks.json"))

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
