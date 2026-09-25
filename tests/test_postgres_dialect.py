import unittest
from unittest.mock import MagicMock

import nova_guarda.storage as storage


class PostgresDialectTest(unittest.TestCase):
    """Testa a camada de tradução SQLite/Postgres sem depender de um servidor
    Postgres real (indisponível neste ambiente). Cobre o que muda entre os
    dois dialetos: detecção de DATABASE_URL, tradução de placeholders e
    obtenção do id gerado em um INSERT."""

    def setUp(self):
        self.original_database_url = storage.DATABASE_URL

    def tearDown(self):
        storage.DATABASE_URL = self.original_database_url

    def test_using_postgres_detects_postgresql_scheme(self):
        storage.DATABASE_URL = "postgresql://user:pass@localhost:5432/db"
        self.assertTrue(storage.using_postgres())

    def test_using_postgres_detects_postgres_scheme(self):
        storage.DATABASE_URL = "postgres://user:pass@localhost:5432/db"
        self.assertTrue(storage.using_postgres())

    def test_using_postgres_false_when_unset(self):
        storage.DATABASE_URL = ""
        self.assertFalse(storage.using_postgres())

    def test_sql_translates_placeholders_only_for_postgres(self):
        storage.DATABASE_URL = "postgresql://user:pass@localhost:5432/db"
        self.assertEqual(storage.sql("SELECT * FROM t WHERE a = ? AND b = ?"), "SELECT * FROM t WHERE a = %s AND b = %s")

        storage.DATABASE_URL = ""
        self.assertEqual(storage.sql("SELECT * FROM t WHERE a = ?"), "SELECT * FROM t WHERE a = ?")

    def test_postgres_connection_proxy_translates_on_execute(self):
        storage.DATABASE_URL = "postgresql://user:pass@localhost:5432/db"
        raw_conn = MagicMock()
        proxy = storage._PostgresConnection(raw_conn)

        proxy.execute("SELECT * FROM bookings WHERE booking_id = ?", ("b1",))

        raw_conn.execute.assert_called_once_with("SELECT * FROM bookings WHERE booking_id = %s", ("b1",))

    def test_insert_returning_id_uses_returning_on_postgres(self):
        storage.DATABASE_URL = "postgresql://user:pass@localhost:5432/db"
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"id": 42}

        new_id = storage.insert_returning_id(conn, "INSERT INTO poller_runs (status) VALUES (?)", ("ok",))

        self.assertEqual(new_id, 42)
        called_query = conn.execute.call_args[0][0]
        self.assertIn("RETURNING id", called_query)

    def test_insert_returning_id_uses_lastrowid_on_sqlite(self):
        storage.DATABASE_URL = ""
        conn = MagicMock()
        conn.execute.return_value.lastrowid = 7

        new_id = storage.insert_returning_id(conn, "INSERT INTO poller_runs (status) VALUES (?)", ("ok",))

        self.assertEqual(new_id, 7)

    def test_ensure_column_uses_if_not_exists_on_postgres(self):
        storage.DATABASE_URL = "postgresql://user:pass@localhost:5432/db"
        conn = MagicMock()

        storage.ensure_column(conn, "bookings", "provider", "TEXT")

        conn.execute.assert_called_once_with("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS provider TEXT")


if __name__ == "__main__":
    unittest.main()
