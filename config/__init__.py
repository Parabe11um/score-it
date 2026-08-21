"""Project bootstrap compatibility helpers."""

import sys


try:
    import pysqlite3
except ImportError:
    pass
else:
    # Some shared hosts build Python against an SQLite version that is too old
    # for Django 5.2. Prefer the bundled Linux wheel when it is installed.
    sys.modules["sqlite3"] = pysqlite3
