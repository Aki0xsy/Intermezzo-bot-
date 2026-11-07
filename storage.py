"""Простое хранилище заявок на SQLite. Без внешних зависимостей."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

# DATA_DIR можно вынести на Railway volume (например /data), иначе ./data
DB_PATH = Path(os.getenv("DATA_DIR") or Path(__file__).parent / "data") / "registrations.db"


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with closing(get_conn(db_path)) as conn:
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
        # миграция старой базы: пароль, кто и когда рассмотрел заявку
        cols = {r[1] for r in conn.execute("PRAGMA table_info(registrations)")}
        for col in ("password", "reviewed_by", "reviewed_at"):
            if col not in cols:
                conn.execute(f"ALTER TABLE registrations ADD COLUMN {col} TEXT")
        conn.commit()


def save_application(
    telegram_id: int,
    telegram_username: str | None,
    data: dict,
    db_path: Path | None = None,
) -> tuple[bool, str]:
    """Сохраняет заявку. Возвращает (ok, сообщение).
    Отклонённая ранее заявка этого пользователя заменяется новой."""
    init_db(db_path)
    try:
        with closing(get_conn(db_path)) as conn:
            conn.execute(
                "DELETE FROM registrations WHERE telegram_id = ? AND status = 'rejected'",
                (telegram_id,),
            )
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
        if "minecraft_nick" in msg:
            return False, "Этот ник Minecraft уже зарегистрирован."
        if "telegram_id" in msg:
            return False, "У тебя уже есть заявка."
        return False, f"Ошибка базы: {e}"


def _one(sql: str, args: tuple, db_path: Path | None = None):
    init_db(db_path)
    with closing(get_conn(db_path)) as conn:
        return conn.execute(sql, args).fetchone()


def check_duplicate_minecraft_nick(nick: str, db_path: Path | None = None) -> bool:
    """Ник занят, если есть НЕ отклонённая заявка с таким ником."""
    return _one(
        "SELECT 1 FROM registrations WHERE lower(minecraft_nick) = lower(?) AND status != 'rejected' LIMIT 1",
        (nick,), db_path,
    ) is not None


def check_duplicate_telegram(telegram_id: int, db_path: Path | None = None) -> bool:
    """Есть активная (pending/approved) заявка. После отклонения можно подать новую."""
    return _one(
        "SELECT 1 FROM registrations WHERE telegram_id = ? AND status != 'rejected' LIMIT 1",
        (telegram_id,), db_path,
    ) is not None


def get_by_telegram(telegram_id: int, db_path: Path | None = None):
    return _one("SELECT * FROM registrations WHERE telegram_id = ?", (telegram_id,), db_path)


def get_by_id(reg_id: int, db_path: Path | None = None):
    return _one("SELECT * FROM registrations WHERE id = ?", (reg_id,), db_path)


def set_status(reg_id: int, status: str, admin_id: int, db_path: Path | None = None) -> bool:
    """pending -> approved/rejected. False, если заявку уже рассмотрел другой админ."""
    init_db(db_path)
    with closing(get_conn(db_path)) as conn:
        cur = conn.execute(
            "UPDATE registrations SET status = ?, reviewed_by = ?, reviewed_at = datetime('now') "
            "WHERE id = ? AND status = 'pending'",
            (status, str(admin_id), reg_id),
        )
        conn.commit()
        return cur.rowcount == 1


def set_password(telegram_id: int, password: str, db_path: Path | None = None) -> bool:
    """Пароль можно задать только одобренной заявке."""
    init_db(db_path)
    with closing(get_conn(db_path)) as conn:
        cur = conn.execute(
            "UPDATE registrations SET password = ? WHERE telegram_id = ? AND status = 'approved'",
            (password, telegram_id),
        )
        conn.commit()
        return cur.rowcount == 1
