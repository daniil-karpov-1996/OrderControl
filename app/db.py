import os
import sqlite3
from settings import SQLITE_PATH
from auth import hash_password


def ensure_default_warehouse(conn: sqlite3.Connection) -> int:
    row = fetchone(conn, "SELECT id FROM warehouses ORDER BY id LIMIT 1", ())
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO warehouses(name, is_active) VALUES(?, 1)",
        ("Основной склад",),
    )
    warehouse_id = int(cur.lastrowid)
    cur.close()
    return warehouse_id


def ensure_default_admin(conn: sqlite3.Connection):
    row = fetchone(conn, "SELECT COUNT(*) AS c FROM users", ())
    c = int(row["c"]) if row else 0
    if c > 0:
        return
    conn.execute(
        "INSERT INTO users(username, password_hash, is_active, role, warehouse_id) VALUES(?,?,1,'admin',?)",
        ("root", hash_password("root"), ensure_default_warehouse(conn)),
    )


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ================= WAREHOUSES =================
CREATE TABLE IF NOT EXISTS warehouses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ================= USERS =================
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  role TEXT NOT NULL DEFAULT 'admin',
  warehouse_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(warehouse_id) REFERENCES warehouses(id)
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
  warehouse_id INTEGER NOT NULL,
  subcategory_id INTEGER NOT NULL,
  sku TEXT,
  name TEXT NOT NULL,
  image_path TEXT,
  unit TEXT NOT NULL DEFAULT 'шт',
  purchase_price REAL NOT NULL DEFAULT 0,
  sale_price REAL NOT NULL DEFAULT 0,
  min_stock REAL NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
  FOREIGN KEY(subcategory_id) REFERENCES subcategories(id) ON DELETE CASCADE,
  UNIQUE(warehouse_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_products_subcat ON products(subcategory_id);

-- ================= STOCK MOVES =================
CREATE TABLE IF NOT EXISTS stock_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  client_id INTEGER,
  stock_out_doc_id INTEGER,
  warehouse_id INTEGER,
  admin_id INTEGER NOT NULL,
  move_type TEXT NOT NULL CHECK(move_type IN ('in','out')),
  qty REAL NOT NULL CHECK(qty > 0),
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
  FOREIGN KEY(client_id) REFERENCES clients(id),
  FOREIGN KEY(stock_out_doc_id) REFERENCES stock_out_docs(id) ON DELETE SET NULL,
  FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
  FOREIGN KEY(admin_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_moves_product ON stock_moves(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_moves_client ON stock_moves(client_id, created_at);

-- ================= STOCK OUT DOCS =================
CREATE TABLE IF NOT EXISTS stock_out_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT UNIQUE NOT NULL,
  client_id INTEGER,
  warehouse_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'posted')),
  note TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT,
  FOREIGN KEY(client_id) REFERENCES clients(id),
  FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
  FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_out_docs_status_created ON stock_out_docs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS stock_out_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL CHECK(qty > 0),
  unit_price REAL NOT NULL DEFAULT 0,
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
  warehouse_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'posted')),
  note TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT,
  FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
  FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_in_docs_status_created ON stock_in_docs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS stock_in_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL CHECK(qty > 0),
  unit_price REAL NOT NULL DEFAULT 0,
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
  warehouse_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'posted')),
  note TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  posted_at TEXT,
  FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
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
        if not _column_exists(cls.conn, "products", "purchase_price"):
            cls.conn.execute("ALTER TABLE products ADD COLUMN purchase_price REAL NOT NULL DEFAULT 0")
        if not _column_exists(cls.conn, "products", "sale_price"):
            cls.conn.execute("ALTER TABLE products ADD COLUMN sale_price REAL NOT NULL DEFAULT 0")
        products_need_warehouse = not _column_exists(cls.conn, "products", "warehouse_id")
        if products_need_warehouse:
            cls.conn.execute("ALTER TABLE products ADD COLUMN warehouse_id INTEGER")
        if not _column_exists(cls.conn, "stock_in_items", "unit_price"):
            cls.conn.execute("ALTER TABLE stock_in_items ADD COLUMN unit_price REAL NOT NULL DEFAULT 0")
        if not _column_exists(cls.conn, "stock_out_items", "unit_price"):
            cls.conn.execute("ALTER TABLE stock_out_items ADD COLUMN unit_price REAL NOT NULL DEFAULT 0")
        if not _column_exists(cls.conn, "stock_moves", "stock_out_doc_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN stock_out_doc_id INTEGER")
        if not _column_exists(cls.conn, "stock_moves", "stock_in_doc_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN stock_in_doc_id INTEGER")
        if not _column_exists(cls.conn, "stock_moves", "inventory_doc_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN inventory_doc_id INTEGER")
        if not _column_exists(cls.conn, "users", "warehouse_id"):
            cls.conn.execute("ALTER TABLE users ADD COLUMN warehouse_id INTEGER")
        if not _column_exists(cls.conn, "stock_in_docs", "warehouse_id"):
            cls.conn.execute("ALTER TABLE stock_in_docs ADD COLUMN warehouse_id INTEGER")
        if not _column_exists(cls.conn, "stock_out_docs", "warehouse_id"):
            cls.conn.execute("ALTER TABLE stock_out_docs ADD COLUMN warehouse_id INTEGER")
        if not _column_exists(cls.conn, "stock_moves", "warehouse_id"):
            cls.conn.execute("ALTER TABLE stock_moves ADD COLUMN warehouse_id INTEGER")
        if not _column_exists(cls.conn, "inventory_docs", "warehouse_id"):
            cls.conn.execute("ALTER TABLE inventory_docs ADD COLUMN warehouse_id INTEGER")
        cls.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stock_moves_warehouse_product ON stock_moves(warehouse_id, product_id, created_at)"
        )
        cls.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stock_out_docs_warehouse_created ON stock_out_docs(warehouse_id, created_at DESC)"
        )
        cls.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stock_in_docs_warehouse_created ON stock_in_docs(warehouse_id, created_at DESC)"
        )
        cls.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inventory_docs_warehouse_created ON inventory_docs(warehouse_id, created_at DESC)"
        )
        default_warehouse_id = ensure_default_warehouse(cls.conn)
        if products_need_warehouse:
            cls.conn.execute(
                """
                UPDATE products
                SET warehouse_id=COALESCE(
                  (SELECT sm.warehouse_id
                   FROM stock_moves sm
                   WHERE sm.product_id=products.id AND sm.warehouse_id IS NOT NULL
                   ORDER BY sm.id DESC LIMIT 1),
                  ?
                )
                WHERE warehouse_id IS NULL
                """,
                (default_warehouse_id,),
            )
        else:
            cls.conn.execute(
                "UPDATE products SET warehouse_id=? WHERE warehouse_id IS NULL",
                (default_warehouse_id,),
            )
        cls.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_warehouse_name ON products(warehouse_id, name)"
        )
        cls.conn.execute(
            "UPDATE users SET warehouse_id=? WHERE warehouse_id IS NULL",
            (default_warehouse_id,),
        )
        cls.conn.execute(
            """
            UPDATE stock_in_docs
            SET warehouse_id=COALESCE(
              (SELECT warehouse_id FROM users WHERE users.id=stock_in_docs.created_by), ?
            )
            WHERE warehouse_id IS NULL
            """,
            (default_warehouse_id,),
        )
        cls.conn.execute(
            """
            UPDATE stock_out_docs
            SET warehouse_id=COALESCE(
              (SELECT warehouse_id FROM users WHERE users.id=stock_out_docs.created_by), ?
            )
            WHERE warehouse_id IS NULL
            """,
            (default_warehouse_id,),
        )
        cls.conn.execute(
            """
            UPDATE inventory_docs
            SET warehouse_id=COALESCE(
              (SELECT warehouse_id FROM users WHERE users.id=inventory_docs.created_by), ?
            )
            WHERE warehouse_id IS NULL
            """,
            (default_warehouse_id,),
        )
        cls.conn.execute(
            """
            UPDATE stock_moves
            SET warehouse_id=COALESCE(
              (SELECT warehouse_id FROM stock_in_docs WHERE stock_in_docs.id=stock_moves.stock_in_doc_id),
              (SELECT warehouse_id FROM stock_out_docs WHERE stock_out_docs.id=stock_moves.stock_out_doc_id),
              (SELECT warehouse_id FROM inventory_docs WHERE inventory_docs.id=stock_moves.inventory_doc_id),
              (SELECT warehouse_id FROM users WHERE users.id=stock_moves.admin_id),
              ?
            )
            WHERE warehouse_id IS NULL
            """,
            (default_warehouse_id,),
        )
        _migrate_products_sku_scope(cls.conn)
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


