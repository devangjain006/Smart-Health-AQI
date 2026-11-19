"""Smoke-test database schema with an explicitly configured SQLite test URL."""
import os
import tempfile
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./smart_aqi_test.sqlite")
from backend import db  # noqa: E402


class DatabaseSchemaTest(unittest.TestCase):
    def test_tables_are_created(self):
        db.init_db()
        table_names = set(db.Base.metadata.tables)
        self.assertTrue({"locations", "stations", "measurements", "alerts", "api_sources"}.issubset(table_names))


if __name__ == "__main__":
    unittest.main()
