"""Runs before any test module is imported (pytest always loads conftest.py first).

kioskarr.config.settings is a module-level singleton read once at import time, and
kioskarr.api.main now runs init_db()/ensure_app_settings_seeded() at import time too
(needed to have a session_secret_key ready before SessionMiddleware registers). Without
this override, importing kioskarr.api.main anywhere in the test suite would create/seed
the real project database (sqlite:///./kioskarr.db from .env) as a side effect.
"""

import os
import tempfile

_fd, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["KIOSKARR_DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
