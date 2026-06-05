"""Shared test setup.

Tests must not depend on a developer's local ``.env`` file. Provide it here
so the suite is hermetic and runs identically locally and in CI.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")

# Routes that don't override ``get_db`` (e.g. the access-log middleware tests)
# fall back to ``settings.database_url``, whose default points at an absolute
# ``/data`` path that doesn't exist in CI. Point it at a writable temp file so
# those requests can open a database without a local ``.env``.
_test_db = Path(tempfile.gettempdir()) / "planer_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_test_db.as_posix()}")
