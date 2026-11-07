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
        for col, typ in (
            ("password", "TEXT"), ("reviewed_by", "TEXT"), ("reviewed_at", "TEXT"),
            ("blocked_until", "TEXT"), ("blocked_by", "TEXT"),
            ("restore_status", "TEXT"),                 # NULL / requested / denied
            ("expiry_notified", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE registrations ADD COLUMN {col} {typ}")
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


def get_by_nick(nick: str, db_path: Path | None = None):
    return _one(
        "SELECT * FROM registrations WHERE lower(minecraft_nick) = lower(?) AND status != 'rejected'",
        (nick,), db_path,
    )


def _exec(sql: str, args: tuple, db_path: Path | None = None) -> int:
    init_db(db_path)
    with closing(get_conn(db_path)) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount


def block(reg_id: int, admin_id: int, hours: int = 24, db_path: Path | None = None) -> bool:
    """Блокировка одобренного аккаунта на N часов. False, если аккаунт не в статусе approved."""
    return _exec(
        "UPDATE registrations SET status='blocked', blocked_until=datetime('now', ?), blocked_by=?, "
        "restore_status=NULL, expiry_notified=0 WHERE id=? AND status='approved'",
        (f"+{int(hours)} hours", str(admin_id), reg_id), db_path,
    ) == 1


def unblock(reg_id: int, db_path: Path | None = None) -> bool:
    """Ручная разблокировка админом."""
    return _exec(
        "UPDATE registrations SET status='approved', blocked_until=NULL, restore_status=NULL, "
        "expiry_notified=0 WHERE id=? AND status='blocked'",
        (reg_id,), db_path,
    ) == 1


def is_block_expired(reg, db_path: Path | None = None) -> bool:
    row = _one("SELECT datetime('now') >= ?", (reg["blocked_until"] or "",), db_path)
    return bool(row[0])


def due_expiry_notifications(db_path: Path | None = None) -> list:
    init_db(db_path)
    with closing(get_conn(db_path)) as conn:
        return conn.execute(
            "SELECT * FROM registrations WHERE status='blocked' AND expiry_notified=0 "
            "AND blocked_until <= datetime('now')"
        ).fetchall()


def mark_expiry_notified(reg_id: int, db_path: Path | None = None) -> None:
    _exec("UPDATE registrations SET expiry_notified=1 WHERE id=?", (reg_id,), db_path)


def request_restore(telegram_id: int, db_path: Path | None = None) -> str:
    """-> 'ok' | 'not_blocked' | 'active' (срок не вышел) | 'already' (обращение уже отправлено)."""
    reg = get_by_telegram(telegram_id, db_path)
    if not reg or reg["status"] != "blocked":
        return "not_blocked"
    if not is_block_expired(reg, db_path):
        return "active"
    if reg["restore_status"] == "requested":
        return "already"
    _exec("UPDATE registrations SET restore_status='requested' WHERE id=? AND status='blocked'", (reg["id"],), db_path)
    return "ok"


def decide_restore(reg_id: int, approve: bool, db_path: Path | None = None) -> bool:
    """Решение админа по обращению. False — уже рассмотрено."""
    if approve:
        sql = ("UPDATE registrations SET status='approved', blocked_until=NULL, restore_status=NULL, "
               "expiry_notified=0 WHERE id=? AND status='blocked' AND restore_status='requested'")
    else:
        sql = ("UPDATE registrations SET restore_status='denied' "
               "WHERE id=? AND status='blocked' AND restore_status='requested'")
    return _exec(sql, (reg_id,), db_path) == 1


def restore_from_rows(rows: list[list[str]], db_path: Path | None = None) -> int:
    """Восстанавливает заявки из строк Google-таблицы (если база на Railway сбросилась).
    Добавляет только тех, кого в базе нет (по Telegram ID и нику). Отклонённых пропускает.
    Колонки: 0 дата, 1 имя, 2 возраст, 3 страна, 4 ник MC, 5 Discord, 6 Telegram ID,
    7 Telegram, 8 статус, 9 принял, 10 пароль, 11 блокировка."""
    init_db(db_path)
    latest: dict[int, list[str]] = {}
    for row in rows:
        row = list(row) + [""] * (12 - len(row))
        if row[6].strip().isdigit():
            latest[int(row[6].strip())] = row  # при нескольких строках берётся последняя
    added = 0
    with closing(get_conn(db_path)) as conn:
        for tg_id, row in latest.items():
            status = row[8].strip().lower()
            if status not in ("pending", "approved", "blocked"):
                continue
            try:
                age = int(row[2].strip())
            except ValueError:
                continue
            blocked_until = "datetime('now')" if status == "blocked" else "NULL"
            try:
                cur = conn.execute(
                    f"""INSERT OR IGNORE INTO registrations
                        (telegram_id, telegram_username, name, age, country, minecraft_nick,
                         discord_nick, status, password, reviewed_by, blocked_until, expiry_notified)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {blocked_until}, 0)""",
                    (tg_id, row[7].strip().lstrip("@") or None, row[1].strip(), age, row[3].strip(),
                     row[4].strip(), row[5].strip(), status, row[10] or None, row[9].strip() or None),
                )
                added += cur.rowcount
            except sqlite3.IntegrityError:
                continue
        conn.commit()
    return added
