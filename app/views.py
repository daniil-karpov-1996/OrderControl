from flask import abort, jsonify, make_response, render_template, request, Response
from datetime import datetime, timedelta
import json
import os
import re
import sqlite3
import urllib.parse
import time
from pathlib import Path
from uuid import uuid4
from flask_base import (
    AdminRequiredHandler,
    FlaskBaseHandler,
    local_wall_to_utc_db_str,
    utc_db_str_to_local_input,
    fmt_dt,
    fmt_money,
    fmt_qty,
)
from db import fetchone, fetchall
from auth import hash_password, verify_password
from services.log import audit_log
from realtime import rt_broadcast_many
from settings import (
    LOGIN_MAX_ATTEMPTS,
    LOGIN_BLOCK_MINUTES,
    TZ_OFFSET_HOURS,
    APP_CURRENCY,
    APP_VERSION,
    MAX_CONTENT_LENGTH,
    PRODUCT_UPLOAD_DIR,
)

LIST_DEFAULT_PER_PAGE = 25
LIST_MIN_PER_PAGE = 10
LIST_MAX_PER_PAGE = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _redirect_with_toast(
    url: str,
    *,
    title: str,
    body: str = "",
    variant: str = "success",
    timeout_ms: int = 10000,
) -> str:
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    q["toast_title"] = [title]
    if body:
        q["toast_body"] = [body]
    q["toast_variant"] = [variant]
    q["toast_timeout"] = [str(int(timeout_ms))]
    new_query = urllib.parse.urlencode(q, doseq=True)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
    )


def _redirect_target_after_modal_save(handler, list_url: str) -> str:
    if (handler.get_argument("from_modal", "") or "").strip() == "1":
        return "/modal/close?from_modal=1"
    return list_url


def _clamp_int(v, default: int, min_v: int, max_v: int) -> int:
    try:
        x = int(v)
    except Exception:
        return default
    return max(min_v, min(max_v, x))


def _sort_dir(v: str | None, default: str = "desc") -> str:
    if v and v.lower() in ("asc", "desc"):
        return v.lower()
    return default


def _order_by(
    sort_key: str | None, dir_: str, mapping: dict, default_key: str
) -> str:
    col = mapping.get(sort_key or "", mapping[default_key])
    d = "ASC" if dir_ == "asc" else "DESC"
    return f"{col} {d}"


