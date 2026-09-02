import os


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


APP_PASSWORD = _require("APP_PASSWORD")
SESSION_SECRET = _require("SESSION_SECRET")
ENCRYPTION_KEY = _require("ENCRYPTION_KEY")
DATABASE_URL = _require("DATABASE_URL")
PORT = int(os.environ.get("PORT", "8000"))
SESSION_SECURE = os.environ.get("SESSION_SECURE", "false").lower() in {
    "1",
    "true",
    "yes",
}
DB_CONNECT_RETRIES = int(os.environ.get("DB_CONNECT_RETRIES", "30"))
DB_CONNECT_DELAY_SEC = float(os.environ.get("DB_CONNECT_DELAY_SEC", "2"))
