import os
import hashlib
import json
from datetime import datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_dsn() -> str:
    """Build a DSN from environment variables (all required in production)."""
    return (
        f"host={os.environ['DB_HOST']} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ['DB_NAME']} "
        f"user={os.environ['DB_USER']} "
        f"password={os.environ['DB_PASSWORD']}"
    )


@contextmanager
def get_db():
    """Yield a psycopg2 connection with RealDictCursor; auto-close on exit."""
    conn = psycopg2.connect(_get_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health check helper
# ---------------------------------------------------------------------------

def check_db_connection() -> bool:
    """Return True if a basic DB round-trip succeeds, False otherwise."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Schema bootstrap  (used only in local dev / testing, not in prod migrations)
# ---------------------------------------------------------------------------

def init_db():
    """
    Create tables if they don't exist.
    In production, Alembic migrations are used instead.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id    TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    item_id     TEXT NOT NULL,
                    quantity    INTEGER NOT NULL CHECK (quantity > 0),
                    status      TEXT NOT NULL DEFAULT 'created',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_orders_customer_id
                    ON orders (customer_id);

                CREATE TABLE IF NOT EXISTS ledger (
                    ledger_id  TEXT PRIMARY KEY,
                    order_id   TEXT NOT NULL REFERENCES orders (order_id),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_ledger_order_id
                    ON ledger (order_id);

                CREATE TABLE IF NOT EXISTS idempotency_records (
                    idempotency_key TEXT PRIMARY KEY,
                    request_hash    TEXT NOT NULL,
                    response_body   TEXT NOT NULL,
                    status_code     INTEGER NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
        conn.commit()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def hash_request_body(body: dict) -> str:
    serialized = json.dumps(body, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


def get_idempotency_record(key: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM idempotency_records WHERE idempotency_key = %s",
                (key,),
            )
            return cur.fetchone()


def create_order_atomic(
    order_id: str,
    customer_id: str,
    item_id: str,
    quantity: int,
    ledger_id: str,
    idempotency_key: str,
    request_hash: str,
    response_body: dict,
):
    now = datetime.utcnow()

    with get_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (order_id, customer_id, item_id, quantity, status, created_at)
                    VALUES (%s, %s, %s, %s, 'created', %s)
                    """,
                    (order_id, customer_id, item_id, quantity, now),
                )

                cur.execute(
                    """
                    INSERT INTO ledger (ledger_id, order_id, created_at)
                    VALUES (%s, %s, %s)
                    """,
                    (ledger_id, order_id, now),
                )

                cur.execute(
                    """
                    INSERT INTO idempotency_records
                        (idempotency_key, request_hash, response_body, status_code, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (idempotency_key, request_hash, json.dumps(response_body), 201, now),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_order(order_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE order_id = %s",
                (order_id,),
            )
            return cur.fetchone()