def _pages(total: int, per_page: int) -> int:
    if per_page <= 0:
        return 1
    return max(1, (total + per_page - 1) // per_page)


def _counterparty_name(db, client_id: int) -> str:
    row = fetchone(db, "SELECT full_name FROM clients WHERE id=?", (int(client_id),))
    if row and "full_name" in row.keys():
        return row["full_name"] or str(client_id)
    return str(client_id)


def _product_name(db, product_id: int) -> str:
    row = fetchone(db, "SELECT name FROM products WHERE id=?", (int(product_id),))
    if row and "name" in row.keys():
        return row["name"] or str(product_id)
    return str(product_id)


def _password_error(p: str) -> str | None:
    if not p:
        return "Пароль не задан"
    if len(p) < 8:
        return "Пароль должен быть не короче 8 символов"
    return None


def _phone_error(phone: str) -> str | None:
    s = (phone or "").strip()
    if not s:
        return None
    if not re.fullmatch(r"[0-9+\s\-\(\)\.]+", s):
        return "В телефоне допустимы только цифры, +, пробел, скобки, дефис и точка"
    digits = re.sub(r"\D", "", s)
    if len(digits) > 15:
        return "Не больше 15 цифр в номере (международный формат)"
    return None


def _get_stock_balance(db, product_id: int, warehouse_id: int) -> float:
    row = fetchone(
        db,
        """
        SELECT
          COALESCE(SUM(CASE WHEN move_type='in' THEN qty ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN move_type='out' THEN qty ELSE 0 END), 0)
          AS balance
        FROM stock_moves
        WHERE product_id=? AND warehouse_id=?
        """,
        (int(product_id), int(warehouse_id)),
    )
    return float(row["balance"]) if row else 0.0


def _doc_line_items_snapshot(db, doc_id: int, doc_kind: str) -> list[dict]:
    table = "stock_in_items" if doc_kind == "in" else "stock_out_items"
    rows = fetchall(
        db,
        f"SELECT product_id, qty, unit_price FROM {table} WHERE doc_id=? ORDER BY id",
        (int(doc_id),),
    )
    return [
        {"product_id": int(r["product_id"]), "qty": float(r["qty"]), "unit_price": float(r["unit_price"] or 0)}
        for r in rows
    ]


def _apply_stock_in_moves(
    db,
    doc_id: int,
    line_items: list[tuple[int, float]],
    admin_id: int,
    note: str | None,
    created_at_db: str | None,
    warehouse_id: int,
) -> None:
    for pid, qty in line_items:
        db.execute(
            """
            INSERT INTO stock_moves(product_id, client_id, stock_in_doc_id, warehouse_id, admin_id, move_type, qty, note, created_at)
            VALUES(?,NULL,?,?,?,'in',?,?, COALESCE(?, datetime('now')))
            """,
            (pid, doc_id, warehouse_id, admin_id, qty, note, created_at_db),
        )


def _apply_stock_out_moves(
    db,
    doc_id: int,
    line_items: list[tuple[int, float]],
    client_id: int | None,
    admin_id: int,
    note: str | None,
    created_at_db: str | None,
    warehouse_id: int,
) -> None:
    for pid, qty in line_items:
        db.execute(
            """
            INSERT INTO stock_moves(product_id, client_id, stock_out_doc_id, warehouse_id, admin_id, move_type, qty, note, created_at)
            VALUES(?,?,?,?,?,'out',?,?, COALESCE(?, datetime('now')))
            """,
            (pid, client_id, doc_id, warehouse_id, admin_id, qty, note, created_at_db),
        )


def _stock_out_single_category_error(db, product_ids: list[int]) -> str | None:
    if not product_ids:
        return None
    placeholders = ",".join("?" * len(product_ids))
    rows = fetchall(
        db,
        f"""
        SELECT DISTINCT sc.category_id, c.name
        FROM products p
        JOIN subcategories sc ON sc.id = p.subcategory_id
        JOIN categories c ON c.id = sc.category_id
        WHERE p.id IN ({placeholders})
        """,
        tuple(product_ids),
    )
    if len(rows) <= 1:
        return None
    names = ", ".join(r["name"] for r in rows)
    return (
        "В одном отпуске допустимы товары только из одной категории. "
        f"Выбрано: {names}"
    )


def _stock_out_qty_error(
    db, line_items: list[tuple[int, float]], warehouse_id: int
) -> str | None:
    totals: dict[int, float] = {}
    for pid, qty in line_items:
        totals[pid] = totals.get(pid, 0.0) + qty
    for pid, total_qty in totals.items():
        balance = _get_stock_balance(db, pid, warehouse_id)
        if total_qty > balance:
            p = fetchone(db, "SELECT name, unit FROM products WHERE id=?", (pid,))
            pname = p["name"] if p else f"ID {pid}"
            unit = p["unit"] if p and p["unit"] else "шт"
            return (
                f"{pname}: на складе {fmt_qty(balance)} {unit}, "
                f"запрошено {fmt_qty(total_qty)} {unit}"
            )
    return None


def _doc_qty_by_product(items: list[dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for it in items:
        pid = int(it["product_id"])
        out[pid] = out.get(pid, 0.0) + float(it["qty"])
    return out


def _inventory_products_query_rows(db, warehouse_id: int) -> list[dict]:
    rows = fetchall(
        db,
        """
        SELECT
          p.id, p.name, p.sku, p.unit, p.image_path, p.is_active,
          sc.id AS subcategory_id, sc.category_id,
          c.name AS category_name, sc.name AS subcategory_name,
          COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END), 0)
            - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END), 0) AS stock_balance
        FROM products p
        JOIN subcategories sc ON sc.id = p.subcategory_id
        JOIN categories c ON c.id = sc.category_id
        LEFT JOIN stock_moves sm ON sm.product_id = p.id AND sm.warehouse_id=?
        GROUP BY p.id
        ORDER BY c.name, sc.name, p.name
        """,
        (int(warehouse_id),),
    )
    return [dict(r) for r in rows]


def _parse_inventory_lines(
    product_ids: list,
    qty_system_values: list,
    qty_actual_values: list,
) -> list[dict]:
    line_items: list[dict] = []
    for i, pid_raw in enumerate(product_ids):
        pid_s = (pid_raw or "").strip()
        if not pid_s.isdigit():
            continue
        actual_s = (qty_actual_values[i] if i < len(qty_actual_values) else "").strip()
        if actual_s == "":
            continue
        system_s = (qty_system_values[i] if i < len(qty_system_values) else "").strip()
        try:
            qty_actual = float(actual_s)
            if qty_actual < 0:
                raise ValueError
            qty_system = float(system_s) if system_s else 0.0
        except Exception:
            continue
        line_items.append(
            {
                "product_id": int(pid_s),
                "qty_system": qty_system,
                "qty_actual": qty_actual,
            }
        )
    return line_items


def _apply_inventory_moves(
    db,
    doc_id: int,
    line_items: list[dict],
    admin_id: int,
    note: str | None,
    created_at_db: str | None,
    warehouse_id: int,
) -> None:
    note_text = note or "Инвентаризация"
    for it in line_items:
        pid = int(it["product_id"])
        live = _get_stock_balance(db, pid, warehouse_id)
        diff = float(it["qty_actual"]) - live
        if abs(diff) < 1e-9:
            continue
        if diff > 0:
            db.execute(
                """
                INSERT INTO stock_moves(product_id, client_id, inventory_doc_id, warehouse_id, admin_id, move_type, qty, note, created_at)
                VALUES(?,NULL,?,?,?,'in',?,?, COALESCE(?, datetime('now')))
                """,
                (pid, doc_id, warehouse_id, admin_id, diff, note_text, created_at_db),
            )
        else:
            qty_out = abs(diff)
            db.execute(
                """
                INSERT INTO stock_moves(product_id, client_id, inventory_doc_id, warehouse_id, admin_id, move_type, qty, note, created_at)
                VALUES(?,NULL,?,?,?,'out',?,?, COALESCE(?, datetime('now')))
                """,
                (pid, doc_id, warehouse_id, admin_id, qty_out, note_text, created_at_db),
            )


def _inventory_page_products(
    db,
    doc: dict | None,
    saved_items: list[dict],
    warehouse_id: int,
) -> list[dict]:
    if doc and (doc.get("status") or "") == "posted":
        return [dict(it) for it in saved_items]

    saved_map = {int(x["product_id"]): x for x in saved_items}
    result: list[dict] = []
    for p in _inventory_products_query_rows(db, warehouse_id):
        row = dict(p)
        pid = int(row["id"])
        if pid in saved_map:
            row["qty_system"] = float(saved_map[pid]["qty_system"])
            row["qty_actual"] = float(saved_map[pid]["qty_actual"])
        else:
            row["qty_system"] = float(row["stock_balance"])
            row["qty_actual"] = None
        result.append(row)
    return result


def _doc_no_next(db, prefix: str = "OUT") -> str:
    yy = datetime.utcnow().strftime("%y")
    table_map = {
        "IN": "stock_in_docs",
        "OUT": "stock_out_docs",
        "INV": "inventory_docs",
    }
    table = table_map.get(prefix, "stock_out_docs")
    row = fetchone(
        db,
        f"SELECT doc_no FROM {table} WHERE doc_no LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{prefix}-{yy}%",),
    )
    seq = 1
    if row and row["doc_no"]:
        tail = re.sub(r"\D", "", str(row["doc_no"])[-6:])
        if tail.isdigit():
            seq = max(1, int(tail) + 1)
    return f"{prefix}-{yy}{seq:04d}"


def _normalize_upload_dir() -> Path:
    p = Path(PRODUCT_UPLOAD_DIR)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_product_image(file_storage) -> str:
    if not file_storage or not getattr(file_storage, "filename", ""):
        return ""
    name = os.path.basename(file_storage.filename or "").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in {"jpg", "jpeg", "png", "webp"}:
        raise ValueError("Разрешены только JPG, PNG или WEBP")
    if request.content_length and int(request.content_length) > int(MAX_CONTENT_LENGTH):
        raise ValueError("Файл слишком большой")
    safe_name = f"{uuid4().hex}.{ext}"
    upload_dir = _normalize_upload_dir()
    abs_path = upload_dir / safe_name
    file_storage.save(str(abs_path))
    return f"/static/uploads/products/{safe_name}"


def _remove_product_image(path: str | None) -> None:
    p = (path or "").strip()
    if not p.startswith("/static/uploads/products/"):
        return
    name = p.rsplit("/", 1)[-1].strip()
    if not name:
        return
    fp = _normalize_upload_dir() / name
    try:
        if fp.exists():
            fp.unlink()
    except Exception:
        return


def _dt_local_to_db(s: str | None, *, is_end: bool) -> tuple[str | None, str | None]:
    if not s:
        return None, None
    raw = str(s).strip()
    if not raw:
        return None, None
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace(" ", "T"))
        except Exception:
            return None, raw
    if is_end and len(raw) == 16:
        dt = dt.replace(second=59)
    if (not is_end) and len(raw) == 16:
        dt = dt.replace(second=0)
    dt_utc = dt - timedelta(hours=TZ_OFFSET_HOURS)
    db_s = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    input_s = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return db_s, input_s


# ═══════════════════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════════════════


class LoginHandler(FlaskBaseHandler):
    _login_attempts: dict[str, dict] = {}

    def get(self):
        row = fetchone(self.db, "SELECT COUNT(*) AS c FROM users", ())
        has_admin = int(row["c"]) > 0 if row else False
        expired = (self.get_argument("expired", "") or "").strip() == "1"
        return self.render(
            "login.html",
            error=(
                "Сессия завершена из-за неактивности. Выполните вход снова."
                if expired
                else None
            ),
            has_admin=has_admin,
            title="Вход",
        )

    def post(self):
        client_ip = (
            (self.request.headers.get("X-Real-IP") or "").strip()
            or self.request.remote_ip
        )
        now_ts = time.time()

        rec = self._login_attempts.get(client_ip)
        if rec and rec.get("count", 0) >= LOGIN_MAX_ATTEMPTS:
            block_secs = LOGIN_BLOCK_MINUTES * 60
            elapsed = now_ts - rec.get("last_ts", 0)
            if elapsed < block_secs:
                block_until_ts = rec.get("last_ts", 0) + block_secs
                minutes_left = int((block_secs - elapsed + 59) // 60)
                err = (
                    f"Слишком много неудачных попыток входа. "
                    f"Попробуйте снова через {minutes_left} мин."
                )
                already_logged_until = rec.get("blocked_logged_until_ts", 0)
                if int(already_logged_until or 0) != int(block_until_ts):
                    username_attempt = self.get_body_argument("username", "").strip()
                    audit_log(
                        self.db,
                        level="WARN",
                        event="login_blocked",
                        actor_admin_id=None,
                        actor_username=username_attempt or None,
                        ip=client_ip,
                        user_agent=(
                            self.request.headers.get("User-Agent") or ""
                        ).strip()
                        or None,
                        request_id=self.request_id,
                        entity="auth",
                        before={"username": username_attempt or None},
                        after={
                            "reason": "blocked",
                            "retry_after_min": minutes_left,
                            "attempts": int(rec.get("count", 0)),
                        },
                        message="login_blocked",
                    )
                    rec["blocked_logged_until_ts"] = block_until_ts
                    self._login_attempts[client_ip] = rec
                row = fetchone(self.db, "SELECT COUNT(*) AS c FROM users", ())
                has_admin = int(row["c"]) > 0 if row else False
                return self.render(
                    "login.html",
                    error=err,
                    has_admin=has_admin,
                    title="Вход",
                )
            else:
                self._login_attempts[client_ip] = {"count": 0, "last_ts": now_ts}

        username = self.get_body_argument("username", "").strip()
        password = self.get_body_argument("password", "")

        row = fetchone(
            self.db,
            "SELECT id, username, password_hash, is_active, role FROM users WHERE username=?",
            (username,),
        )

        if not row or int(row["is_active"]) != 1:
            target_id = int(row["id"]) if row else None
            target_is_active = int(row["is_active"]) if row else None
            target_role = row["role"] if row else None
            audit_log(
                self.db,
                level="WARN",
                event="login_failed",
                actor_admin_id=None,
                actor_username=username or None,
                ip=client_ip,
                user_agent=(
                    self.request.headers.get("User-Agent") or ""
                ).strip()
                or None,
                request_id=self.request_id,
                entity="auth",
                before={"username": username or None},
                after={
                    "reason": "no_user_or_inactive",
                    "target_id": target_id,
                    "target_is_active": target_is_active,
                    "target_role": target_role,
                },
                message="login_failed: no_user_or_inactive",
            )
            rec = self._login_attempts.get(client_ip) or {
                "count": 0,
                "last_ts": now_ts,
            }
            rec["count"] = rec.get("count", 0) + 1
            rec["last_ts"] = now_ts
            self._login_attempts[client_ip] = rec
            return self.render(
                "login.html",
                error="Неверный логин или пароль",
                has_admin=True,
                title="Вход",
            )

        if not verify_password(password, row["password_hash"]):
            audit_log(
                self.db,
                level="WARN",
                event="login_failed",
                actor_admin_id=int(row["id"]),
                actor_username=row["username"],
                ip=client_ip,
                user_agent=(
                    self.request.headers.get("User-Agent") or ""
                ).strip()
                or None,
                request_id=self.request_id,
                entity="auth",
                before={"username": row["username"]},
                after={
                    "reason": "bad_password",
                    "target_id": int(row["id"]),
                    "target_role": row["role"],
                },
                message="login_failed: bad_password",
            )
            rec = self._login_attempts.get(client_ip) or {
                "count": 0,
                "last_ts": now_ts,
            }
            rec["count"] = rec.get("count", 0) + 1
            rec["last_ts"] = now_ts
            self._login_attempts[client_ip] = rec
            return self.render(
                "login.html",
                error="Неверный логин или пароль",
                has_admin=True,
                title="Вход",
            )

        if client_ip in self._login_attempts:
            del self._login_attempts[client_ip]

        self.set_secure_cookie("admin_id", str(int(row["id"])))
        role = (row["role"] or "admin") if "role" in row.keys() else "admin"
        self.set_secure_cookie("role", role)
        self.set_secure_cookie("username", row["username"])
        self.set_secure_cookie("last_activity_ts", str(int(time.time())))

        audit_log(
            self.db,
            level="AUDIT",
            event="login",
            actor_admin_id=int(row["id"]),
            actor_username=row["username"],
            ip=client_ip,
            user_agent=(
                self.request.headers.get("User-Agent") or ""
            ).strip()
            or None,
            request_id=self.request_id,
            entity="auth",
            before={"username": row["username"]},
            after={
                "reason": "success",
                "target_id": int(row["id"]),
                "role": row["role"],
                "target_is_active": 1,
            },
            message="login",
        )

        return self.redirect("/")


class LogoutHandler(FlaskBaseHandler):
    def get(self):
        if self.current_admin_id:
            client_ip = (
                (self.request.headers.get("X-Real-IP") or "").strip()
                or self.request.remote_ip
            )
            u = fetchone(
                self.db,
                "SELECT username, role FROM users WHERE id=?",
                (self.current_admin_id,),
            )
            audit_log(
                self.db,
                level="AUDIT",
                event="logout",
                actor_admin_id=self.current_admin_id,
                actor_username=u["username"] if u else None,
                ip=client_ip,
                user_agent=(
                    self.request.headers.get("User-Agent") or ""
                ).strip()
                or None,
                request_id=self.request_id,
                entity="auth",
                before={"username": u["username"] if u else None},
                after={"reason": "logout", "role": u["role"] if u else None},
                message="logout",
            )
        self.clear_cookie("admin_id")
        self.clear_cookie("role")
        self.clear_cookie("username")
        self.clear_cookie("last_activity_ts")
        return self.redirect("/login?clear_toasts=1")


class ModalCloseHandler(AdminRequiredHandler):
    def get(self):
        return render_template("modal_close.html")


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════


class DashboardHandler(AdminRequiredHandler):
    def get(self):
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)
        scoped = not self.is_role_admin
        move_join_scope = " AND sm.warehouse_id=?" if scoped else ""
        move_scope = "WHERE sm.warehouse_id=?" if scoped else ""
        doc_scope = "WHERE d.warehouse_id=?" if scoped else ""
        scope_params = (warehouse_id,) if scoped else ()
        total_products = int(
            (fetchone(self.db, "SELECT COUNT(*) AS c FROM products WHERE is_active=1") or {"c": 0})["c"]
        )

        products_with_stock = int(
            (fetchone(self.db, f"""
                SELECT COUNT(*) AS c FROM (
                    SELECT p.id,
                        COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END),0)
                        - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END),0) AS bal
                    FROM products p
                    LEFT JOIN stock_moves sm ON sm.product_id = p.id{move_join_scope}
                    WHERE p.is_active=1
                    GROUP BY p.id
                    HAVING bal > 0
                )
            """, scope_params) or {"c": 0})["c"]
        )

        low_stock_products = fetchall(
            self.db,
            f"""
            SELECT
              p.id, p.name, p.sku, p.unit, p.min_stock,
              COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END), 0)
                - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END), 0)
                AS stock_balance
            FROM products p
            LEFT JOIN stock_moves sm ON sm.product_id = p.id{move_join_scope}
            WHERE p.is_active = 1 AND p.min_stock > 0
            GROUP BY p.id
            HAVING stock_balance < p.min_stock
            ORDER BY (stock_balance - p.min_stock) ASC
            LIMIT 20
            """,
            scope_params,
        )

        today_moves_count = int(
            (fetchone(
                self.db,
                "SELECT COUNT(*) AS c FROM stock_moves sm WHERE date(sm.created_at)=date('now')"
                + (" AND sm.warehouse_id=?" if scoped else ""),
                scope_params,
            ) or {"c": 0})["c"]
        )

        total_clients = int(
            (fetchone(self.db, "SELECT COUNT(*) AS c FROM clients WHERE is_active=1") or {"c": 0})["c"]
        )

        recent_moves = fetchall(
            self.db,
            f"""
            SELECT
              sm.id, sm.move_type, sm.qty, sm.note, sm.created_at,
              p.name AS product_name, p.unit AS product_unit,
              cl.full_name AS client_name,
              u.username
            FROM stock_moves sm
            JOIN products p ON p.id = sm.product_id
            LEFT JOIN clients cl ON cl.id = sm.client_id
            LEFT JOIN users u ON u.id = sm.admin_id
            {move_scope}
            ORDER BY sm.id DESC
            LIMIT 15
            """,
            scope_params,
        )

        recent_out_docs = fetchall(
            self.db,
            f"""
            SELECT d.id, d.doc_no, d.status, d.note, d.created_at,
                   cl.full_name AS client_name,
                   (SELECT COUNT(*) FROM stock_out_items i WHERE i.doc_id = d.id) AS item_count
            FROM stock_out_docs d
            LEFT JOIN clients cl ON cl.id = d.client_id
            {doc_scope}
            ORDER BY d.created_at DESC
            LIMIT 8
            """,
            scope_params,
        )

        recent_in_docs = fetchall(
            self.db,
            f"""
            SELECT d.id, d.doc_no, d.status, d.note, d.created_at,
                   (SELECT COUNT(*) FROM stock_in_items i WHERE i.doc_id = d.id) AS item_count
            FROM stock_in_docs d
            {doc_scope}
            ORDER BY d.created_at DESC
            LIMIT 8
            """,
            scope_params,
        )

        return self.render(
            "dashboard.html",
            total_products=total_products,
            products_with_stock=products_with_stock,
            low_stock_count=len(low_stock_products),
            today_moves_count=today_moves_count,
            total_clients=total_clients,
            low_stock_products=[dict(r) for r in low_stock_products],
            recent_moves=[dict(r) for r in recent_moves],
            recent_out_docs=[dict(r) for r in recent_out_docs],
            recent_in_docs=[dict(r) for r in recent_in_docs],
            title="Склад",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Clients
# ═══════════════════════════════════════════════════════════════════════════


class ClientsHandler(AdminRequiredHandler):
    def get(self):
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 10_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")

        sort_map = {
            "id": "c.id",
            "name": "c.full_name",
            "created": "c.created_at",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        row = fetchone(self.db, "SELECT COUNT(*) AS c FROM clients", ())
        total = int(row["c"]) if row else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        clients = fetchall(
            self.db,
            f"""
            SELECT c.id, c.full_name, c.phone, c.is_active, c.created_at
            FROM clients c
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        )

        return self.render(
            "clients.html",
            clients=[dict(r) for r in clients],
            error=None,
            form={},
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title="Клиенты",
        )

    def post(self):
        full_name = self.get_body_argument("full_name", "").strip()
        phone = self.get_body_argument("phone", "").strip()

        if not full_name:
            return self.redirect(
                _redirect_with_toast(
                    "/clients",
                    title="Клиент не добавлен",
                    body="Введите имя",
                    variant="error",
                )
            )

        ph_err = _phone_error(phone)
        if ph_err:
            return self.redirect(
                _redirect_with_toast(
                    "/clients",
                    title="Клиент не добавлен",
                    body=ph_err,
                    variant="error",
                )
            )

        try:
            cur = self.db.execute(
                "INSERT INTO clients(full_name, phone) VALUES(?,?)",
                (full_name, phone or None),
            )
            new_id = int(cur.lastrowid)
            cur.close()
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            if "clients.full_name" in str(e):
                return self.redirect(
                    _redirect_with_toast(
                        "/clients",
                        title="Клиент не добавлен",
                        body="Такой клиент уже существует",
                        variant="error",
                    )
                )
            raise

        self.audit(
            event="create_client",
            entity="client",
            entity_id=new_id,
            after={"full_name": full_name, "phone": phone or None},
            message=full_name,
        )

        client_id = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["clients"],
            by=client_id,
            event={"kind": "add_client", "name": full_name},
        )

        return self.redirect(
            _redirect_with_toast(
                "/clients", title="Клиент добавлен", body=full_name
            )
        )


class ClientEditHandler(AdminRequiredHandler):
    def get(self, client_id: str):
        client = fetchone(
            self.db,
            "SELECT id, full_name, phone, is_active, created_at FROM clients WHERE id=?",
            (int(client_id),),
        )
        if not client:
            abort(404)
        return self.render(
            "client_edit.html",
            client=dict(client),
            error=None,
            title="Редактирование клиента",
        )

    def post(self, client_id: str):
        cid = int(client_id)
        full_name = self.get_body_argument("full_name", "").strip()
        phone_raw = self.get_body_argument("phone", "").strip()
        phone = phone_raw or None
        is_active = 1 if self.get_body_argument("is_active", None) is not None else 0

        if not full_name:
            client = fetchone(
                self.db,
                "SELECT id, full_name, phone, is_active, created_at FROM clients WHERE id=?",
                (cid,),
            )
            if not client:
                abort(404)
            return self.render(
                "client_edit.html",
                client=dict(client),
                error="Имя не может быть пустым",
                title="Редактирование клиента",
            )

        ph_err = _phone_error(phone_raw)
        if ph_err:
            client = fetchone(
                self.db,
                "SELECT id, full_name, phone, is_active, created_at FROM clients WHERE id=?",
                (cid,),
            )
            if not client:
                abort(404)
            c = dict(client)
            c["full_name"] = full_name
            c["phone"] = phone_raw
            return self.render(
                "client_edit.html",
                client=c,
                error=ph_err,
                title="Редактирование клиента",
            )

        old = fetchone(
            self.db,
            "SELECT full_name, phone, is_active FROM clients WHERE id=?",
            (cid,),
        )
        if not old:
            abort(404)

        if (
            (old["full_name"] or "").strip() == full_name
            and (old["phone"] or None) == phone
            and int(old["is_active"]) == is_active
        ):
            return self.redirect(
                _redirect_target_after_modal_save(self, "/clients")
            )

        try:
            self.db.execute(
                "UPDATE clients SET full_name=?, phone=?, is_active=? WHERE id=?",
                (full_name, phone, is_active, cid),
            )
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            if "clients.full_name" in str(e):
                client = fetchone(
                    self.db,
                    "SELECT id, full_name, phone, is_active, created_at FROM clients WHERE id=?",
                    (cid,),
                )
                return self.render(
                    "client_edit.html",
                    client=dict(client) if client else {"id": cid, "full_name": full_name, "phone": phone_raw, "is_active": is_active},
                    error="Такой клиент уже существует",
                    title="Редактирование клиента",
                )
            raise

        self.audit(
            event="edit_client",
            entity="client",
            entity_id=cid,
            before={
                "full_name": (old["full_name"] or "").strip(),
                "phone": old["phone"],
                "is_active": int(old["is_active"]),
            },
            after={
                "full_name": full_name,
                "phone": phone,
                "is_active": is_active,
            },
            message=full_name,
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["clients", f"client/{cid}"],
            by=ws_cid,
            event={"kind": "edit_client", "id": cid, "name": full_name},
        )

        return self.redirect(
            _redirect_with_toast(
                _redirect_target_after_modal_save(self, "/clients"),
                title="Клиент обновлён",
                body=full_name,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Categories
# ═══════════════════════════════════════════════════════════════════════════


class CategoriesHandler(AdminRequiredHandler):
    def get(self):
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 100_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")

        sort_map = {
            "id": "c.id",
            "name": "c.name",
            "created": "c.created_at",
            "subcategories": "subcategory_count",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        row = fetchone(self.db, "SELECT COUNT(*) AS c FROM categories", ())
        total = int(row["c"]) if row else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        categories = fetchall(
            self.db,
            f"""
            SELECT
              c.id,
              c.name,
              c.created_at,
              (SELECT COUNT(*) FROM subcategories sc WHERE sc.category_id = c.id) AS subcategory_count
            FROM categories c
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        )

        return self.render(
            "categories.html",
            categories=[dict(r) for r in categories],
            error=None,
            form={},
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title="Категории",
        )

    def post(self):
        if self.is_role_viewer:
            abort(403)
        name = self.get_body_argument("name", "").strip()

        if not name:
            return self.redirect(
                _redirect_with_toast(
                    "/categories",
                    title="Категория не добавлена",
                    body="Введите название",
                    variant="error",
                )
            )

        try:
            cur = self.db.execute(
                "INSERT INTO categories(name) VALUES(?)", (name,)
            )
            new_id = int(cur.lastrowid)
            cur.close()
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            if "categories.name" in str(e):
                return self.redirect(
                    _redirect_with_toast(
                        "/categories",
                        title="Категория не добавлена",
                        body="Такая категория уже существует",
                        variant="error",
                    )
                )
            raise

        self.audit(
            event="create_category",
            entity="category",
            entity_id=new_id,
            after={"name": name},
            message=name,
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["categories"],
            by=ws_cid,
            event={"kind": "add_category", "name": name},
        )

        return self.redirect(
            _redirect_with_toast(
                "/categories", title="Категория добавлена", body=name
            )
        )


class CategoryEditHandler(AdminRequiredHandler):
    def get(self, category_id: str):
        category = fetchone(
            self.db,
            "SELECT id, name, created_at FROM categories WHERE id=?",
            (int(category_id),),
        )
        if not category:
            abort(404)
        return self.render(
            "category_edit.html",
            category=dict(category),
            error=None,
            title="Редактирование категории",
        )

    def post(self, category_id: str):
        cid = int(category_id)
        name = self.get_body_argument("name", "").strip()

        category = fetchone(
            self.db,
            "SELECT id, name, created_at FROM categories WHERE id=?",
            (cid,),
        )
        if not category:
            abort(404)

        if not name:
            return self.render(
                "category_edit.html",
                category=dict(category),
                error="Название не может быть пустым",
                title="Редактирование категории",
            )

        if (category["name"] or "").strip() == name:
            return self.redirect(
                _redirect_target_after_modal_save(self, "/categories")
            )

        try:
            self.db.execute(
                "UPDATE categories SET name=? WHERE id=?", (name, cid)
            )
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            if "categories.name" in str(e):
                return self.render(
                    "category_edit.html",
                    category=dict(
                        {**dict(category), "name": name}
                    ),
                    error="Такая категория уже существует",
                    title="Редактирование категории",
                )
            raise

        self.audit(
            event="edit_category",
            entity="category",
            entity_id=cid,
            before={"name": (category["name"] or "").strip()},
            after={"name": name},
            message=name,
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["categories", f"category/{cid}"],
            by=ws_cid,
            event={"kind": "edit_category", "id": cid, "name": name},
        )

        return self.redirect(
            _redirect_with_toast(
                _redirect_target_after_modal_save(self, "/categories"),
                title="Категория обновлена",
                body=name,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Subcategories
# ═══════════════════════════════════════════════════════════════════════════


class SubcategoriesHandler(AdminRequiredHandler):
    def get(self):
        category_id_raw = self.get_argument("category_id", "").strip()

        categories_all = fetchall(
            self.db, "SELECT id, name FROM categories ORDER BY name", ()
        )

        if not category_id_raw.isdigit():
            if categories_all:
                category_id = int(categories_all[0]["id"])
            else:
                return self.render(
                    "subcategories.html",
                    subcategories=[],
                    categories_all=[dict(r) for r in categories_all],
                    category=None,
                    category_id="",
                    category_name="",
                    error=None,
                    form={},
                    page=1,
                    pages=1,
                    per_page=LIST_DEFAULT_PER_PAGE,
                    sort="created",
                    dir="desc",
                    title="Подкатегории",
                )
        else:
            category_id = int(category_id_raw)

        category = fetchone(
            self.db,
            "SELECT id, name FROM categories WHERE id=?",
            (category_id,),
        )
        if not category:
            abort(404)

        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 100_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")

        sort_map = {
            "id": "sc.id",
            "name": "sc.name",
            "created": "sc.created_at",
            "products": "(SELECT COUNT(*) FROM products p WHERE p.subcategory_id=sc.id)",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        row = fetchone(
            self.db,
            "SELECT COUNT(*) AS c FROM subcategories WHERE category_id=?",
            (category_id,),
        )
        total = int(row["c"]) if row else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        subcategories = fetchall(
            self.db,
            f"""
            SELECT
              sc.id,
              sc.name,
              sc.created_at,
              (SELECT COUNT(*) FROM products p WHERE p.subcategory_id=sc.id) AS product_count
            FROM subcategories sc
            WHERE sc.category_id = ?
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (category_id, per_page, offset),
        )

        return self.render(
            "subcategories.html",
            subcategories=[dict(r) for r in subcategories],
            categories_all=[dict(r) for r in categories_all],
            category=dict(category),
            category_id=category_id,
            category_name=category["name"],
            error=None,
            form={},
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title=f"Подкатегории — {category['name']}",
        )


class SubcategoryCreateHandler(AdminRequiredHandler):
    def post(self):
        category_id_raw = self.get_body_argument("category_id", "").strip()
        if not category_id_raw.isdigit():
            abort(400)
        category_id = int(category_id_raw)

        category = fetchone(
            self.db,
            "SELECT id, name FROM categories WHERE id=?",
            (category_id,),
        )
        if not category:
            abort(404)

        name = self.get_body_argument("name", "").strip()
        return_to = (self.get_body_argument("return_to", "") or "").strip()
        list_url = f"/subcategories?category_id={category_id}"
        success_url = return_to if return_to.startswith("/products") else list_url

        if not name:
            return self.redirect(
                _redirect_with_toast(
                        success_url,
                    title="Подкатегория не добавлена",
                    body="Введите название",
                    variant="error",
                )
            )

        try:
            cur = self.db.execute(
                "INSERT INTO subcategories(category_id, name) VALUES(?,?)",
                (category_id, name),
            )
            new_id = int(cur.lastrowid)
            cur.close()
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            if "subcategories" in str(e).lower():
                return self.redirect(
                    _redirect_with_toast(
                        success_url,
                        title="Подкатегория не добавлена",
                        body="Такая подкатегория уже существует в этой категории",
                        variant="error",
                    )
                )
            raise

        self.audit(
            event="create_subcategory",
            entity="subcategory",
            entity_id=new_id,
            after={"name": name, "category_id": category_id},
            message=name,
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["subcategories", f"category/{category_id}"],
            by=ws_cid,
            event={"kind": "add_subcategory", "name": name, "category_id": category_id},
        )

        return self.redirect(
            _redirect_with_toast(
                success_url, title="Подкатегория добавлена", body=name
            )
        )


class SubcategoryEditHandler(AdminRequiredHandler):
    def get(self, subcategory_id: str):
        sc = fetchone(
            self.db,
            """
            SELECT sc.id, sc.category_id, sc.name, sc.created_at, c.name AS category_name
            FROM subcategories sc
            JOIN categories c ON c.id = sc.category_id
            WHERE sc.id=?
            """,
            (int(subcategory_id),),
        )
        if not sc:
            abort(404)
        return self.render(
            "subcategory_edit.html",
            subcategory=dict(sc),
            error=None,
            title="Редактирование подкатегории",
        )

    def post(self, subcategory_id: str):
        sid = int(subcategory_id)
        name = self.get_body_argument("name", "").strip()

        old = fetchone(
            self.db,
            "SELECT id, category_id, name FROM subcategories WHERE id=?",
            (sid,),
        )
        if not old:
            abort(404)

        list_url = f"/subcategories?category_id={old['category_id']}"

        if not name:
            sc = fetchone(
                self.db,
                """
                SELECT sc.id, sc.category_id, sc.name, sc.created_at, c.name AS category_name
                FROM subcategories sc
                JOIN categories c ON c.id = sc.category_id
                WHERE sc.id=?
                """,
                (sid,),
            )
            return self.render(
                "subcategory_edit.html",
                subcategory=dict(sc) if sc else {"id": sid, "name": "", "category_id": old["category_id"]},
                error="Название не может быть пустым",
                title="Редактирование подкатегории",
            )

        if (old["name"] or "").strip() == name:
            return self.redirect(
                _redirect_target_after_modal_save(self, list_url)
            )

        try:
            self.db.execute(
                "UPDATE subcategories SET name=? WHERE id=?", (name, sid)
            )
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            if "subcategories" in str(e).lower():
                sc = fetchone(
                    self.db,
                    """
                    SELECT sc.id, sc.category_id, sc.name, sc.created_at, c.name AS category_name
                    FROM subcategories sc
                    JOIN categories c ON c.id = sc.category_id
                    WHERE sc.id=?
                    """,
                    (sid,),
                )
                return self.render(
                    "subcategory_edit.html",
                    subcategory=dict({**dict(sc), "name": name}) if sc else {"id": sid, "name": name},
                    error="Такая подкатегория уже существует в этой категории",
                    title="Редактирование подкатегории",
                )
            raise

        self.audit(
            event="edit_subcategory",
            entity="subcategory",
            entity_id=sid,
            before={"name": (old["name"] or "").strip()},
            after={"name": name},
            message=name,
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["subcategories", f"category/{old['category_id']}"],
            by=ws_cid,
            event={"kind": "edit_subcategory", "id": sid, "name": name},
        )

        return self.redirect(
            _redirect_with_toast(
                _redirect_target_after_modal_save(self, list_url),
                title="Подкатегория обновлена",
                body=name,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════


class ApiSubcategoriesHandler(AdminRequiredHandler):
    def get(self):
        category_id_raw = (self.get_argument("category_id", "") or "").strip()
        if not category_id_raw.isdigit():
            return jsonify({"items": []})
        rows = fetchall(
            self.db,
            "SELECT id, name FROM subcategories WHERE category_id=? ORDER BY name",
            (int(category_id_raw),),
        )
        return jsonify({"items": [{"id": int(r["id"]), "name": r["name"]} for r in rows]})


class ApiProductsHandler(AdminRequiredHandler):
    def get(self):
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            return jsonify({"items": []})
        category_id_raw = (self.get_argument("category_id", "") or "").strip()
        subcategory_id_raw = (self.get_argument("subcategory_id", "") or "").strip()
        q = (self.get_argument("q", "") or "").strip()
        where = ["p.is_active=1"]
        params: list = [warehouse_id]
        if category_id_raw.isdigit():
            where.append("sc.category_id=?")
            params.append(int(category_id_raw))
        if subcategory_id_raw.isdigit():
            where.append("p.subcategory_id=?")
            params.append(int(subcategory_id_raw))
        if q:
            where.append("(p.name LIKE ? OR COALESCE(p.sku,'') LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        rows = fetchall(
            self.db,
            f"""
            SELECT
              p.id,
              p.name,
              p.sku,
              p.unit,
              p.sale_price,
              COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END),0)
                - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END),0) AS balance
            FROM products p
            JOIN subcategories sc ON sc.id=p.subcategory_id
            LEFT JOIN stock_moves sm ON sm.product_id=p.id AND sm.warehouse_id=?
            WHERE {" AND ".join(where)}
            GROUP BY p.id
            ORDER BY p.name
            """,
            tuple(params),
        )
        return jsonify(
            {
                "items": [
                    {
                        "id": int(r["id"]),
                        "name": r["name"],
                        "sku": r["sku"] or "",
                        "unit": r["unit"] or "шт",
                        "sale_price": float(r["sale_price"] or 0),
                        "balance": float(r["balance"] or 0),
                    }
                    for r in rows
                ]
            }
        )


class ApiStockBalanceHandler(AdminRequiredHandler):
    def get(self):
        product_id_raw = (self.get_argument("product_id", "") or "").strip()
        if not product_id_raw.isdigit():
            return jsonify({"balance": 0})
        if self.current_warehouse_id is None:
            return jsonify({"balance": 0})
        return jsonify(
            {
                "balance": _get_stock_balance(
                    self.db, int(product_id_raw), self.current_warehouse_id
                )
            }
        )


# ═══════════════════════════════════════════════════════════════════════════
# Products
# ═══════════════════════════════════════════════════════════════════════════


class ProductsHandler(AdminRequiredHandler):
    def get(self):
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 10_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")
        q = self.get_argument("q", "").strip()
        category_id_raw = self.get_argument("category_id", "").strip()
        subcategory_id_raw = self.get_argument("subcategory_id", "").strip()

        sort_map = {
            "id": "p.id",
            "name": "p.name",
            "sku": "p.sku",
            "created": "p.created_at",
            "stock": "balance",
            "balance": "balance",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        where_parts = ["1=1"]
        params: list = []

        if category_id_raw.isdigit():
            where_parts.append("sc.category_id = ?")
            params.append(int(category_id_raw))

        if subcategory_id_raw.isdigit():
            where_parts.append("p.subcategory_id = ?")
            params.append(int(subcategory_id_raw))

        if q:
            where_parts.append(
                "(p.name LIKE ? OR p.sku LIKE ? OR sc.name LIKE ?)"
            )
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q])

        where_sql = " AND ".join(where_parts)

        cnt = fetchone(
            self.db,
            f"""
            SELECT COUNT(*) AS c
            FROM products p
            JOIN subcategories sc ON sc.id = p.subcategory_id
            WHERE {where_sql}
            """,
            tuple(params),
        )
        total = int(cnt["c"]) if cnt else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        products = fetchall(
            self.db,
            f"""
            SELECT
              p.id,
              p.sku,
              p.name,
              p.image_path,
              p.unit,
              p.purchase_price,
              p.sale_price,
              p.min_stock,
              p.is_active,
              p.created_at,
              sc.category_id,
              sc.name AS subcategory_name,
              cat.name AS category_name,
              COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END), 0)
                - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END), 0)
                AS balance
            FROM products p
            JOIN subcategories sc ON sc.id = p.subcategory_id
            JOIN categories cat ON cat.id = sc.category_id
            LEFT JOIN stock_moves sm ON sm.product_id = p.id AND sm.warehouse_id=?
            WHERE {where_sql}
            GROUP BY p.id
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (warehouse_id,) + tuple(params) + (per_page, offset),
        )

        categories = fetchall(
            self.db, "SELECT id, name FROM categories ORDER BY name", ()
        )
        subcategories = fetchall(
            self.db,
            "SELECT id, category_id, name FROM subcategories ORDER BY name",
            (),
        )

        return self.render(
            "products.html",
            products=[dict(r) for r in products],
            categories=[dict(r) for r in categories],
            subcategories=[dict(r) for r in subcategories],
            error=None,
            form={},
            q=q,
            selected_category_id=category_id_raw,
            selected_subcategory_id=subcategory_id_raw,
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title="Товары",
        )

    def post(self):
        if not self.is_role_admin:
            abort(403)
        name = self.get_body_argument("name", "").strip()
        sku = self.get_body_argument("sku", "").strip() or None
        category_id_raw = self.get_body_argument("category_id", "").strip()
        subcategory_id_raw = self.get_body_argument("subcategory_id", "").strip()
        unit = self.get_body_argument("unit", "шт").strip() or "шт"
        min_stock_raw = self.get_body_argument("min_stock", "0").strip()
        purchase_price_raw = self.get_body_argument("purchase_price", "0").strip()
        sale_price_raw = self.get_body_argument("sale_price", "0").strip()

        if not name:
            return self.redirect(
                _redirect_with_toast(
                    "/products",
                    title="Товар не добавлен",
                    body="Введите название",
                    variant="error",
                )
            )
        if not subcategory_id_raw.isdigit():
            return self.redirect(
                _redirect_with_toast(
                    "/products",
                    title="Товар не добавлен",
                    body="Выберите подкатегорию",
                    variant="error",
                )
            )
        subcategory_id = int(subcategory_id_raw)
        if category_id_raw.isdigit():
            sc = fetchone(
                self.db,
                "SELECT id FROM subcategories WHERE id=? AND category_id=?",
                (subcategory_id, int(category_id_raw)),
            )
            if not sc:
                return self.redirect(
                    _redirect_with_toast(
                        "/products",
                        title="Товар не добавлен",
                        body="Подкатегория не принадлежит выбранной категории",
                        variant="error",
                    )
                )

        try:
            min_stock = max(0.0, float(min_stock_raw))
        except Exception:
            min_stock = 0.0
        try:
            purchase_price = max(0.0, float(purchase_price_raw)) if self.is_role_admin else 0.0
        except Exception:
            purchase_price = 0.0
        try:
            sale_price = max(0.0, float(sale_price_raw))
        except Exception:
            sale_price = 0.0

        image_path = None
        try:
            image_path = _save_product_image(request.files.get("image"))
        except ValueError as e:
            return self.redirect(
                _redirect_with_toast(
                    "/products",
                    title="Товар не добавлен",
                    body=str(e),
                    variant="error",
                )
            )

        try:
            cur = self.db.execute(
                """
                INSERT INTO products(subcategory_id, sku, name, image_path, unit, purchase_price, sale_price, min_stock)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (subcategory_id, sku, name, image_path, unit, purchase_price, sale_price, min_stock),
            )
            new_id = int(cur.lastrowid)
            cur.close()
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            _remove_product_image(image_path)
            if "products.sku" in str(e):
                return self.redirect(
                    _redirect_with_toast(
                        "/products",
                        title="Товар не добавлен",
                        body="Артикул (SKU) уже используется",
                        variant="error",
                    )
                )
            raise

        self.audit(
            event="create_product",
            entity="product",
            entity_id=new_id,
            after={
                "name": name,
                "sku": sku,
                "image_path": image_path,
                "subcategory_id": subcategory_id,
                "unit": unit,
                "purchase_price": purchase_price,
                "sale_price": sale_price,
                "min_stock": min_stock,
            },
            message=name,
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["products"],
            by=ws_cid,
            event={"kind": "add_product", "name": name},
        )

        return self.redirect(
            _redirect_with_toast(
                "/products", title="Товар добавлен", body=name
            )
        )


class ProductEditHandler(AdminRequiredHandler):
    def get(self, product_id: str):
        product = fetchone(
            self.db,
            """
            SELECT
              p.id, p.subcategory_id, p.sku, p.name, p.image_path, p.unit,
              p.purchase_price, p.sale_price, p.min_stock, p.is_active, p.created_at,
              sc.name AS subcategory_name,
              cat.name AS category_name,
              sc.category_id AS category_id
            FROM products p
            JOIN subcategories sc ON sc.id = p.subcategory_id
            JOIN categories cat ON cat.id = sc.category_id
            WHERE p.id=?
            """,
            (int(product_id),),
        )
        if not product:
            abort(404)

        balance = _get_stock_balance(
            self.db, int(product_id), self.current_warehouse_id
        ) if self.current_warehouse_id is not None else 0.0

        categories = fetchall(
            self.db, "SELECT id, name FROM categories ORDER BY name", ()
        )
        subcategories = fetchall(
            self.db,
            "SELECT id, category_id, name FROM subcategories ORDER BY name",
            (),
        )

        return self.render(
            "product_edit.html",
            product=dict(product),
            balance=balance,
            categories=[dict(r) for r in categories],
            subcategories=[dict(r) for r in subcategories],
            error=None,
            title="Редактирование товара",
        )

    def post(self, product_id: str):
        if self.is_role_viewer:
            abort(403)
        pid = int(product_id)
        name = self.get_body_argument("name", "").strip()
        sku = self.get_body_argument("sku", "").strip() or None
        category_id_raw = self.get_body_argument("category_id", "").strip()
        subcategory_id_raw = self.get_body_argument("subcategory_id", "").strip()
        unit = self.get_body_argument("unit", "шт").strip() or "шт"
        min_stock_raw = self.get_body_argument("min_stock", "0").strip()
        purchase_price_raw = self.get_body_argument("purchase_price", "0").strip()
        sale_price_raw = self.get_body_argument("sale_price", "0").strip()
        remove_image = 1 if self.get_body_argument("remove_image", "") == "1" else 0
        is_active = 1 if self.get_body_argument("is_active", None) is not None else 0

        old = fetchone(
            self.db,
            "SELECT subcategory_id, sku, name, image_path, unit, purchase_price, sale_price, min_stock, is_active FROM products WHERE id=?",
            (pid,),
        )
        if not old:
            abort(404)

        if not name:
            return self.redirect(
                _redirect_with_toast(
                    f"/products/{pid}",
                    title="Товар не сохранён",
                    body="Название не может быть пустым",
                    variant="error",
                )
            )
        if not subcategory_id_raw.isdigit():
            return self.redirect(
                _redirect_with_toast(
                    f"/products/{pid}",
                    title="Товар не сохранён",
                    body="Выберите подкатегорию",
                    variant="error",
                )
            )
        subcategory_id = int(subcategory_id_raw)
        if category_id_raw.isdigit():
            sc = fetchone(
                self.db,
                "SELECT id FROM subcategories WHERE id=? AND category_id=?",
                (subcategory_id, int(category_id_raw)),
            )
            if not sc:
                return self.redirect(
                    _redirect_with_toast(
                        f"/products/{pid}",
                        title="Товар не сохранён",
                        body="Подкатегория не принадлежит выбранной категории",
                        variant="error",
                    )
                )

        try:
            min_stock = max(0.0, float(min_stock_raw))
        except Exception:
            min_stock = 0.0
        try:
            purchase_price = (
                max(0.0, float(purchase_price_raw))
                if self.is_role_admin
                else float(old["purchase_price"] or 0)
            )
        except Exception:
            purchase_price = float(old["purchase_price"] or 0)
        try:
            sale_price = max(0.0, float(sale_price_raw))
        except Exception:
            sale_price = float(old["sale_price"] or 0)

        new_image_path = old["image_path"] or None
        uploaded_image = None
        try:
            uploaded_image = _save_product_image(request.files.get("image"))
        except ValueError as e:
            return self.redirect(
                _redirect_with_toast(
                    f"/products/{pid}",
                    title="Товар не сохранён",
                    body=str(e),
                    variant="error",
                )
            )
        if uploaded_image:
            new_image_path = uploaded_image
        elif remove_image:
            new_image_path = None

        if (
            (old["name"] or "").strip() == name
            and (old["sku"] or None) == sku
            and (old["image_path"] or None) == new_image_path
            and int(old["subcategory_id"]) == subcategory_id
            and (old["unit"] or "шт") == unit
            and float(old["purchase_price"] or 0) == purchase_price
            and float(old["sale_price"] or 0) == sale_price
            and float(old["min_stock"]) == min_stock
            and int(old["is_active"]) == is_active
        ):
            return self.redirect(
                _redirect_target_after_modal_save(self, "/products")
            )

        try:
            self.db.execute(
                """
                UPDATE products
                SET name=?, sku=?, image_path=?, subcategory_id=?, unit=?, purchase_price=?, sale_price=?, min_stock=?, is_active=?
                WHERE id=?
                """,
                (name, sku, new_image_path, subcategory_id, unit, purchase_price, sale_price, min_stock, is_active, pid),
            )
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            _remove_product_image(uploaded_image)
            if "products.sku" in str(e):
                return self.redirect(
                    _redirect_with_toast(
                        f"/products/{pid}",
                        title="Товар не сохранён",
                        body="Артикул (SKU) уже используется",
                        variant="error",
                    )
                )
            raise

        self.audit(
            event="edit_product",
            entity="product",
            entity_id=pid,
            before={
                "name": (old["name"] or "").strip(),
                "sku": old["sku"],
                "image_path": old["image_path"],
                "subcategory_id": int(old["subcategory_id"]),
                "unit": old["unit"],
                "purchase_price": float(old["purchase_price"] or 0),
                "sale_price": float(old["sale_price"] or 0),
                "min_stock": float(old["min_stock"]),
                "is_active": int(old["is_active"]),
            },
            after={
                "name": name,
                "sku": sku,
                "image_path": new_image_path,
                "subcategory_id": subcategory_id,
                "unit": unit,
                "purchase_price": purchase_price,
                "sale_price": sale_price,
                "min_stock": min_stock,
                "is_active": is_active,
            },
            message=name,
        )

        if uploaded_image and old["image_path"] and old["image_path"] != uploaded_image:
            _remove_product_image(old["image_path"])
        if remove_image and old["image_path"] and not uploaded_image:
            _remove_product_image(old["image_path"])

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["products", f"product/{pid}"],
            by=ws_cid,
            event={"kind": "edit_product", "id": pid, "name": name},
        )

        return self.redirect(
            _redirect_with_toast(
                _redirect_target_after_modal_save(self, "/products"),
                title="Товар обновлён",
                body=name,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Catalog workspace
# ═══════════════════════════════════════════════════════════════════════════


class CatalogHandler(AdminRequiredHandler):
    def get(self):
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)
        categories = fetchall(self.db, "SELECT id, name FROM categories ORDER BY name", ())
        subcategories = fetchall(
            self.db,
            "SELECT id, category_id, name FROM subcategories ORDER BY name",
            (),
        )
        products_rows = fetchall(
            self.db,
            """
            SELECT
              p.id, p.name, p.sku, p.unit, p.image_path, p.sale_price,
              p.subcategory_id, p.is_active,
              sc.category_id,
              COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END), 0)
                - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END), 0) AS balance
            FROM products p
            JOIN subcategories sc ON sc.id = p.subcategory_id
            LEFT JOIN stock_moves sm ON sm.product_id = p.id AND sm.warehouse_id=?
            GROUP BY p.id
            ORDER BY p.name
            """,
            (warehouse_id,),
        )
        return self.render(
            "catalog.html",
            categories=[dict(r) for r in categories],
            subcategories=[dict(r) for r in subcategories],
            products=[dict(r) for r in products_rows],
            title="Каталог",
        )


class ApiCategoryInlineHandler(AdminRequiredHandler):
    def post(self):
        action = (request.form.get("action") or "").strip()
        if action == "add":
            name = (request.form.get("name") or "").strip()
            if not name:
                return jsonify({"ok": False, "error": "Название не указано"}), 400
            try:
                cur = self.db.execute("INSERT INTO categories(name) VALUES(?)", (name,))
                self.db.commit()
                return jsonify({"ok": True, "id": cur.lastrowid, "name": name})
            except sqlite3.IntegrityError:
                self.db.rollback()
                return jsonify({"ok": False, "error": "Категория уже существует"}), 409
        elif action == "rename":
            cat_id = request.form.get("id", "").strip()
            name = (request.form.get("name") or "").strip()
            if not cat_id.isdigit() or not name:
                return jsonify({"ok": False, "error": "Неверные данные"}), 400
            try:
                self.db.execute("UPDATE categories SET name=? WHERE id=?", (name, int(cat_id)))
                self.db.commit()
                return jsonify({"ok": True, "id": int(cat_id), "name": name})
            except sqlite3.IntegrityError:
                self.db.rollback()
                return jsonify({"ok": False, "error": "Категория с таким именем уже существует"}), 409
        return jsonify({"ok": False, "error": "Unknown action"}), 400


class ApiSubcategoryInlineHandler(AdminRequiredHandler):
    def post(self):
        action = (request.form.get("action") or "").strip()
        if action == "add":
            category_id = (request.form.get("category_id") or "").strip()
            name = (request.form.get("name") or "").strip()
            if not category_id.isdigit() or not name:
                return jsonify({"ok": False, "error": "Данные неполные"}), 400
            try:
                cur = self.db.execute(
                    "INSERT INTO subcategories(category_id, name) VALUES(?,?)",
                    (int(category_id), name),
                )
                self.db.commit()
                return jsonify({"ok": True, "id": cur.lastrowid, "category_id": int(category_id), "name": name})
            except sqlite3.IntegrityError:
                self.db.rollback()
                return jsonify({"ok": False, "error": "Подкатегория уже существует"}), 409
        elif action == "rename":
            sub_id = request.form.get("id", "").strip()
            name = (request.form.get("name") or "").strip()
            if not sub_id.isdigit() or not name:
                return jsonify({"ok": False, "error": "Неверные данные"}), 400
            try:
                self.db.execute("UPDATE subcategories SET name=? WHERE id=?", (name, int(sub_id)))
                self.db.commit()
                return jsonify({"ok": True, "id": int(sub_id), "name": name})
            except sqlite3.IntegrityError:
                self.db.rollback()
                return jsonify({"ok": False, "error": "Подкатегория с таким именем уже существует"}), 409
        return jsonify({"ok": False, "error": "Unknown action"}), 400


# ═══════════════════════════════════════════════════════════════════════════
# Stock operations
# ═══════════════════════════════════════════════════════════════════════════


class StockInDocsHandler(AdminRequiredHandler):
    def get(self):
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 10_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")
        status_filter = self.get_argument("status", "").strip()
        q = self.get_argument("q", "").strip()

        sort_map = {
            "id": "d.id",
            "created": "d.created_at",
            "doc_no": "d.doc_no",
            "status": "d.status",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        where_parts: list[str] = ["1=1"]
        params: list = []

        if not self.is_role_admin:
            if self.current_warehouse_id is None:
                abort(403)
            where_parts.append("d.warehouse_id = ?")
            params.append(self.current_warehouse_id)

        if status_filter in ("draft", "posted"):
            where_parts.append("d.status = ?")
            params.append(status_filter)

        if q:
            where_parts.append("(d.doc_no LIKE ? OR d.note LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])

        where_sql = " AND ".join(where_parts)

        cnt = fetchone(
            self.db,
            f"SELECT COUNT(*) AS c FROM stock_in_docs d WHERE {where_sql}",
            tuple(params),
        )
        total = int(cnt["c"]) if cnt else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        docs = fetchall(
            self.db,
            f"""
            SELECT
              d.id, d.doc_no, d.status, d.note,
              d.created_by, d.created_at, d.posted_at, d.warehouse_id,
              w.name AS warehouse_name,
              u.username AS created_by_name,
              (SELECT COUNT(*) FROM stock_in_items i WHERE i.doc_id = d.id) AS item_count,
              (SELECT COALESCE(SUM(i.qty), 0) FROM stock_in_items i WHERE i.doc_id = d.id) AS total_qty,
              (SELECT COALESCE(SUM(i.qty * i.unit_price), 0) FROM stock_in_items i WHERE i.doc_id = d.id) AS total_amount
            FROM stock_in_docs d
            LEFT JOIN users u ON u.id = d.created_by
            LEFT JOIN warehouses w ON w.id = d.warehouse_id
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (per_page, offset),
        )

        return self.render(
            "stock_in_docs.html",
            docs=[dict(r) for r in docs],
            filter_status=status_filter,
            filter_q=q,
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title="Документы прихода",
        )


