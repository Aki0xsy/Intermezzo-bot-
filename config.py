"""Настройки бота. Токен и админы берутся из переменных окружения (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]
MIN_AGE = int(os.getenv("MIN_AGE", "12"))
MAX_AGE = int(os.getenv("MAX_AGE", "99"))
SERVER_NAME = os.getenv("SERVER_NAME", "Minecraft Server")
SERVER_IP = os.getenv("SERVER_IP", "mc.example.com")
# Режим вебхуков (для бесплатного хостинга Railway/Render)
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "0") == "1"
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", "8000"))
# --- Google Sheets ---
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Sheet1").strip() or "Sheet1"
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
SHEET_HEADER = ["Дата", "Имя", "Возраст", "Страна", "Ник Minecraft", "Ник Discord", "Telegram ID", "Telegram", "Статус", "Принял (админ)", "Пароль", "Блокировка"]
# --- API для серверного мода (Minecraft) ---
# Общий секрет бота и мода. Пусто = API выключен.
PLUGIN_API_TOKEN = os.getenv("PLUGIN_API_TOKEN", "").strip()
