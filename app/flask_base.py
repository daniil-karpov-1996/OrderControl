from __future__ import annotations

import time
import random
from datetime import date, datetime, timedelta

from flask import abort, g, redirect, render_template, request, session, url_for

from db import fetchone
from services.log import audit_log
from settings import APP_VERSION, TZ_OFFSET_HOURS, APP_CURRENCY

TZ_OFFSET = timedelta(hours=TZ_OFFSET_HOURS)


def _to_local(dt: datetime) -> datetime:
    return dt + TZ_OFFSET


def _to_utc(dt: datetime) -> datetime:
    return dt - TZ_OFFSET


def parse_naive_datetime_flexible(s: str) -> datetime:
    s = str(s).strip().replace("T", " ")
    s = s.split(".")[0].strip()
    if len(s) == 10:
        s = f"{s} 00:00:00"
    elif len(s) == 16:
        s = f"{s}:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    raise ValueError(s)


def local_wall_to_utc_db_str(s: str) -> str:
    return _to_utc(parse_naive_datetime_flexible(s)).strftime("%Y-%m-%d %H:%M:%S")


def utc_db_str_to_local_input(s: str | None) -> str:
    if not s:
        return ""
    dt_u = parse_naive_datetime_flexible(s)
    return _to_local(dt_u).strftime("%Y-%m-%dT%H:%M:%S")[:19]


def fmt_dt(v) -> str:
    if not v:
        return "-"
    if isinstance(v, datetime):
        return _to_local(v).strftime("%d.%m.%Y %H:%M:%S")
    s = str(v).strip()
    if len(s) == 10:
        s = s + " 00:00:00"
    s = s.replace("T", " ")
    s = s.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return _to_local(dt).strftime("%d.%m.%Y %H:%M:%S")
        except ValueError:
            pass
    return str(v)


def fmt_money(v, *, digits: int = 0, suffix: str | None = None) -> str:
    if suffix is None:
        suffix = APP_CURRENCY
    if v is None:
        return "-"
    try:
        x = float(v)
    except Exception:
        return str(v)
    sign = "-" if x < 0 else ""
    x = abs(x)
    if digits <= 0:
        s = f"{x:,.0f}"
    else:
        s = f"{x:,.{digits}f}"
    s = s.replace(",", " ")
    if suffix:
        return f"{sign}{s} {suffix}"
    return f"{sign}{s}"


def fmt_qty(v) -> str:
    if v is None:
        return "-"
    try:
        x = float(v)
    except Exception:
        return str(v)
    if x == int(x):
        return f"{int(x):,}".replace(",", " ")
    return f"{x:,.2f}".replace(",", " ")


def _short_request_id():
    return f"{int(time.time() * 1000):x}-{random.getrandbits(24):x}"


class _ArgView:
    def __contains__(self, key):
        return key in request.args

    def items(self):
        for k in request.args:
            yield k, request.args.getlist(k)