class StockInHandler(AdminRequiredHandler):
    def get(self):
        products = fetchall(
            self.db,
            """
            SELECT
              p.id, p.name, p.sku, p.unit, p.subcategory_id,
              p.image_path,
              p.is_active,
              p.purchase_price,
              sc.category_id
            FROM products p
            JOIN subcategories sc ON sc.id = p.subcategory_id
            ORDER BY p.name
            """,
            (),
        )
        categories = fetchall(self.db, "SELECT id, name FROM categories ORDER BY name", ())
        subcategories = fetchall(
            self.db,
            "SELECT id, category_id, name FROM subcategories ORDER BY name",
            (),
        )
        doc_id_raw = (self.get_argument("doc_id", "") or "").strip()
        doc = None
        items: list[dict] = []
        if doc_id_raw.isdigit():
            doc = fetchone(
                self.db,
                "SELECT id, doc_no, status, note, created_at, warehouse_id FROM stock_in_docs WHERE id=?",
                (int(doc_id_raw),),
            )
            if doc and not self.can_access_warehouse(doc["warehouse_id"]):
                abort(404)
            if doc:
                item_rows = fetchall(
                    self.db,
                    """
                    SELECT
                      i.id, i.product_id, i.qty, i.unit_price,
                      p.name AS product_name, p.sku, p.unit, p.image_path
                    FROM stock_in_items i
                    JOIN products p ON p.id=i.product_id
                    WHERE i.doc_id=?
                    ORDER BY i.id
                    """,
                    (int(doc_id_raw),),
                )
                items = [dict(r) for r in item_rows]

        can_edit_posted = bool(
            doc and (doc["status"] or "") == "posted" and self.is_role_admin
        )

        products_out = [dict(r) for r in products]
        if not self.is_role_admin:
            for product in products_out:
                product.pop("purchase_price", None)

        return self.render(
            "stock_in.html",
            products=products_out,
            categories=[dict(r) for r in categories],
            subcategories=[dict(r) for r in subcategories],
            doc=dict(doc) if doc else None,
            next_doc_no=_doc_no_next(self.db, "IN") if not doc else None,
            items=items,
            can_edit_posted=can_edit_posted,
            form={},
            error=None,
            title="Приход товара",
        )

    def post(self):
        action = (self.get_body_argument("action", "save_draft") or "save_draft").strip()
        if self.is_role_viewer and action != "save_draft":
            abort(403)
        doc_id_raw = self.get_body_argument("doc_id", "").strip()
        note = self.get_body_argument("note", "").strip() or None
        created_at_raw = self.get_body_argument("created_at", "").strip()
        product_ids = request.form.getlist("product_id[]")
        qty_values = request.form.getlist("qty[]")
        price_values = request.form.getlist("unit_price[]")
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)

        if len(product_ids) != len(qty_values):
            return self.redirect(
                _redirect_with_toast("/stock/in", title="Ошибка", body="Некорректные позиции", variant="error")
            )

        line_rows: list[tuple[int, float, float | None]] = []
        for i, pid_raw in enumerate(product_ids):
            pid_s = (pid_raw or "").strip()
            qty_s = (qty_values[i] or "").strip()
            if not pid_s and not qty_s:
                continue
            if not pid_s.isdigit():
                return self.redirect(
                    _redirect_with_toast("/stock/in", title="Ошибка", body="Выберите товар в каждой строке", variant="error")
                )
            try:
                qty = float(qty_s)
                if qty <= 0:
                    raise ValueError
            except Exception:
                return self.redirect(
                    _redirect_with_toast("/stock/in", title="Ошибка", body="Количество должно быть положительным", variant="error")
                )
            submitted_price = None
            if self.is_role_admin:
                try:
                    submitted_price = float(price_values[i])
                    if submitted_price < 0:
                        raise ValueError
                except (IndexError, TypeError, ValueError):
                    return self.redirect(
                        _redirect_with_toast("/stock/in", title="Ошибка", body="Цена прихода должна быть неотрицательной", variant="error")
                    )
            line_rows.append((int(pid_s), qty, submitted_price))

        line_items = [(pid, qty) for pid, qty, _ in line_rows]

        if not line_items:
            return self.redirect(
                _redirect_with_toast("/stock/in", title="Ошибка", body="Добавьте хотя бы одну позицию", variant="error")
            )

        created_at_db = None
        if created_at_raw:
            try:
                created_at_db = local_wall_to_utc_db_str(created_at_raw)
            except Exception:
                created_at_db = None

        is_posted_edit = False
        before_snapshot: dict | None = None

        try:
            self.db.execute("BEGIN")
            old_prices: dict[int, float] = {}

            if doc_id_raw.isdigit():
                doc_id = int(doc_id_raw)
                doc = fetchone(self.db, "SELECT id, status, note, warehouse_id FROM stock_in_docs WHERE id=?", (doc_id,))
                if not doc:
                    self.db.rollback()
                    abort(404)
                if not self.can_access_warehouse(doc["warehouse_id"]):
                    self.db.rollback()
                    abort(404)
                warehouse_id = int(doc["warehouse_id"])
                old_prices = {
                    int(r["product_id"]): float(r["unit_price"] or 0)
                    for r in fetchall(
                        self.db,
                        "SELECT product_id, unit_price FROM stock_in_items WHERE doc_id=? ORDER BY id",
                        (doc_id,),
                    )
                }
                if (doc["status"] or "") == "posted":
                    if not self.is_role_admin:
                        self.db.rollback()
                        return self.redirect(
                            _redirect_with_toast(
                                "/stock/in",
                                title="Ошибка",
                                body="Редактирование проведённых документов доступно только руководителю",
                                variant="error",
                            )
                        )
                    if action != "update_posted":
                        self.db.rollback()
                        return self.redirect(
                            _redirect_with_toast(
                                f"/stock/in?doc_id={doc_id}",
                                title="Ошибка",
                                body="Проведённый документ можно только сохранить с изменениями",
                                variant="error",
                            )
                        )
                    is_posted_edit = True
                    before_snapshot = {
                        "note": doc["note"],
                        "items": _doc_line_items_snapshot(self.db, doc_id, "in"),
                    }
                    self.db.execute("DELETE FROM stock_moves WHERE stock_in_doc_id=?", (doc_id,))
                    if created_at_db:
                        self.db.execute(
                            "UPDATE stock_in_docs SET note=?, created_at=? WHERE id=?",
                            (note, created_at_db, doc_id),
                        )
                    else:
                        self.db.execute("UPDATE stock_in_docs SET note=? WHERE id=?", (note, doc_id))
                else:
                    self.db.execute("UPDATE stock_in_docs SET note=? WHERE id=?", (note, doc_id))
                self.db.execute("DELETE FROM stock_in_items WHERE doc_id=?", (doc_id,))
            else:
                cur = self.db.execute(
                    """
                    INSERT INTO stock_in_docs(doc_no, warehouse_id, status, note, created_by, created_at)
                    VALUES(?,?,'draft',?,?,COALESCE(?, datetime('now')))
                    """,
                    (_doc_no_next(self.db, "IN"), warehouse_id, note, int(self.current_admin_id), created_at_db),
                )
                doc_id = int(cur.lastrowid)
                cur.close()

            for pid, qty, submitted_price in line_rows:
                if self.is_role_admin:
                    unit_price = float(submitted_price or 0)
                elif pid in old_prices:
                    unit_price = old_prices[pid]
                else:
                    product = fetchone(self.db, "SELECT purchase_price FROM products WHERE id=?", (pid,))
                    unit_price = float(product["purchase_price"] or 0) if product else 0.0
                self.db.execute(
                    "INSERT INTO stock_in_items(doc_id, product_id, qty, unit_price) VALUES(?,?,?,?)",
                    (doc_id, pid, qty, unit_price),
                )

            if action == "post":
                _apply_stock_in_moves(
                    self.db,
                    doc_id,
                    line_items,
                    int(self.current_admin_id),
                    note,
                    created_at_db,
                    warehouse_id,
                )
                self.db.execute(
                    "UPDATE stock_in_docs SET status='posted', posted_at=datetime('now') WHERE id=?",
                    (doc_id,),
                )
            elif is_posted_edit:
                _apply_stock_in_moves(
                    self.db,
                    doc_id,
                    line_items,
                    int(self.current_admin_id),
                    note,
                    created_at_db,
                    warehouse_id,
                )
                self.db.execute(
                    "UPDATE stock_in_docs SET posted_at=datetime('now') WHERE id=?",
                    (doc_id,),
                )

            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            return self.redirect(
                _redirect_with_toast("/stock/in", title="Ошибка", body="Не удалось сохранить документ", variant="error")
            )

        if is_posted_edit:
            doc_event = "stock_in_post_edit"
            toast_title = "Изменения сохранены"
            redirect_url = f"/stock/in?doc_id={doc_id}"
            audit_after = {
                "note": note,
                "items": len(line_items),
                "status": "posted",
            }
            audit_before = before_snapshot
        else:
            doc_event = "stock_in_post" if action == "post" else "stock_in_draft_save"
            toast_title = "Документ проведён" if action == "post" else "Черновик сохранён"
            redirect_url = "/stock/in/docs" if action == "post" else f"/stock/in?doc_id={doc_id}"
            audit_after = {
                "note": note,
                "items": len(line_items),
                "status": ("posted" if action == "post" else "draft"),
            }
            audit_before = None

        self.audit(
            event=doc_event,
            entity="stock_in_doc",
            entity_id=doc_id,
            before=audit_before,
            after=audit_after,
            message=f"Документ прихода #{doc_id}",
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["stock_moves", "dashboard", "stock_in"],
            by=ws_cid,
            event={"kind": doc_event, "doc_id": doc_id},
        )

        return self.redirect(
            _redirect_with_toast(
                redirect_url,
                title=toast_title,
                body=f"Позиции: {len(line_items)}",
            )
        )