def _products_has_global_sku_unique(conn: sqlite3.Connection) -> bool:
    for index_row in conn.execute("PRAGMA index_list(products)").fetchall():
        if not int(index_row["unique"]):
            continue
        columns = [
            row["name"]
            for row in conn.execute(
                f"PRAGMA index_info({index_row['name']})"
            ).fetchall()
        ]
        if columns == ["sku"]:
            return True
    return False


def _migrate_products_sku_scope(conn: sqlite3.Connection) -> None:
    if not _products_has_global_sku_unique(conn):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_subcat ON products(subcategory_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_warehouse_name ON products(warehouse_id, name)"
        )
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            """
            BEGIN;
            CREATE TABLE products_scoped (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              warehouse_id INTEGER NOT NULL,
              subcategory_id INTEGER NOT NULL,
              sku TEXT,
              name TEXT NOT NULL,
              image_path TEXT,
              unit TEXT NOT NULL DEFAULT 'шт',
              purchase_price REAL NOT NULL DEFAULT 0,
              sale_price REAL NOT NULL DEFAULT 0,
              min_stock REAL NOT NULL DEFAULT 0,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
              FOREIGN KEY(subcategory_id) REFERENCES subcategories(id) ON DELETE CASCADE,
              UNIQUE(warehouse_id, sku)
            );
            INSERT INTO products_scoped(
              id, warehouse_id, subcategory_id, sku, name, image_path, unit,
              purchase_price, sale_price, min_stock, is_active, created_at
            )
            SELECT
              id, warehouse_id, subcategory_id, sku, name, image_path, unit,
              purchase_price, sale_price, min_stock, is_active, created_at
            FROM products;
            DROP TABLE products;
            ALTER TABLE products_scoped RENAME TO products;
            CREATE INDEX idx_products_subcat ON products(subcategory_id);
            CREATE INDEX idx_products_warehouse_name ON products(warehouse_id, name);
            COMMIT;
            """
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
