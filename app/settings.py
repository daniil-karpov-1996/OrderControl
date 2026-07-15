import os


def _load_dotenv():
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env_path = os.path.join(base_dir, ".env")
        if not os.path.exists(env_path):
            return
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        return


_load_dotenv()


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


SQLITE_PATH = os.getenv("SQLITE_PATH", "./data/ordercontrol.sqlite3")
BLOCK_SEARCH_INDEXING = _env_flag("BLOCK_SEARCH_INDEXING", "0")
APP_PORT = int(os.getenv("APP_PORT", "80"))
COOKIE_SECRET = os.getenv("COOKIE_SECRET", "change-me-please-to-long-random-string")
APP_VERSION = os.getenv("APP_VERSION", "v2.0")
STATIC_CACHE_BUSTER = (os.getenv("STATIC_CACHE_BUSTER") or APP_VERSION).strip()
LOGS_MAX_ROWS = int(os.getenv("LOGS_MAX_ROWS", "200000"))
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "5"))
SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", "60"))
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_BLOCK_MINUTES = int(os.getenv("LOGIN_BLOCK_MINUTES", "10"))
APP_DEBUG = _env_flag("APP_DEBUG", "0")
APP_CURRENCY = os.getenv("APP_CURRENCY", "$").strip()
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(5 * 1024 * 1024)))
PRODUCT_UPLOAD_DIR = os.getenv("PRODUCT_UPLOAD_DIR", "./static/uploads/products").strip()
