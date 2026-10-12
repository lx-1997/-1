"""Use bundled SQLite in this isolated research runtime for MLflow >= 3.17."""
import sys
import pysqlite3

if tuple(map(int, pysqlite3.sqlite_version.split("."))) < (3, 31, 0):
    raise RuntimeError("Research runtime requires SQLite >= 3.31")
sys.modules["sqlite3"] = pysqlite3
sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
