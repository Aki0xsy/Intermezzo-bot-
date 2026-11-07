"""Простое хранилище заявок на SQLite. Без внешних зависимостей."""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "registrations.db"


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                telegram_username TEXT,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                country TEXT NOT NULL,
                minecraft_nick TEXT NOT NULL UNIQUE,
                discord_nick TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                raw_json TEXT
            )
            """
        )
        conn.commit()


def save_application(
    telegram_id: int,
    telegram_username: str | None,
    data: dict,
    db_path: Path | None = None,
) -> tuple[bool, str]:
    """Сохраняет заявку. Возвращает (ok, сообщение)."""
    init_db(db_path)
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                """
                INSERT INTO registrations
                    (telegram_id, telegram_username, name, age, country,
                     minecraft_nick, discord_nick, status, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    telegram_id,
                    telegram_username,
                    data["name"],
                    int(data["age"]),
                    data["country"],
                    data["minecraft_nick"],
                    data["discord_nick"],
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            conn.commit()
        return True, "Заявка сохранена."
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "minecraft_nick" in msg or "unique" in msg and "minecraft" in msg:
            return False, "Этот ник Minecraft уже зарегистрирован."
        if "telegram_id" in msg:
            return False, "У тебя уже есть заявка."
        return False, f"Ошибка базы: {e}"


def check_duplicate_minecraft_nick(nick: str, db_path: Path | None = None) -> bool:
    init_db(db_path)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM registrations WHERE lower(minecraft_nick) = lower(?) LIMIT 1",
            (nick,),
        ).fetchone()
        return row is not None


def check_duplicate_telegram(telegram_id: int, db_path: Path | None = None) -> bool:
    init_db(db_path)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM registrations WHERE telegram_id = ? LIMIT 1", (telegram_id,)
        ).fetchone()
        return row is not None