class StockOutDocsHandler(AdminRequiredHandler):
    def get(self):
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 10_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")
        status_filter = self.get_argument("status", "").strip()
        q = self.get_argument("q", "").strip()

        sort_map = {
            "id": "d.id",
            "created": "d.created_at",
            "doc_no": "d.doc_no",
            "status": "d.status",
            "client": "COALESCE(cl.full_name,'')",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        where_parts: list[str] = ["1=1"]
        params: list = []

        if not self.is_role_admin:
            if self.current_warehouse_id is None:
                abort(403)
            where_parts.append("d.warehouse_id = ?")
            params.append(self.current_warehouse_id)

        if status_filter in ("draft", "posted"):
            where_parts.append("d.status = ?")
            params.append(status_filter)

        if q:
            where_parts.append(
                "(d.doc_no LIKE ? OR d.note LIKE ? OR cl.full_name LIKE ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like])

        where_sql = " AND ".join(where_parts)

        cnt = fetchone(
            self.db,
            f"""
            SELECT COUNT(*) AS c
            FROM stock_out_docs d
            LEFT JOIN clients cl ON cl.id = d.client_id
            WHERE {where_sql}
            """,
            tuple(params),
        )
        total = int(cnt["c"]) if cnt else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        docs = fetchall(
            self.db,
            f"""
            SELECT
              d.id, d.doc_no, d.client_id, d.status, d.note,
              d.created_by, d.created_at, d.posted_at, d.warehouse_id,
              w.name AS warehouse_name,
              cl.full_name AS client_name,
              u.username AS created_by_name,
              (SELECT COUNT(*) FROM stock_out_items i WHERE i.doc_id = d.id) AS item_count,
              (SELECT COALESCE(SUM(i.qty), 0) FROM stock_out_items i WHERE i.doc_id = d.id) AS total_qty,
              (SELECT COALESCE(SUM(i.qty * i.unit_price), 0) FROM stock_out_items i WHERE i.doc_id = d.id) AS total_amount
            FROM stock_out_docs d
            LEFT JOIN clients cl ON cl.id = d.client_id
            LEFT JOIN users u ON u.id = d.created_by
            LEFT JOIN warehouses w ON w.id = d.warehouse_id
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (per_page, offset),
        )

        return self.render(
            "stock_out_docs.html",
            docs=[dict(r) for r in docs],
            filter_status=status_filter,
            filter_q=q,
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title="Документы отпуска",
        )


class StockOutHandler(AdminRequiredHandler):
    def get(self):
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)
        requested_doc_id = (self.get_argument("doc_id", "") or "").strip()
        if requested_doc_id.isdigit():
            requested_doc = fetchone(
                self.db,
                "SELECT warehouse_id FROM stock_out_docs WHERE id=?",
                (int(requested_doc_id),),
            )
            if requested_doc:
                if not self.can_access_warehouse(requested_doc["warehouse_id"]):
                    abort(404)
                warehouse_id = int(requested_doc["warehouse_id"])
        products = fetchall(
            self.db,
            """
            SELECT
              p.id, p.name, p.sku, p.unit, p.subcategory_id,
              p.image_path,
              p.is_active,
              p.sale_price,
              sc.category_id,
              COALESCE(SUM(CASE WHEN sm.move_type='in' THEN sm.qty ELSE 0 END),0)
                - COALESCE(SUM(CASE WHEN sm.move_type='out' THEN sm.qty ELSE 0 END),0) AS stock_balance
            FROM products p
            JOIN subcategories sc ON sc.id = p.subcategory_id
            LEFT JOIN stock_moves sm ON sm.product_id = p.id AND sm.warehouse_id=?
            GROUP BY p.id
            ORDER BY p.name
            """,
            (warehouse_id,),
        )
        clients = fetchall(
            self.db,
            "SELECT id, full_name FROM clients WHERE is_active=1 ORDER BY full_name",
            (),
        )
        categories = fetchall(self.db, "SELECT id, name FROM categories ORDER BY name", ())
        subcategories = fetchall(
            self.db,
            "SELECT id, category_id, name FROM subcategories ORDER BY name",
            (),
        )
        doc_id_raw = (self.get_argument("doc_id", "") or "").strip()
        doc = None
        items: list[dict] = []
        if doc_id_raw.isdigit():
            doc = fetchone(
                self.db,
                "SELECT id, doc_no, client_id, status, note, created_at, warehouse_id FROM stock_out_docs WHERE id=?",
                (int(doc_id_raw),),
            )
            if doc and not self.can_access_warehouse(doc["warehouse_id"]):
                abort(404)
            if doc:
                item_rows = fetchall(
                    self.db,
                    """
                    SELECT
                      i.id, i.product_id, i.qty, i.unit_price,
                      p.name AS product_name, p.sku, p.unit, p.image_path,
                      sc.category_id
                    FROM stock_out_items i
                    JOIN products p ON p.id=i.product_id
                    JOIN subcategories sc ON sc.id = p.subcategory_id
                    WHERE i.doc_id=?
                    ORDER BY i.id
                    """,
                    (int(doc_id_raw),),
                )
                items = [dict(r) for r in item_rows]

        can_edit_posted = bool(
            doc and (doc["status"] or "") == "posted" and self.is_role_admin
        )
        doc_qty_by_product = _doc_qty_by_product(items) if can_edit_posted else {}
        if can_edit_posted:
            for it in items:
                pid = int(it["product_id"])
                actual = _get_stock_balance(self.db, pid, int(doc["warehouse_id"]))
                it["stock_balance"] = actual
                it["limit_balance"] = actual + doc_qty_by_product.get(pid, 0.0)
        products_out = []
        for r in products:
            p = dict(r)
            if can_edit_posted:
                pid = int(p["id"])
                actual = float(p["stock_balance"])
                p["limit_balance"] = actual + doc_qty_by_product.get(pid, 0.0)
            products_out.append(p)

        product_category_map = {int(p["id"]): int(p["category_id"]) for p in products_out}
        doc_category_id = None
        if items:
            doc_category_id = int(items[0]["category_id"])

        return self.render(
            "stock_out.html",
            products=products_out,
            clients=[dict(r) for r in clients],
            categories=[dict(r) for r in categories],
            subcategories=[dict(r) for r in subcategories],
            doc=dict(doc) if doc else None,
            next_doc_no=_doc_no_next(self.db, "OUT") if not doc else None,
            items=items,
            can_edit_posted=can_edit_posted,
            doc_qty_by_product=doc_qty_by_product,
            product_category_map=product_category_map,
            doc_category_id=doc_category_id,
            form={},
            error=None,
            title="Отпуск товара",
        )

    def post(self):
        action = (self.get_body_argument("action", "save_draft") or "save_draft").strip()
        if self.is_role_viewer and action != "save_draft":
            abort(403)
        doc_id_raw = self.get_body_argument("doc_id", "").strip()
        client_id_raw = self.get_body_argument("client_id", "").strip()
        note = self.get_body_argument("note", "").strip() or None
        created_at_raw = self.get_body_argument("created_at", "").strip()
        product_ids = request.form.getlist("product_id[]")
        qty_values = request.form.getlist("qty[]")
        price_values = request.form.getlist("unit_price[]")
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)

        if len(product_ids) != len(qty_values):
            return self.redirect(
                _redirect_with_toast("/stock/out", title="Ошибка", body="Некорректные позиции", variant="error")
            )

        client_id = None
        if client_id_raw.isdigit():
            client_id = int(client_id_raw)

        line_rows: list[tuple[int, float, float | None]] = []
        for i, pid_raw in enumerate(product_ids):
            pid_s = (pid_raw or "").strip()
            qty_s = (qty_values[i] or "").strip()
            if not pid_s and not qty_s:
                continue
            if not pid_s.isdigit():
                return self.redirect(
                    _redirect_with_toast("/stock/out", title="Ошибка", body="Выберите товар в каждой строке", variant="error")
                )
            try:
                qty = float(qty_s)
                if qty <= 0:
                    raise ValueError
            except Exception:
                return self.redirect(
                    _redirect_with_toast("/stock/out", title="Ошибка", body="Количество должно быть положительным", variant="error")
                )
            submitted_price = None
            if not self.is_role_viewer:
                try:
                    submitted_price = float(price_values[i])
                    if submitted_price < 0:
                        raise ValueError
                except (IndexError, TypeError, ValueError):
                    return self.redirect(
                        _redirect_with_toast("/stock/out", title="Ошибка", body="Цена отпуска должна быть неотрицательной", variant="error")
                    )
            line_rows.append((int(pid_s), qty, submitted_price))

        line_items = [(pid, qty) for pid, qty, _ in line_rows]

        if not line_items:
            return self.redirect(
                _redirect_with_toast(
                    "/stock/out",
                    title="Ошибка",
                    body="Добавьте хотя бы одну позицию",
                    variant="error",
                )
            )

        cat_err = _stock_out_single_category_error(self.db, [pid for pid, _ in line_items])
        if cat_err:
            back = f"/stock/out?doc_id={doc_id_raw}" if doc_id_raw.isdigit() else "/stock/out"
            return self.redirect(
                _redirect_with_toast(back, title="Ошибка", body=cat_err, variant="error")
            )

        created_at_db = None
        if created_at_raw:
            try:
                created_at_db = local_wall_to_utc_db_str(created_at_raw)
            except Exception:
                created_at_db = None

        is_posted_edit = False
        before_snapshot: dict | None = None

        try:
            self.db.execute("BEGIN")
            old_prices: dict[int, float] = {}

            if doc_id_raw.isdigit():
                doc_id = int(doc_id_raw)
                doc = fetchone(
                    self.db,
                    "SELECT id, status, note, client_id, warehouse_id FROM stock_out_docs WHERE id=?",
                    (doc_id,),
                )
                if not doc:
                    self.db.rollback()
                    abort(404)
                if not self.can_access_warehouse(doc["warehouse_id"]):
                    self.db.rollback()
                    abort(404)
                warehouse_id = int(doc["warehouse_id"])
                old_prices = {
                    int(r["product_id"]): float(r["unit_price"] or 0)
                    for r in fetchall(
                        self.db,
                        "SELECT product_id, unit_price FROM stock_out_items WHERE doc_id=? ORDER BY id",
                        (doc_id,),
                    )
                }
                if (doc["status"] or "") == "posted":
                    if not self.is_role_admin:
                        self.db.rollback()
                        return self.redirect(
                            _redirect_with_toast(
                                "/stock/out",
                                title="Ошибка",
                                body="Редактирование проведённых документов доступно только руководителю",
                                variant="error",
                            )
                        )
                    if action != "update_posted":
                        self.db.rollback()
                        return self.redirect(
                            _redirect_with_toast(
                                f"/stock/out?doc_id={doc_id}",
                                title="Ошибка",
                                body="Проведённый документ можно только сохранить с изменениями",
                                variant="error",
                            )
                        )
                    is_posted_edit = True
                    before_snapshot = {
                        "note": doc["note"],
                        "client_id": doc["client_id"],
                        "items": _doc_line_items_snapshot(self.db, doc_id, "out"),
                    }
                    self.db.execute("DELETE FROM stock_moves WHERE stock_out_doc_id=?", (doc_id,))
                    if created_at_db:
                        self.db.execute(
                            "UPDATE stock_out_docs SET client_id=?, note=?, created_at=? WHERE id=?",
                            (client_id, note, created_at_db, doc_id),
                        )
                    else:
                        self.db.execute(
                            "UPDATE stock_out_docs SET client_id=?, note=? WHERE id=?",
                            (client_id, note, doc_id),
                        )
                else:
                    self.db.execute(
                        "UPDATE stock_out_docs SET client_id=?, note=? WHERE id=?",
                        (client_id, note, doc_id),
                    )
                self.db.execute("DELETE FROM stock_out_items WHERE doc_id=?", (doc_id,))
            else:
                cur = self.db.execute(
                    """
                    INSERT INTO stock_out_docs(doc_no, client_id, warehouse_id, status, note, created_by, created_at)
                    VALUES(?,?,?,'draft',?,?,COALESCE(?, datetime('now')))
                    """,
                    (_doc_no_next(self.db), client_id, warehouse_id, note, int(self.current_admin_id), created_at_db),
                )
                doc_id = int(cur.lastrowid)
                cur.close()

            for pid, qty, submitted_price in line_rows:
                if not self.is_role_viewer:
                    unit_price = float(submitted_price or 0)
                elif pid in old_prices:
                    unit_price = old_prices[pid]
                else:
                    product = fetchone(self.db, "SELECT sale_price FROM products WHERE id=?", (pid,))
                    unit_price = float(product["sale_price"] or 0) if product else 0.0
                self.db.execute(
                    "INSERT INTO stock_out_items(doc_id, product_id, qty, unit_price) VALUES(?,?,?,?)",
                    (doc_id, pid, qty, unit_price),
                )

            if action == "post" or is_posted_edit:
                qty_err = _stock_out_qty_error(self.db, line_items, warehouse_id)
                if qty_err:
                    self.db.rollback()
                    return self.redirect(
                        _redirect_with_toast(
                            f"/stock/out?doc_id={doc_id}",
                            title="Недостаточно на складе",
                            body=qty_err,
                            variant="error",
                        )
                    )
                _apply_stock_out_moves(
                    self.db,
                    doc_id,
                    line_items,
                    client_id,
                    int(self.current_admin_id),
                    note,
                    created_at_db,
                    warehouse_id,
                )
                if action == "post":
                    self.db.execute(
                        "UPDATE stock_out_docs SET status='posted', posted_at=datetime('now') WHERE id=?",
                        (doc_id,),
                    )
                else:
                    self.db.execute(
                        "UPDATE stock_out_docs SET posted_at=datetime('now') WHERE id=?",
                        (doc_id,),
                    )

            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            return self.redirect(
                _redirect_with_toast(
                    "/stock/out",
                    title="Ошибка",
                    body="Не удалось сохранить документ",
                    variant="error",
                )
            )

        if is_posted_edit:
            doc_event = "stock_out_post_edit"
            toast_title = "Изменения сохранены"
            redirect_url = f"/stock/out?doc_id={doc_id}"
            audit_after = {
                "client_id": client_id,
                "note": note,
                "items": len(line_items),
                "status": "posted",
            }
            audit_before = before_snapshot
        else:
            doc_event = "stock_out_post" if action == "post" else "stock_out_draft_save"
            toast_title = "Документ проведён" if action == "post" else "Черновик сохранён"
            redirect_url = "/stock/out/docs" if action == "post" else f"/stock/out?doc_id={doc_id}"
            audit_after = {
                "client_id": client_id,
                "note": note,
                "items": len(line_items),
                "status": ("posted" if action == "post" else "draft"),
            }
            audit_before = None

        self.audit(
            event=doc_event,
            entity="stock_out_doc",
            entity_id=doc_id,
            before=audit_before,
            after=audit_after,
            message=f"Документ отпуска #{doc_id}",
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["stock_moves", "dashboard", "stock_out"],
            by=ws_cid,
            event={"kind": doc_event, "doc_id": doc_id},
        )

        return self.redirect(
            _redirect_with_toast(
                redirect_url,
                title=toast_title,
                body=f"Позиции: {len(line_items)}",
            )
        )


class InventoryDocsHandler(AdminRequiredHandler):
    def get(self):
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 10_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")
        status_filter = self.get_argument("status", "").strip()
        q = self.get_argument("q", "").strip()

        sort_map = {
            "doc_no": "d.doc_no",
            "created": "d.created_at",
            "status": "d.status",
        }
        order_sql = _order_by(sort, dir_, sort_map, "created")

        where_parts: list[str] = []
        params: list = []
        if not self.is_role_admin:
            if self.current_warehouse_id is None:
                abort(403)
            where_parts.append("d.warehouse_id=?")
            params.append(self.current_warehouse_id)
        if status_filter in ("draft", "posted"):
            where_parts.append("d.status=?")
            params.append(status_filter)
        if q:
            where_parts.append("(d.doc_no LIKE ? OR COALESCE(d.note,'') LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total_row = fetchone(
            self.db,
            f"SELECT COUNT(*) AS c FROM inventory_docs d {where_sql}",
            tuple(params),
        )
        total = int(total_row["c"]) if total_row else 0
        pages = _pages(total, per_page)
        page = min(page, pages)
        offset = (page - 1) * per_page

        docs = fetchall(
            self.db,
            f"""
            SELECT
              d.id, d.doc_no, d.status, d.note,
              d.created_by, d.created_at, d.posted_at, d.warehouse_id,
              w.name AS warehouse_name,
              u.username AS created_by_name,
              (SELECT COUNT(*) FROM inventory_items i WHERE i.doc_id = d.id) AS item_count,
              (SELECT COUNT(*) FROM inventory_items i WHERE i.doc_id = d.id AND ABS(i.qty_actual - i.qty_system) > 0.000001) AS diff_count
            FROM inventory_docs d
            LEFT JOIN users u ON u.id = d.created_by
            LEFT JOIN warehouses w ON w.id = d.warehouse_id
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (per_page, offset),
        )

        return self.render(
            "inventory_docs.html",
            docs=[dict(d) for d in docs],
            page=page,
            pages=pages,
            per_page=per_page,
            total=total,
            sort=sort,
            dir=dir_,
            filter_status=status_filter,
            filter_q=q,
            title="Документы инвентаризации",
        )


class InventoryHandler(AdminRequiredHandler):
    def get(self):
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)
        categories = fetchall(self.db, "SELECT id, name FROM categories ORDER BY name", ())
        subcategories = fetchall(
            self.db,
            "SELECT id, category_id, name FROM subcategories ORDER BY name",
            (),
        )
        doc_id_raw = (self.get_argument("doc_id", "") or "").strip()
        doc = None
        saved_items: list[dict] = []
        if doc_id_raw.isdigit():
            doc = fetchone(
                self.db,
                "SELECT id, doc_no, status, note, created_at, warehouse_id FROM inventory_docs WHERE id=?",
                (int(doc_id_raw),),
            )
            if doc and not self.can_access_warehouse(doc["warehouse_id"]):
                abort(404)
            if doc:
                warehouse_id = int(doc["warehouse_id"])
                item_rows = fetchall(
                    self.db,
                    """
                    SELECT
                      i.product_id, i.qty_system, i.qty_actual,
                      p.name, p.sku, p.unit, p.image_path, p.is_active,
                      sc.id AS subcategory_id, sc.category_id,
                      c.name AS category_name, sc.name AS subcategory_name
                    FROM inventory_items i
                    JOIN products p ON p.id = i.product_id
                    JOIN subcategories sc ON sc.id = p.subcategory_id
                    JOIN categories c ON c.id = sc.category_id
                    WHERE i.doc_id=?
                    ORDER BY c.name, sc.name, p.name
                    """,
                    (int(doc_id_raw),),
                )
                saved_items = []
                for r in item_rows:
                    d = dict(r)
                    d["id"] = d["product_id"]
                    saved_items.append(d)

        is_posted = bool(doc and (doc["status"] or "") == "posted")
        can_edit_posted = bool(is_posted and self.is_role_admin)
        products = _inventory_page_products(
            self.db,
            None if can_edit_posted else (dict(doc) if doc else None),
            saved_items,
            warehouse_id,
        )

        return self.render(
            "inventory.html",
            products=products,
            categories=[dict(r) for r in categories],
            subcategories=[dict(r) for r in subcategories],
            doc=dict(doc) if doc else None,
            next_doc_no=_doc_no_next(self.db, "INV") if not doc else None,
            is_posted=is_posted,
            can_edit_posted=can_edit_posted,
            title="Инвентаризация",
        )

    def post(self):
        action = (self.get_body_argument("action", "save_draft") or "save_draft").strip()
        if self.is_role_viewer and action != "save_draft":
            abort(403)
        doc_id_raw = self.get_body_argument("doc_id", "").strip()
        note = self.get_body_argument("note", "").strip() or None
        created_at_raw = self.get_body_argument("created_at", "").strip()
        product_ids = request.form.getlist("product_id[]")
        qty_system_values = request.form.getlist("qty_system[]")
        qty_actual_values = request.form.getlist("qty_actual[]")
        warehouse_id = self.current_warehouse_id
        if warehouse_id is None:
            abort(403)

        line_items = _parse_inventory_lines(product_ids, qty_system_values, qty_actual_values)
        if not line_items:
            return self.redirect(
                _redirect_with_toast(
                    "/stock/inventory",
                    title="Ошибка",
                    body="Укажите фактическое количество хотя бы для одного товара",
                    variant="error",
                )
            )

        created_at_db = None
        if created_at_raw:
            try:
                created_at_db = local_wall_to_utc_db_str(created_at_raw)
            except Exception:
                created_at_db = None

        doc_id: int | None = None
        is_posted_edit = False
        try:
            self.db.execute("BEGIN")

            if doc_id_raw.isdigit():
                doc_id = int(doc_id_raw)
                doc = fetchone(
                    self.db,
                    "SELECT id, status, warehouse_id FROM inventory_docs WHERE id=?",
                    (doc_id,),
                )
                if not doc:
                    self.db.rollback()
                    abort(404)
                if not self.can_access_warehouse(doc["warehouse_id"]):
                    self.db.rollback()
                    abort(404)
                warehouse_id = int(doc["warehouse_id"])
                if (doc["status"] or "") == "posted":
                    if not self.is_role_admin or action != "update_posted":
                        self.db.rollback()
                        abort(403)
                    is_posted_edit = True
                if created_at_db:
                    self.db.execute(
                        "UPDATE inventory_docs SET note=?, created_at=? WHERE id=?",
                        (note, created_at_db, doc_id),
                    )
                else:
                    self.db.execute("UPDATE inventory_docs SET note=? WHERE id=?", (note, doc_id))
                self.db.execute("DELETE FROM inventory_items WHERE doc_id=?", (doc_id,))
            else:
                cur = self.db.execute(
                    """
                    INSERT INTO inventory_docs(doc_no, warehouse_id, status, note, created_by, created_at)
                    VALUES(?,?,'draft',?,?,COALESCE(?, datetime('now')))
                    """,
                    (_doc_no_next(self.db, "INV"), warehouse_id, note, int(self.current_admin_id), created_at_db),
                )
                doc_id = int(cur.lastrowid)
                cur.close()

            for it in line_items:
                self.db.execute(
                    """
                    INSERT INTO inventory_items(doc_id, product_id, qty_system, qty_actual)
                    VALUES(?,?,?,?)
                    """,
                    (doc_id, it["product_id"], it["qty_system"], it["qty_actual"]),
                )

            if action == "post":
                self.db.execute(
                    "UPDATE inventory_docs SET status='posted', posted_at=datetime('now') WHERE id=?",
                    (doc_id,),
                )
            elif is_posted_edit:
                self.db.execute(
                    "UPDATE inventory_docs SET posted_at=datetime('now') WHERE id=?",
                    (doc_id,),
                )

            self.db.commit()
        except ValueError as e:
            self.db.rollback()
            back = f"/stock/inventory?doc_id={doc_id}" if doc_id else "/stock/inventory"
            return self.redirect(
                _redirect_with_toast(back, title="Ошибка инвентаризации", body=str(e), variant="error")
            )
        except sqlite3.Error:
            self.db.rollback()
            return self.redirect(
                _redirect_with_toast(
                    "/stock/inventory",
                    title="Ошибка",
                    body="Не удалось сохранить документ",
                    variant="error",
                )
            )

        diff_count = sum(
            1 for it in line_items if abs(float(it["qty_actual"]) - float(it["qty_system"])) > 1e-6
        )
        if is_posted_edit:
            doc_event = "inventory_post_edit"
        else:
            doc_event = "inventory_post" if action == "post" else "inventory_draft_save"
        self.audit(
            event=doc_event,
            entity="inventory_doc",
            entity_id=doc_id,
            after={
                "note": note,
                "items": len(line_items),
                "diff_count": diff_count,
                "status": ("posted" if action in ("post", "update_posted") else "draft"),
            },
            message=f"Инвентаризация #{doc_id}",
        )

        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["stock_moves", "dashboard", "products", "inventory"],
            by=ws_cid,
            event={"kind": doc_event, "doc_id": doc_id},
        )

        return self.redirect(
            _redirect_with_toast(
                ("/stock/inventory/docs" if action == "post" else f"/stock/inventory?doc_id={doc_id}"),
                title=(
                    "Изменения сохранены"
                    if is_posted_edit
                    else ("Инвентаризация проведена" if action == "post" else "Черновик сохранён")
                ),
                body=(
                    f"Позиций: {len(line_items)} · Расхождений: {diff_count}"
                    if action in ("post", "update_posted")
                    else f"Позиций: {len(line_items)}"
                ),
            )
        )


