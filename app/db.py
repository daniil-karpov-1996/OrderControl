import os
import sqlite3
from settings import SQLITE_PATH
from auth import hash_password


def ensure_default_admin(conn: sqlite3.Connection):
    row = fetchone(conn, "SELECT COUNT(*) AS c FROM users", ())
    c = int(row["c"]) if row else 0
    if c > 0:
        return
    conn.execute(
        "INSERT INTO users(username, password_hash, is_active, role) VALUES(?,?,1,'admin')",
        ("root", hash_password("root")),
    )


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ================= USERS =================
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  role TEXT NOT NULL DEFAULT 'admin',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ================= LOGS =================
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'INFO',
    event TEXT NOT NULL,
    actor_admin_id INTEGER,
    actor_username TEXT,
    ip TEXT,
    user_agent TEXT,
    request_id TEXT,
    entity TEXT,
    entity_id INTEGER,
    before_json TEXT,
    after_json TEXT,
    diff_json TEXT,
    money_delta REAL,
    message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(actor_admin_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);
CREATE INDEX IF NOT EXISTS idx_logs_entity_created ON logs(entity, entity_id, created_at);

-- ================= CLIENTS =================
CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name TEXT NOT NULL UNIQUE,
  phone TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ================= CATEGORIES =================
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ================= SUBCATEGORIES =================
CREATE TABLE IF NOT EXISTS subcategories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE,
  UNIQUE(category_id, name)
);

-- ================= PRODUCTS =================
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subcategory_id INTEGER NOT NULL,
  sku TEXT UNIQUE,
  name TEXT NOT NULL,
  image_path TEXT,
  unit TEXT NOT NULL DEFAULT 'шт',
  min_stock REAL NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(subcategory_id) REFERENCES subcategories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_products_subcat ON products(subcategory_id);

-- ================= STOCK MOVES =================
CREATE TABLE IF NOT EXISTS stock_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  client_id INTEGER,
  stock_out_doc_id INTEGER,
  admin_id INTEGER NOT NULL,
  move_type TEXT NOT NULL CHECK(move_type IN ('in','out')),
  qty REAL NOT NULL CHECK(qty > 0),
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
  FOREIGN KEY(client_id) REFERENCES clients(id),
  FOREIGN KEY(stock_out_doc_id) REFERENCES stock_out_docs(id) ON DELETE SET NULL,
  FOREIGN KEY(admin_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_moves_product ON stock_moves(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_moves_client ON stock_moves(client_id, created_at);

-- ================= STOCK OUT DOCS =================
CREATE TABLE IF NOT EXISTS stock_out_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT UNIQUE NOT NULL,
  client_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'posted')),
  note TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT,
  FOREIGN KEY(client_id) REFERENCES clients(id),
  FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_out_docs_status_created ON stock_out_docs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS stock_out_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL CHECK(qty > 0),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(doc_id) REFERENCES stock_out_docs(id) ON DELETE CASCADE,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_stock_out_items_doc ON stock_out_items(doc_id);
CREATE INDEX IF NOT EXISTS idx_stock_out_items_product ON stock_out_items(product_id);

-- ================= STOCK IN DOCS =================
CREATE TABLE IF NOT EXISTS stock_in_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'posted')),
  note TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT,
  FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_in_docs_status_created ON stock_in_docs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS stock_in_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL CHECK(qty > 0),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(doc_id) REFERENCES stock_in_docs(id) ON DELETE CASCADE,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_stock_in_items_doc ON stock_in_items(doc_id);
CREATE INDEX IF NOT EXISTS idx_stock_in_items_product ON stock_in_items(product_id);

-- ================= INVENTORY DOCS =================
CREATE TABLE IF NOT EXISTS inventory_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'posted')),
  note TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT,
  FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_docs_status_created ON inventory_docs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS inventory_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty_system REAL NOT NULL DEFAULT 0,
  qty_actual REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(doc_id) REFERENCES inventory_docs(id) ON DELETE CASCADE,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE RESTRICT,
  UNIQUE(doc_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_items_doc ON inventory_items(doc_id);
CREATE INDEX IF NOT EXISTS idx_inventory_items_product ON inventory_items(product_id);

-- ================= REALTIME =================
CREATE TABLE IF NOT EXISTS realtime_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  context TEXT NOT NULL,
  exclude_client TEXT NOT NULL DEFAULT '',
  event_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rt_outbox_ctx_id ON realtime_outbox(context, id);
"""


class DB:
    conn: sqlite3.Connection | None = None

    @classmethod
    def init(cls):
        os.makedirs(os.path.dirname(SQLITE_PATH) or ".", exist_ok=True)
        cls.conn = sqlite3.connect(
            SQLITE_PATH, check_same_thread=False, timeout=30.0
        )
        cls.conn.row_factory = sqlite3.Row
        cls.conn.execute("PRAGMA busy_timeout=30000")
        cls.conn.create_function(
            "casefold", 1,
            lambda s: (str(s).casefold() if s is not None else ""),
        )
        cls.conn.executescript(SCHEMA_SQL)
        # soft migrations
        if not _column_exists(cls.conn, "products", "image_path"):
            cls.conn.execute("ALTER TABLE products ADD COLUMN image_path TEXT")
        if not _column_exists(cls.conn, "stock_moves", "stock_out_doc_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN stock_out_doc_id INTEGER")
        if not _column_exists(cls.conn, "stock_moves", "stock_in_doc_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN stock_in_doc_id INTEGER")
        if not _column_exists(cls.conn, "stock_moves", "inventory_doc_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN inventory_doc_id INTEGER")
        ensure_default_admin(cls.conn)
        cls.conn.commit()

    @classmethod
    def close(cls):
        if cls.conn:
            cls.conn.close()
            cls.conn = None


def fetchone(conn, sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def fetchall(conn, sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _column_exists(conn, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()
    cur.close()
    return any(r[1] == col for r in rows)
