import json


def rt_broadcast(conn, context: str, *, by: str | None = None, event: dict | None = None):
    conn.execute(
        "INSERT INTO realtime_outbox (context, exclude_client, event_json) VALUES (?,?,?)",
        (context, (by or ""), json.dumps(event or {}, ensure_ascii=False)),
    )


def rt_broadcast_many(conn, contexts: list[str], *, by: str | None = None, event: dict | None = None):
    for ctx in contexts:
        rt_broadcast(conn, ctx, by=by, event=event or {})
    conn.commit()


def rt_poll(conn, since_id: int, context: str, client_id: str) -> tuple[bool, int]:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM realtime_outbox").fetchone()
    last_id = int(row["m"] if row else 0)
    if since_id < 0:
        return (False, last_id)
    hit = conn.execute(
        """
        SELECT 1 FROM realtime_outbox
        WHERE id > ? AND context = ? AND (exclude_client = '' OR exclude_client != ?)
        LIMIT 1
        """,
        (int(since_id), context, client_id or ""),
    ).fetchone()
    return (hit is not None, last_id)