class DocumentPdfHandler(AdminRequiredHandler):
    def get(self, doc_kind: str):
        from flask import Response

        doc_id_raw = (self.get_argument("doc_id", "") or "").strip()
        if not doc_id_raw.isdigit():
            abort(404)
        table_by_kind = {
            "stock_in": "stock_in_docs",
            "stock_out": "stock_out_docs",
            "inventory": "inventory_docs",
        }
        table = table_by_kind.get(doc_kind)
        if table:
            doc_access = fetchone(
                self.db,
                f"SELECT warehouse_id FROM {table} WHERE id=?",
                (int(doc_id_raw),),
            )
            if not doc_access or not self.can_access_warehouse(doc_access["warehouse_id"]):
                abort(404)
        try:
            from services.pdf_docs import build_document_pdf

            pdf_bytes, filename = build_document_pdf(
                self.db,
                doc_kind,
                int(doc_id_raw),
                include_purchase_prices=self.is_role_admin,
            )
        except LookupError:
            abort(404)
        except ValueError:
            abort(404)
        except RuntimeError as e:
            abort(500, description=str(e))
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                    if self.get_argument("download", "") == "1"
                    else f'inline; filename="{filename}"'
                )
            },
        )


class StockMovesHandler(AdminRequiredHandler):
    def get(self):
        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 10_000)
        per_page = _clamp_int(
            self.get_argument("per_page", str(LIST_DEFAULT_PER_PAGE)),
            LIST_DEFAULT_PER_PAGE,
            LIST_MIN_PER_PAGE,
            LIST_MAX_PER_PAGE,
        )
        sort = self.get_argument("sort", "created")
        dir_ = _sort_dir(self.get_argument("dir", "desc"), default="desc")

        q = self.get_argument("q", "").strip()
        product_id_raw = self.get_argument("product_id", "").strip()
        client_id_raw = self.get_argument("client_id", "").strip()
        move_type_raw = self.get_argument("move_type", "").strip()
        date_from_arg = self.get_argument("date_from", "").strip()
        date_to_arg = self.get_argument("date_to", "").strip()

        sort_map = {
            "id": "sm.id",
            "created": "sm.created_at",
            "product": "p.name",
            "qty": "sm.qty",
            "type": "sm.move_type",
        }
        order_by = _order_by(sort, dir_, sort_map, default_key="created")

        where_parts: list[str] = ["1=1"]
        params: list = []

        if not self.is_role_admin:
            if self.current_warehouse_id is None:
                abort(403)
            where_parts.append("sm.warehouse_id = ?")
            params.append(self.current_warehouse_id)

        if product_id_raw.isdigit():
            where_parts.append("sm.product_id = ?")
            params.append(int(product_id_raw))

        if client_id_raw.isdigit():
            where_parts.append("sm.client_id = ?")
            params.append(int(client_id_raw))

        if move_type_raw in ("in", "out"):
            where_parts.append("sm.move_type = ?")
            params.append(move_type_raw)

        if q:
            where_parts.append(
                "(p.name LIKE ? OR p.sku LIKE ? OR cl.full_name LIKE ? OR sm.note LIKE ?)"
            )
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q, like_q])

        db_from, date_from_norm = _dt_local_to_db(date_from_arg, is_end=False)
        db_to, date_to_norm = _dt_local_to_db(date_to_arg, is_end=True)

        if db_from:
            where_parts.append("substr(sm.created_at, 1, 19) >= ?")
            params.append(db_from)
        if db_to:
            where_parts.append("substr(sm.created_at, 1, 19) <= ?")
            params.append(db_to)

        where_sql = " AND ".join(where_parts)

        cnt = fetchone(
            self.db,
            f"""
            SELECT COUNT(*) AS c
            FROM stock_moves sm
            JOIN products p ON p.id = sm.product_id
            LEFT JOIN clients cl ON cl.id = sm.client_id
            WHERE {where_sql}
            """,
            tuple(params),
        )
        total = int(cnt["c"]) if cnt else 0
        pages = _pages(total, per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page

        moves = fetchall(
            self.db,
            f"""
            SELECT
              sm.id,
              sm.product_id,
              sm.client_id,
              sm.admin_id,
              sm.move_type,
              sm.qty,
              sm.note,
              sm.created_at,
              p.name AS product_name,
              p.unit AS product_unit,
              p.sku AS product_sku,
              cl.full_name AS client_name,
              u.username AS admin_username,
              w.name AS warehouse_name
            FROM stock_moves sm
            JOIN products p ON p.id = sm.product_id
            LEFT JOIN clients cl ON cl.id = sm.client_id
            LEFT JOIN users u ON u.id = sm.admin_id
            LEFT JOIN warehouses w ON w.id = sm.warehouse_id
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (per_page, offset),
        )

        products = fetchall(
            self.db, "SELECT id, name FROM products ORDER BY name", ()
        )
        clients = fetchall(
            self.db, "SELECT id, full_name FROM clients ORDER BY full_name", ()
        )

        return self.render(
            "stock_moves.html",
            moves=[dict(r) for r in moves],
            products_list=[dict(r) for r in products],
            clients_list=[dict(r) for r in clients],
            q=q,
            filter_product_id=product_id_raw,
            filter_client_id=client_id_raw,
            filter_move_type=move_type_raw,
            date_from=date_from_norm or date_from_arg,
            date_to=date_to_norm or date_to_arg,
            page=page,
            pages=pages,
            per_page=per_page,
            sort=sort,
            dir=dir_,
            title="Журнал движений",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Admin — Users
# ═══════════════════════════════════════════════════════════════════════════


class UsersHandler(AdminRequiredHandler):
    def get(self):
        if not (self.is_role_admin or self.is_role_manager):
            abort(403)

        if self.is_role_admin:
            users = fetchall(
                self.db,
                """
                SELECT u.id, u.username, u.is_active, u.role, u.created_at,
                       u.warehouse_id, w.name AS warehouse_name
                FROM users u
                LEFT JOIN warehouses w ON w.id=u.warehouse_id
                ORDER BY u.id
                """,
            )
        else:
            if self.current_warehouse_id is None:
                abort(403)
            users = fetchall(
                self.db,
                """
                SELECT u.id, u.username, u.is_active, u.role, u.created_at,
                       u.warehouse_id, w.name AS warehouse_name
                FROM users u
                LEFT JOIN warehouses w ON w.id=u.warehouse_id
                WHERE u.role='viewer' AND u.warehouse_id=?
                ORDER BY u.id
                """,
                (self.current_warehouse_id,),
            )
        warehouses = fetchall(
            self.db, "SELECT id, name, is_active FROM warehouses ORDER BY name", ()
        )
        return self.render(
            "users.html",
            users=[dict(u) for u in users],
            warehouses=[dict(w) for w in warehouses],
            error=None,
            title="Пользователи",
        )

    def post(self):
        if not (self.is_role_admin or self.is_role_manager):
            abort(403)

        action = (self.get_body_argument("action", "create_user") or "create_user").strip()
        if action == "create_warehouse":
            if not self.is_role_admin:
                abort(403)
            warehouse_name = self.get_body_argument("warehouse_name", "").strip()
            if not warehouse_name:
                return self.redirect(
                    _redirect_with_toast(
                        "/admins", title="Ошибка", body="Укажите название склада", variant="error"
                    )
                )
            try:
                self.db.execute(
                    "INSERT INTO warehouses(name, is_active) VALUES(?, 1)",
                    (warehouse_name,),
                )
                self.db.commit()
            except sqlite3.IntegrityError:
                self.db.rollback()
                return self.redirect(
                    _redirect_with_toast(
                        "/admins", title="Ошибка", body="Склад с таким названием уже существует", variant="error"
                    )
                )
            return self.redirect(
                _redirect_with_toast("/admins", title="Склад добавлен", body=warehouse_name)
            )

        username = self.get_body_argument("username").strip()
        password = self.get_body_argument("password").strip()
        role = (self.get_body_argument("role", "") or "").strip()
        warehouse_id_raw = (self.get_body_argument("warehouse_id", "") or "").strip()
        if self.is_role_manager:
            role = "viewer"
            warehouse_id = self.current_warehouse_id
        else:
            if role not in ("admin", "manager", "viewer"):
                return self.redirect(
                    _redirect_with_toast(
                        "/admins", title="Ошибка", body="Выберите роль", variant="error"
                    )
                )
            warehouse_id = int(warehouse_id_raw) if warehouse_id_raw.isdigit() else None

        warehouse = fetchone(
            self.db,
            "SELECT id FROM warehouses WHERE id=? AND is_active=1",
            (warehouse_id,),
        ) if warehouse_id is not None else None
        if not warehouse:
            return self.redirect(
                _redirect_with_toast(
                    "/admins", title="Ошибка", body="Выберите склад", variant="error"
                )
            )

        if not username or not password:
            return self.redirect(
                _redirect_with_toast(
                    "/admins",
                    title="Не удалось добавить пользователя",
                    body="Логин и пароль обязательны",
                    variant="error",
                )
            )

        err = _password_error(password)
        if err:
            return self.redirect(
                _redirect_with_toast(
                    "/admins",
                    title="Требования к паролю",
                    body=err,
                    variant="error",
                )
            )

        try:
            cur = self.db.execute(
                "INSERT INTO users(username, password_hash, is_active, role, warehouse_id) VALUES(?,?,1,?,?)",
                (username, hash_password(password), role or "viewer", warehouse_id),
            )
            new_id = int(cur.lastrowid) if cur.lastrowid else None
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            return self.redirect(
                _redirect_with_toast(
                    "/admins",
                    title="Не удалось добавить пользователя",
                    body="Пользователь с таким логином уже существует",
                    variant="error",
                )
            )

        self.audit(
            event="create_user",
            entity="user",
            entity_id=new_id,
            after={
                "username": username,
                "is_active": 1,
                "role": role,
                "warehouse_id": warehouse_id,
            },
            message=username,
        )
        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["admins"],
            by=ws_cid,
            event={"kind": "add_user", "username": username},
        )
        return self.redirect(
            _redirect_with_toast(
                "/admins", title="Пользователь добавлен", body=username
            )
        )


class UserEditHandler(AdminRequiredHandler):
    def get(self, admin_id: str):
        if not self.is_role_admin:
            abort(403)

        admin = fetchone(
            self.db,
            "SELECT id, username, is_active, role, warehouse_id FROM users WHERE id=?",
            (int(admin_id),),
        )
        if not admin:
            abort(404)
        is_root = int(admin_id) == 1
        is_self_admin = (
            not is_root
            and self.current_admin_id == int(admin_id)
            and (admin["role"] or "admin") == "admin"
        )
        return self.render(
            "user_edit.html",
            admin=dict(admin),
            warehouses=[dict(w) for w in fetchall(
                self.db, "SELECT id, name FROM warehouses WHERE is_active=1 ORDER BY name", ()
            )],
            is_root=is_root,
            is_self_admin=is_self_admin,
            error=None,
            title="Редактирование пользователя",
        )

    def post(self, admin_id: str):
        if not self.is_role_admin:
            abort(403)

        username = self.get_body_argument("username").strip()
        password = self.get_body_argument("password", "").strip()
        is_active = 1 if self.get_body_argument("is_active", None) is not None else 0
        role = (self.get_body_argument("role", "") or "").strip()
        if role not in ("admin", "manager", "viewer"):
            role = "viewer"
        warehouse_id_raw = (self.get_body_argument("warehouse_id", "") or "").strip()
        warehouse_id = int(warehouse_id_raw) if warehouse_id_raw.isdigit() else None

        old = fetchone(
            self.db,
            "SELECT username, is_active, role, warehouse_id FROM users WHERE id=?",
            (int(admin_id),),
        )
        if not old:
            abort(404)

        is_self = self.current_admin_id == int(admin_id)

        if int(admin_id) == 1:
            if not is_self:
                username = old["username"]
                is_active = int(old["is_active"])
                role = old["role"]
                warehouse_id = old["warehouse_id"]
                password = ""
            else:
                is_active = int(old["is_active"])
                role = old["role"]

        if (
            old
            and is_self
            and int(admin_id) != 1
            and (old["role"] or "admin") == "admin"
        ):
            role = old["role"]
            is_active = int(old["is_active"])

        if (
            old
            and (old["username"] or "").strip() == username
            and int(old["is_active"]) == is_active
            and (old["role"] or "admin") == (role or old["role"] or "admin")
            and old["warehouse_id"] == warehouse_id
            and not password
        ):
            return self.redirect(
                _redirect_target_after_modal_save(self, "/admins")
            )

        try:
            warehouse = fetchone(
                self.db,
                "SELECT id FROM warehouses WHERE id=? AND is_active=1",
                (warehouse_id,),
            ) if warehouse_id is not None else None
            if not warehouse:
                raise sqlite3.IntegrityError("warehouse_required")
            self.db.execute(
                "UPDATE users SET username=?, is_active=?, role=?, warehouse_id=? WHERE id=?",
                (
                    username,
                    is_active,
                    role or (old["role"] if old else "admin"),
                    warehouse_id,
                    int(admin_id),
                ),
            )
        except sqlite3.IntegrityError:
            self.db.rollback()
            from_modal = (
                self.get_argument("from_modal", "") or ""
            ).strip() == "1"
            if from_modal:
                admin_row = fetchone(
                    self.db,
                    "SELECT id, username, is_active, role, warehouse_id FROM users WHERE id=?",
                    (int(admin_id),),
                )
                if not admin_row:
                    abort(404)
                is_root = int(admin_id) == 1
                is_self_admin = (
                    not is_root
                    and self.current_admin_id == int(admin_id)
                    and (admin_row["role"] or "admin") == "admin"
                )
                return self.render(
                    "user_edit.html",
                    admin=dict(admin_row),
                    warehouses=[dict(w) for w in fetchall(
                        self.db, "SELECT id, name FROM warehouses WHERE is_active=1 ORDER BY name", ()
                    )],
                    is_root=is_root,
                    is_self_admin=is_self_admin,
                    error="Пользователь с таким логином уже существует",
                    title="Редактирование пользователя",
                )
            return self.redirect(
                _redirect_with_toast(
                    "/admins",
                    title="Не удалось сохранить",
                    body="Пользователь с таким логином уже существует",
                    variant="error",
                )
            )

        password_changed = False
        if password:
            err = _password_error(password)
            if err:
                self.db.rollback()
                from_modal = (
                    self.get_argument("from_modal", "") or ""
                ).strip() == "1"
                if from_modal:
                    admin_dict = {
                        "id": int(admin_id),
                        "username": username,
                        "is_active": is_active,
                        "role": role or (old["role"] if old else "admin"),
                        "warehouse_id": warehouse_id,
                    }
                    is_root = int(admin_id) == 1
                    is_self_admin = (
                        not is_root
                        and self.current_admin_id == int(admin_id)
                        and (admin_dict["role"] or "admin") == "admin"
                    )
                    return self.render(
                        "user_edit.html",
                        admin=admin_dict,
                        warehouses=[dict(w) for w in fetchall(
                            self.db, "SELECT id, name FROM warehouses WHERE is_active=1 ORDER BY name", ()
                        )],
                        is_root=is_root,
                        is_self_admin=is_self_admin,
                        error=err,
                        title="Редактирование пользователя",
                    )
                return self.redirect(
                    _redirect_with_toast(
                        "/admins",
                        title="Требования к паролю",
                        body=err,
                        variant="error",
                    )
                )

            self.db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(password), int(admin_id)),
            )
            password_changed = True

        self.db.commit()

        if self.current_admin_id == int(admin_id):
            self.set_secure_cookie(
                "role", role or (old["role"] if old else "admin")
            )
            self.set_secure_cookie("username", username)

        self.audit(
            event="edit_user",
            entity="user",
            entity_id=int(admin_id),
            before={
                "username": (old["username"] or "").strip(),
                "is_active": int(old["is_active"]),
                "role": old["role"],
                "warehouse_id": old["warehouse_id"],
            },
            after={
                "username": username,
                "is_active": is_active,
                "role": role or (old["role"] if old else None),
                "warehouse_id": warehouse_id,
                "password_changed": bool(password),
            },
            message=username,
        )
        ws_cid = self.get_body_argument("ws_client_id", default=None)
        rt_broadcast_many(
            self.db,
            ["admins"],
            by=ws_cid,
            event={
                "kind": "edit_user",
                "id": int(admin_id),
                "username": username,
            },
        )
        toast_body = username
        if password_changed:
            toast_body = f"{username} (пароль изменён)"
        return self.redirect(
            _redirect_with_toast(
                _redirect_target_after_modal_save(self, "/admins"),
                title="Пользователь обновлён",
                body=toast_body,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Admin — Profile
# ═══════════════════════════════════════════════════════════════════════════


class ProfileHandler(AdminRequiredHandler):
    def get(self):
        admin = fetchone(
            self.db,
            "SELECT id, username, role FROM users WHERE id=?",
            (self.current_admin_id,),
        )
        return self.render(
            "profile.html", admin=dict(admin), error=None, title="Профиль"
        )

    def post(self):
        username = self.get_body_argument("username").strip()
        password = self.get_body_argument("password", "").strip()

        old = fetchone(
            self.db,
            "SELECT username, password_hash FROM users WHERE id=?",
            (self.current_admin_id,),
        )
        if not old:
            abort(404)
        old_username = (old["username"] or "").strip()

        if not username:
            return self.redirect(
                _redirect_with_toast(
                    "/profile",
                    title="Профиль не сохранён",
                    body="Логин не может быть пустым",
                    variant="error",
                )
            )

        login_changed = old_username != username

        password_really_changes = False
        if password:
            err = _password_error(password)
            if err:
                return self.redirect(
                    _redirect_with_toast(
                        "/profile",
                        title="Требования к паролю",
                        body=err,
                        variant="error",
                    )
                )
            if not verify_password(password, old["password_hash"]):
                password_really_changes = True

        if not login_changed and not password_really_changes:
            return self.redirect("/profile")

        try:
            if login_changed:
                self.db.execute(
                    "UPDATE users SET username=? WHERE id=?",
                    (username, self.current_admin_id),
                )
            if password_really_changes:
                self.db.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (hash_password(password), self.current_admin_id),
                )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            return self.redirect(
                _redirect_with_toast(
                    "/profile",
                    title="Профиль не сохранён",
                    body="Пользователь с таким логином уже существует",
                    variant="error",
                )
            )

        if login_changed:
            self.set_secure_cookie("username", username)

        if login_changed and password_really_changes:
            toast_body = "Профиль обновлён (логин и пароль)"
        elif password_really_changes:
            toast_body = "Профиль обновлён (пароль изменён)"
        else:
            toast_body = "Профиль обновлён"

        return self.redirect(
            _redirect_with_toast("/profile", title="Готово", body=toast_body)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Admin — Logs
# ═══════════════════════════════════════════════════════════════════════════


class LogsHandler(AdminRequiredHandler):
    def get(self):
        if not self.is_role_admin:
            abort(403)

        page = _clamp_int(self.get_argument("page", "1"), 1, 1, 100_000)
        limit = 200
        offset = (page - 1) * limit

        q = self.get_argument("q", "").strip()
        level = self.get_argument("level", "").strip()
        admin_id_raw = self.get_argument("admin_id", "").strip()
        admin_id = admin_id_raw if admin_id_raw.isdigit() else ""
        event = self.get_argument("event", "").strip()
        entity = self.get_argument("entity", "").strip()
        date_from = self.get_argument("date_from", "").strip()
        date_to = self.get_argument("date_to", "").strip()

        def _local_str_to_utc_sql(s: str) -> str:
            ss = (s or "").replace("T", " ").split(".")[0].strip()
            if len(ss) == 16:
                ss = ss + ":00"
            dt = datetime.strptime(ss[:19], "%Y-%m-%d %H:%M:%S")
            dt_utc = dt - timedelta(hours=TZ_OFFSET_HOURS)
            return dt_utc.strftime("%Y-%m-%d %H:%M:%S")

        where_parts: list[str] = []
        params: list = []

        if level:
            where_parts.append("l.level = ?")
            params.append(level)
        if admin_id:
            where_parts.append("l.actor_admin_id = ?")
            params.append(admin_id)
        if event:
            where_parts.append("l.event = ?")
            params.append(event)
        if entity:
            where_parts.append("l.entity = ?")
            params.append(entity)
        if date_from:
            where_parts.append("substr(l.created_at, 1, 19) >= ?")
            params.append(_local_str_to_utc_sql(date_from))
        if date_to:
            where_parts.append("substr(l.created_at, 1, 19) <= ?")
            params.append(_local_str_to_utc_sql(date_to))

        if q:
            q_strip = q.strip()
            q_cf = q_strip.casefold()
            q_norm = q_cf.replace("ё", "е")

            event_ru_to_key = {
                "вход": "login",
                "логин": "login",
                "выход": "logout",
                "сессия завершена": "session_expired",
                "ошибка запроса": "request_error",
                "создан клиент": "create_client",
                "изменен клиент": "edit_client",
                "создана категория": "create_category",
                "изменена категория": "edit_category",
                "создана подкатегория": "create_subcategory",
                "изменена подкатегория": "edit_subcategory",
                "создан товар": "create_product",
                "изменен товар": "edit_product",
                "приход": "stock_in",
                "отпуск": "stock_out",
                "инвентаризация": "inventory",
                "создан пользователь": "create_user",
                "изменен пользователь": "edit_user",
            }
            entity_ru_to_key = {
                "авторизация": "auth",
                "http": "http",
                "клиент": "client",
                "категория": "category",
                "подкатегория": "subcategory",
                "товар": "product",
                "пользователь": "user",
            }

            def _norm_ru(s: str | None) -> str:
                return (s or "").casefold().replace("ё", "е")

            if re.fullmatch(r"\d+", q_strip):
                where_parts.append("CAST(l.entity_id AS TEXT) LIKE ?")
                params.append(f"%{q_strip}%")
            else:
                m_id_only = re.match(
                    r"^\s*id\s*:\s*(\d+)\s*$", q_strip, flags=re.IGNORECASE
                )
                if m_id_only:
                    where_parts.append("l.entity_id = ?")
                    params.append(int(m_id_only.group(1)))
                else:
                    m_hash = re.match(
                        r"^\s*([^#]+?)\s*#\s*(\d+)\s*$", q_strip
                    )
                    m_id_label = re.match(
                        r"^\s*([^:]+?)\s*id\s*:\s*(\d+)\s*$",
                        q_strip,
                        flags=re.IGNORECASE,
                    )

                    if m_id_label:
                        label = m_id_label.group(1).strip()
                        eid = int(m_id_label.group(2))
                        ent_key = entity_ru_to_key.get(_norm_ru(label))
                        if ent_key:
                            where_parts.append(
                                "(l.entity = ? AND l.entity_id = ?)"
                            )
                            params.append(ent_key)
                            params.append(eid)
                        else:
                            where_parts.append("l.entity_id = ?")
                            params.append(eid)
                    elif m_hash:
                        label = m_hash.group(1).strip()
                        eid = int(m_hash.group(2))
                        ent_key = entity_ru_to_key.get(_norm_ru(label))
                        if ent_key:
                            where_parts.append(
                                "(l.entity = ? AND l.entity_id = ?)"
                            )
                            params.append(ent_key)
                            params.append(eid)
                        else:
                            where_parts.append(
                                "(lower(l.event) LIKE lower(?) OR lower(l.entity) LIKE lower(?))"
                            )
                            params.append(f"%{q_strip}%")
                            params.append(f"%{q_strip}%")
                    else:
                        event_norm_map = {
                            _norm_ru(k): v
                            for k, v in event_ru_to_key.items()
                        }
                        entity_norm_map = {
                            _norm_ru(k): v
                            for k, v in entity_ru_to_key.items()
                        }
                        if q_norm in event_norm_map:
                            where_parts.append("l.event = ?")
                            params.append(event_norm_map[q_norm])
                        elif q_norm in entity_norm_map:
                            where_parts.append("l.entity = ?")
                            params.append(entity_norm_map[q_norm])
                        else:
                            matched_event = None
                            for ru_val, key in event_ru_to_key.items():
                                rv_norm = _norm_ru(ru_val)
                                if q_norm in rv_norm or rv_norm in q_norm:
                                    matched_event = key
                                    break
                            if matched_event:
                                where_parts.append("l.event = ?")
                                params.append(matched_event)
                            else:
                                matched_entity = None
                                for ru_val, key in entity_ru_to_key.items():
                                    rv_norm = _norm_ru(ru_val)
                                    if (
                                        q_norm in rv_norm
                                        or rv_norm in q_norm
                                    ):
                                        matched_entity = key
                                        break
                                if matched_entity:
                                    where_parts.append("l.entity = ?")
                                    params.append(matched_entity)
                                else:
                                    where_parts.append(
                                        "(lower(l.event) LIKE lower(?) OR lower(l.entity) LIKE lower(?))"
                                    )
                                    params.append(f"%{q_strip}%")
                                    params.append(f"%{q_strip}%")

        where_sql = (
            " AND ".join(where_parts) if where_parts else "1=1"
        )

        logs = fetchall(
            self.db,
            f"""
            SELECT
              l.id,
              l.created_at,
              l.level,
              l.event,
              l.actor_admin_id,
              l.actor_username,
              l.ip,
              l.entity,
              l.entity_id,
              l.before_json,
              l.after_json,
              l.diff_json,
              l.money_delta,
              l.message
            FROM logs l
            WHERE {where_sql}
            ORDER BY l.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        )

        events_rows = fetchall(
            self.db,
            "SELECT DISTINCT event FROM logs WHERE event IS NOT NULL AND event != '' ORDER BY event LIMIT 500",
            (),
        )
        entities_rows = fetchall(
            self.db,
            "SELECT DISTINCT entity FROM logs WHERE entity IS NOT NULL AND entity != '' ORDER BY entity LIMIT 200",
            (),
        )
        admins_rows = fetchall(
            self.db,
            """
            SELECT u.id AS id, u.username AS username
            FROM users u
            INNER JOIN (
              SELECT DISTINCT actor_admin_id AS aid FROM logs WHERE actor_admin_id IS NOT NULL
            ) x ON x.aid = u.id
            ORDER BY u.username COLLATE NOCASE
            LIMIT 200
            """,
            (),
        )
        orphan_admins = fetchall(
            self.db,
            """
            SELECT DISTINCT l.actor_admin_id AS id, ('#' || l.actor_admin_id) AS username
            FROM logs l
            WHERE l.actor_admin_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = l.actor_admin_id)
            ORDER BY l.actor_admin_id
            LIMIT 50
            """,
            (),
        )

        events_list = [r["event"] for r in events_rows]
        entities_list = [r["entity"] for r in entities_rows]
        admins_list = [dict(r) for r in admins_rows] + [
            dict(r) for r in orphan_admins
        ]

        cnt = fetchone(
            self.db,
            f"SELECT COUNT(*) AS c FROM logs l WHERE {where_sql}",
            tuple(params),
        )
        total = int(cnt["c"])
        pages = max(1, (total + limit - 1) // limit)
        if page > pages:
            page = pages

        query_parts: list[tuple[str, str]] = []
        if q:
            query_parts.append(("q", q))
        if level:
            query_parts.append(("level", level))
        if admin_id:
            query_parts.append(("admin_id", admin_id))
        if event:
            query_parts.append(("event", event))
        if entity:
            query_parts.append(("entity", entity))
        if date_from:
            query_parts.append(("date_from", date_from))
        if date_to:
            query_parts.append(("date_to", date_to))
        logs_query_suffix = (
            ("&" + urllib.parse.urlencode(query_parts))
            if query_parts
            else ""
        )

        def _safe_json_loads(s):
            if not s:
                return None
            try:
                return json.loads(s)
            except Exception:
                return None

        ROLE_RU = {
            "admin": "Руководитель",
            "manager": "Менеджер",
            "viewer": "Продавец",
        }

        EVENT_RU = {
            "login": "Вход",
            "login_failed": "Вход (ошибка)",
            "login_blocked": "Вход (заблокирован)",
            "logout": "Выход",
            "session_expired": "Сессия завершена",
            "request_error": "Ошибка запроса",
            "create_client": "Создан клиент",
            "edit_client": "Изменен клиент",
            "create_category": "Создана категория",
            "edit_category": "Изменена категория",
            "create_subcategory": "Создана подкатегория",
            "edit_subcategory": "Изменена подкатегория",
            "create_product": "Создан товар",
            "edit_product": "Изменен товар",
            "stock_in": "Приход",
            "stock_out": "Отпуск",
            "inventory_post": "Инвентаризация",
            "inventory_draft_save": "Инвентаризация (черновик)",
            "create_user": "Создан пользователь",
            "edit_user": "Изменен пользователь",
        }

        ENTITY_RU = {
            "auth": "Авторизация",
            "http": "HTTP",
            "client": "Клиент",
            "category": "Категория",
            "subcategory": "Подкатегория",
            "product": "Товар",
            "user": "Пользователь",
            "inventory_doc": "Инвентаризация",
        }

        FIELD_RU = {
            "username": "Логин",
            "is_active": "Активен",
            "role": "Роль",
            "password_changed": "Пароль изменён",
            "full_name": "Имя",
            "phone": "Телефон",
            "name": "Название",
            "sku": "Артикул",
            "unit": "Ед. изм.",
            "min_stock": "Мин. остаток",
            "subcategory_id": "Подкатегория",
            "category_id": "Категория",
            "qty": "Количество",
            "note": "Примечание",
            "product_name": "Товар",
            "client_name": "Клиент",
            "client_id": "ID клиента",
            "balance_after": "Остаток после",
            "move_type": "Тип",
        }

        log_rows = []
        for row in logs:
            d = dict(row)
            d["before_parsed"] = _safe_json_loads(d.get("before_json"))
            d["after_parsed"] = _safe_json_loads(d.get("after_json"))
            d["diff_parsed"] = _safe_json_loads(d.get("diff_json"))
            d["event_ru"] = EVENT_RU.get(d.get("event") or "", d.get("event") or "")
            d["entity_ru"] = ENTITY_RU.get(d.get("entity") or "", d.get("entity") or "")
            log_rows.append(d)

        return self.render(
            "logs.html",
            logs=log_rows,
            page=page,
            pages=pages,
            q=q,
            level=level,
            admin_id=admin_id,
            event=event,
            entity=entity,
            date_from=date_from,
            date_to=date_to,
            events_list=events_list,
            entities_list=entities_list,
            admins_list=admins_list,
            logs_query_suffix=logs_query_suffix,
            EVENT_RU=EVENT_RU,
            ENTITY_RU=ENTITY_RU,
            ROLE_RU=ROLE_RU,
            FIELD_RU=FIELD_RU,
            title="Журнал действий",
        )
