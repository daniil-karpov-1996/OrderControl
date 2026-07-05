import json
import sqlite3
from typing import Any

from settings import LOGS_MAX_ROWS


def _diff_before_after(before: dict | None, after: dict | None) -> list[dict] | None:
    if before is None and after is None:
        return None
    if before is None or after is None:
        return None

    def _norm(v: Any):
        if v is None:
            return ""
        return v

    diff = []
    all_keys = set(before) | set(after)
    for k in all_keys:
        v_b = before.get(k)
        v_a = after.get(k)
        if _norm(v_b) != _norm(v_a):
            diff.append({"field": k, "from": v_b, "to": v_a})
    return diff if diff else None


def audit_log(
    db,
    *,
    level: str = "AUDIT",
    event: str,
    actor_admin_id: int | None = None,
    actor_username: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    entity: str | None = None,
    entity_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    message: str | None = None,
    money_delta: float | None = None,
):
    before_json = json.dumps(before, ensure_ascii=False) if before is not None else None
    after_json = json.dumps(after, ensure_ascii=False) if after is not None else None
    diff = _diff_before_after(before, after)
    diff_json = json.dumps(diff, ensure_ascii=False) if diff else None

    db.execute(
        """
        INSERT INTO logs(
            level, event, actor_admin_id, actor_username, ip, user_agent, request_id,
            entity, entity_id, before_json, after_json, diff_json, money_delta, message
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            level,
            event,
            actor_admin_id,
            actor_username or None,
            ip,
            user_agent,
            request_id,
            entity,
            entity_id,
            before_json,
            after_json,
            diff_json,
            money_delta,
            message,
        ),
    )
    _rotate_logs(db)
    db.commit()


def _rotate_logs(db):
    if LOGS_MAX_ROWS < 1:
        return
    cur = db.execute(
        "SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET ?",
        (LOGS_MAX_ROWS,),
    )
    row = cur.fetchone()
    cur.close()
    if row is None:
        return
    db.execute("DELETE FROM logs WHERE id <= ?", (row[0],))
