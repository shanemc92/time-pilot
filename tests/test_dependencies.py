"""Run with:  python -m unittest discover -s tests -v
Guards the database driver: DATABASE_URL in docker-compose.yml is a plain
postgresql:// URL, which SQLAlchemy 2.0 maps to psycopg2 (what requirements.txt
installs) but SQLAlchemy 2.1 maps to psycopg3 - an app that then fails at
start-up with "No module named 'psycopg'". Dialect lookup doesn't import the
driver, so this runs anywhere."""
import unittest

from sqlalchemy.engine import make_url


class DatabaseDriver(unittest.TestCase):
    def test_plain_postgres_url_uses_the_installed_driver(self):
        dialect = make_url("postgresql://u:p@db:5432/timepilot").get_dialect()
        self.assertEqual(dialect.driver, "psycopg2")


if __name__ == "__main__":
    unittest.main()
