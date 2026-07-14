from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from urllib.parse import quote_plus

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session, url_for
_ROOT = Path(__file__).resolve().parents[1]

from db import DB, fetchone
from flask_base import admin_required, fmt_dt, fmt_money, fmt_qty
from services.log import audit_log
from settings import (
    APP_VERSION,
    APP_DEBUG,
    BLOCK_SEARCH_INDEXING,
    COOKIE_SECRET,
    SESSION_IDLE_TIMEOUT_MINUTES,
    STATIC_CACHE_BUSTER,
    MAX_CONTENT_LENGTH,
)


def _auth_context_for_template() -> dict:
    """Same auth fields as FlaskBaseHandler.render() so error pages work with base.html."""
    aid = session.get("admin_id")
    username = session.get("username")
    role_raw = session.get("role")
    if role_raw:
        role = str(role_raw)
    elif aid == 1:
        role = "admin"
    else:
        role = None
    return {
        "current_user": aid,
        "current_admin_id": aid,
        "current_username": username,
        "current_role": role,
        "is_superadmin": aid == 1,
        "is_admin_role": (role or "") == "admin",
        "is_manager_role": (role or "") == "manager",
        "is_viewer_role": (role or "") == "viewer",
    }


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_ROOT / "templates"),
        static_folder=str(_ROOT / "static"),
        static_url_path="/static",
    )
    app.secret_key = COOKIE_SECRET
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SESSION_COOKIE_NAME"] = "dc_session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if not APP_DEBUG:
        app.config["SESSION_COOKIE_SECURE"] = True

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-XSS-Protection", "1; mode=block")
        if not APP_DEBUG:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    @app.template_global()
    def url_escape(s):
        return quote_plus(str(s), safe="")

    app.jinja_env.globals.update(str=str, int=int, float=float, len=len)
    app.jinja_env.auto_reload = True
    app.jinja_env.globals["block_search_indexing"] = BLOCK_SEARCH_INDEXING
    app.jinja_env.globals["static_cache_buster"] = STATIC_CACHE_BUSTER

    @app.after_request
    def _static_cache_headers(resp):
        p = (request.path or "").strip()
        if p.startswith("/static/"):
            resp.headers.setdefault(
                "Cache-Control",
                "public, max-age=0, must-revalidate",
            )
        return resp

    if BLOCK_SEARCH_INDEXING:

        @app.after_request
        def _block_search_indexing(resp):
            resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
            return resp

    @app.route("/robots.txt")
    def robots_txt():
        if BLOCK_SEARCH_INDEXING:
            body = "User-agent: *\nDisallow: /\n"
        else:
            body = "User-agent: *\nAllow: /\n"
        return Response(body, mimetype="text/plain; charset=utf-8")

    if DB.conn is None:
        DB.init()

    @app.before_request
    def _attach_db():
        g.db = DB.conn

    @app.before_request
    def _session_idle_timeout_guard():
        p = (request.path or "").strip()
        if p == "/login" or p == "/robots.txt" or p.startswith("/static/") or p.startswith("/bootstrap"):
            return None
        aid_raw = session.get("admin_id")
        if not aid_raw:
            return None
        now_ts = int(time.time())
        timeout_seconds = max(1, int(SESSION_IDLE_TIMEOUT_MINUTES)) * 60
        last_activity_raw = session.get("last_activity_ts")
        if last_activity_raw is not None:
            try:
                last_activity_ts = int(float(last_activity_raw))
            except Exception:
                last_activity_ts = 0
            if last_activity_ts > 0 and (now_ts - last_activity_ts) > timeout_seconds:
                aid = int(aid_raw)
                u = fetchone(g.db, "SELECT username, role FROM users WHERE id=?", (aid,))
                audit_log(
                    g.db,
                    level="AUDIT",
                    event="session_expired",
                    actor_admin_id=aid,
                    actor_username=u["username"] if u else None,
                    ip=(request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For") or request.remote_addr or "").strip() or None,
                    user_agent=(request.headers.get("User-Agent") or "").strip() or None,
                    request_id=getattr(g, "request_id", None),
                    entity="auth",
                    before={"username": u["username"] if u else None},
                    after={
                        "reason": "idle_timeout",
                        "role": u["role"] if u else None,
                        "idle_timeout_minutes": int(SESSION_IDLE_TIMEOUT_MINUTES),
                    },
                    message="session_expired",
                )
                session.clear()
                return redirect(url_for("login", expired="1"))
        session["last_activity_ts"] = now_ts
        return None

    from realtime import rt_poll
    from views import (
        LoginHandler,
        LogoutHandler,
        ModalCloseHandler,
        DashboardHandler,
        ClientsHandler,
        ClientEditHandler,
        CategoriesHandler,
        CategoryEditHandler,
        SubcategoriesHandler,
        SubcategoryCreateHandler,
        SubcategoryEditHandler,
        ApiSubcategoriesHandler,
        ApiProductsHandler,
        ApiStockBalanceHandler,
        ProductsHandler,
        ProductEditHandler,
        CatalogHandler,
        ApiCategoryInlineHandler,
        ApiSubcategoryInlineHandler,
        StockInDocsHandler,
        StockInHandler,
        StockOutDocsHandler,
        StockOutHandler,
        InventoryDocsHandler,
        InventoryHandler,
        DocumentPdfHandler,
        StockMovesHandler,
        UsersHandler,
        UserEditHandler,
        ProfileHandler,
        LogsHandler,
    )

    @app.route("/login", methods=["GET", "POST"], endpoint="login")
    def login():
        h = LoginHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/logout", methods=["GET"])
    def logout():
        return LogoutHandler().get()

    @app.route("/realtime/poll", methods=["GET"])
    @admin_required
    def realtime_poll():
        try:
            since = int(request.args.get("since", "0") or 0)
        except ValueError:
            since = 0
        context = (request.args.get("context") or "").strip()
        client = (request.args.get("client") or "").strip()
        if not context:
            return jsonify({"error": "context required"}), 400
        try:
            inv, last = rt_poll(g.db, since, context, client)
            return jsonify({"invalidate": inv, "last_id": last})
        except sqlite3.Error:
            return jsonify({"invalidate": False, "last_id": max(0, since), "error": "db_busy"}), 200

    @app.route("/", methods=["GET"])
    @app.route("/admin", methods=["GET"])
    @admin_required
    def dashboard():
        return DashboardHandler().get()

    @app.route("/clients", methods=["GET", "POST"])
    @admin_required
    def clients():
        h = ClientsHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/clients/<int:client_id>", methods=["GET", "POST"])
    @admin_required
    def client_edit(client_id: int):
        h = ClientEditHandler()
        return h.get(str(client_id)) if request.method == "GET" else h.post(str(client_id))

    @app.route("/categories", methods=["GET", "POST"])
    @admin_required
    def categories():
        h = CategoriesHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/categories/<int:category_id>", methods=["GET", "POST"])
    @admin_required
    def category_edit(category_id: int):
        h = CategoryEditHandler()
        return h.get(str(category_id)) if request.method == "GET" else h.post(str(category_id))

    @app.route("/subcategories", methods=["GET", "POST"])
    @admin_required
    def subcategories():
        if request.method == "POST":
            return SubcategoryCreateHandler().post()
        return SubcategoriesHandler().get()

    @app.route("/subcategories/create", methods=["POST"])
    @admin_required
    def subcategory_create():
        return SubcategoryCreateHandler().post()

    @app.route("/subcategories/<int:subcategory_id>", methods=["GET", "POST"])
    @admin_required
    def subcategory_edit(subcategory_id: int):
        h = SubcategoryEditHandler()
        return h.get(str(subcategory_id)) if request.method == "GET" else h.post(str(subcategory_id))

    @app.route("/api/subcategories", methods=["GET"])
    @admin_required
    def api_subcategories():
        return ApiSubcategoriesHandler().get()

    @app.route("/api/products", methods=["GET"])
    @admin_required
    def api_products():
        return ApiProductsHandler().get()

    @app.route("/api/stock/balance", methods=["GET"])
    @admin_required
    def api_stock_balance():
        return ApiStockBalanceHandler().get()

    @app.route("/products", methods=["GET", "POST"])
    @admin_required
    def products():
        h = ProductsHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/products/<int:product_id>", methods=["GET", "POST"])
    @admin_required
    def product_edit(product_id: int):
        h = ProductEditHandler()
        return h.get(str(product_id)) if request.method == "GET" else h.post(str(product_id))

    @app.route("/catalog", methods=["GET"])
    @admin_required
    def catalog():
        return CatalogHandler().get()

    @app.route("/api/catalog/category", methods=["POST"])
    @admin_required
    def api_catalog_category():
        return ApiCategoryInlineHandler().post()

    @app.route("/api/catalog/subcategory", methods=["POST"])
    @admin_required
    def api_catalog_subcategory():
        return ApiSubcategoryInlineHandler().post()

    @app.route("/stock/in/docs", methods=["GET"])
    @admin_required
    def stock_in_docs():
        return StockInDocsHandler().get()

    @app.route("/stock/in", methods=["GET", "POST"])
    @admin_required
    def stock_in():
        h = StockInHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/stock/out/docs", methods=["GET"])
    @admin_required
    def stock_out_docs():
        return StockOutDocsHandler().get()

    @app.route("/stock/out", methods=["GET", "POST"])
    @admin_required
    def stock_out():
        h = StockOutHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/stock/inventory/docs", methods=["GET"])
    @admin_required
    def inventory_docs():
        return InventoryDocsHandler().get()

    @app.route("/stock/inventory", methods=["GET", "POST"])
    @admin_required
    def inventory():
        h = InventoryHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/stock/in/pdf", methods=["GET"])
    @admin_required
    def stock_in_pdf():
        return DocumentPdfHandler().get("stock_in")

    @app.route("/stock/out/pdf", methods=["GET"])
    @admin_required
    def stock_out_pdf():
        return DocumentPdfHandler().get("stock_out")

    @app.route("/stock/inventory/pdf", methods=["GET"])
    @admin_required
    def inventory_pdf():
        return DocumentPdfHandler().get("inventory")

    @app.route("/stock/moves", methods=["GET"])
    @admin_required
    def stock_moves():
        return StockMovesHandler().get()

    @app.route("/admins", methods=["GET", "POST"])
    @admin_required
    def admins():
        h = UsersHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/admins/<int:admin_id>", methods=["GET", "POST"])
    @admin_required
    def admin_edit(admin_id: int):
        h = UserEditHandler()
        return h.get(str(admin_id)) if request.method == "GET" else h.post(str(admin_id))

    @app.route("/profile", methods=["GET", "POST"])
    @admin_required
    def profile():
        h = ProfileHandler()
        return h.get() if request.method == "GET" else h.post()

    @app.route("/logs", methods=["GET"])
    @app.route("/logs/", methods=["GET"])
    @admin_required
    def logs():
        return LogsHandler().get()

    @app.route("/modal/close", methods=["GET"])
    @admin_required
    def modal_close():
        return ModalCloseHandler().get()

    @app.errorhandler(404)
    def err404(e):
        uri_short = (request.full_path or request.path or "")[:400]
        if isinstance(uri_short, bytes):
            uri_short = uri_short.decode("utf-8", "replace")

        return (
            render_template(
                "404.html",
                fragment=False,
                title="404 - страница не найдена",
                err_http_code=404,
                err_heading="Эта страница не найдена",
                err_lead="Проверьте адрес в строке браузера или вернитесь на главную.",
                err_uri=uri_short,
                app_version=APP_VERSION,
                fmt_dt=fmt_dt,
                fmt_money=fmt_money,
                **_auth_context_for_template(),
            ),
            404,
        )

    @app.errorhandler(403)
    def err403(e):
        uri_short = (request.full_path or request.path or "")[:400]
        if isinstance(uri_short, bytes):
            uri_short = uri_short.decode("utf-8", "replace")

        return (
            render_template(
                "404.html",
                fragment=False,
                title="403 - доступ запрещён",
                err_http_code=403,
                err_heading="Доступ запрещён",
                err_lead="У вас недостаточно прав для доступа к этой странице. Обратитесь к руководителю для получения доступа.",
                err_uri=uri_short,
                app_version=APP_VERSION,
                fmt_dt=fmt_dt,
                fmt_money=fmt_money,
                **_auth_context_for_template(),
            ),
            403,
        )

    return app