class _ReqShim:
    @property
    def headers(self):
        return request.headers

    @property
    def remote_ip(self):
        x = (request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For") or "").strip()
        if x and "," in x:
            x = x.split(",")[0].strip()
        return x or (request.remote_addr or "")

    @property
    def arguments(self):
        return _ArgView()

    @property
    def method(self):
        return request.method

    @property
    def path(self):
        return request.path

    @property
    def uri(self):
        fp = request.full_path or request.path
        if isinstance(fp, bytes):
            fp = fp.decode("utf-8", "replace")
        return (fp[:400] if fp else "")[:400]


class FlaskBaseHandler:
    _req_shim = _ReqShim()

    @property
    def request_id(self):
        if not hasattr(g, "request_id"):
            g.request_id = _short_request_id()
        return g.request_id

    @property
    def request(self):
        return self._req_shim

    @property
    def db(self):
        return g.db

    def get_current_user(self):
        return session.get("admin_id")

    @property
    def current_admin_id(self):
        return session.get("admin_id")

    @property
    def current_username(self) -> str | None:
        return session.get("username")

    @property
    def current_role(self) -> str | None:
        v = session.get("role")
        if v:
            return str(v)
        if self.current_admin_id == 1:
            return "admin"
        return None

    @property
    def is_superadmin(self) -> bool:
        return self.current_admin_id == 1

    @property
    def is_role_admin(self) -> bool:
        return (self.current_role or "") == "admin"

    @property
    def is_role_manager(self) -> bool:
        return (self.current_role or "") == "manager"

    @property
    def is_role_viewer(self) -> bool:
        return (self.current_role or "") == "viewer"

    def get_argument(self, name, default=""):
        v = request.values.get(name)
        if v is None:
            return default
        return v

    def get_body_argument(self, name, default=""):
        v = request.form.get(name)
        if v is None:
            return default
        return v

    def render(self, template_name, **kwargs):
        kwargs.setdefault("title", "OrderControl")
        kwargs.setdefault("app_version", APP_VERSION)
        kwargs.setdefault("current_admin_id", self.current_admin_id)
        kwargs.setdefault("is_superadmin", self.is_superadmin)
        kwargs.setdefault("current_username", self.current_username)
        kwargs.setdefault("current_role", self.current_role)
        kwargs.setdefault("is_admin_role", self.is_role_admin)
        kwargs.setdefault("is_manager_role", self.is_role_manager)
        kwargs.setdefault("is_viewer_role", self.is_role_viewer)
        kwargs.setdefault("now_local", _to_local(datetime.utcnow()).strftime("%Y-%m-%dT%H:%M"))
        kwargs.setdefault("fmt_dt", fmt_dt)
        kwargs.setdefault("fmt_money", fmt_money)
        kwargs.setdefault("fmt_qty", fmt_qty)
        kwargs.setdefault("dt_local_for_input", utc_db_str_to_local_input)
        kwargs.setdefault("app_currency", APP_CURRENCY)
        if "fragment" not in kwargs:
            kwargs["fragment"] = request.args.get("fragment", "0") == "1"
        kwargs.setdefault("current_user", self.current_admin_id)
        return render_template(template_name, **kwargs)

    def redirect(self, url: str, code: int = 302):
        return redirect(url, code=code)

    def set_secure_cookie(self, name: str, value: str):
        if name == "admin_id":
            session["admin_id"] = int(value)
        elif name == "role":
            session["role"] = value
        elif name == "username":
            session["username"] = value
        else:
            session[name] = value

    def clear_cookie(self, name: str):
        session.pop(name, None)

    def audit(
        self,
        event: str,
        entity: str | None = None,
        entity_id: int | None = None,
        before: dict | None = None,
        after: dict | None = None,
        message: str | None = None,
        money_delta: float | None = None,
        level: str = "AUDIT",
    ):
        aid = self.current_admin_id
        username = None
        if aid:
            row = fetchone(self.db, "SELECT username FROM users WHERE id=?", (aid,))
            username = row["username"] if row else None
        audit_log(
            self.db,
            level=level,
            event=event,
            actor_admin_id=aid,
            actor_username=username,
            ip=self.request.remote_ip or None,
            user_agent=(self.request.headers.get("User-Agent") or "").strip() or None,
            request_id=self.request_id,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
            message=message,
            money_delta=money_delta,
        )


class AdminRequiredHandler(FlaskBaseHandler):
    pass


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login"))
        row = fetchone(g.db, "SELECT is_active FROM users WHERE id=?", (session["admin_id"],))
        if not row or int(row["is_active"]) != 1:
            session.clear()
            return redirect(url_for("login"))
        h = FlaskBaseHandler()
        if h.is_role_viewer and request.method.upper() == "POST":
            if not (request.path or "").startswith("/profile"):
                abort(403)
        return view_func(*args, **kwargs)

    return wrapped
