"""FSM-хранилище aiogram поверх SQLite (переживает рестарт/redeploy, если data/ на volume).

Наследуется от BaseStorage — иначе в нём нет update_data() и aiogram падает
с AttributeError на первом же state.update_data(...).
"""
import asyncio
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS fsm (
                    key TEXT PRIMARY KEY,
                    state TEXT,
                    data TEXT NOT NULL DEFAULT '{}'
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        # timeout — не падаем на "database is locked"
        return closing_conn(sqlite3.connect(str(self.path), timeout=15))

    @staticmethod
    def _key(key: StorageKey) -> str:
        return ":".join(
            str(getattr(key, name, None))
            for name in ("bot_id", "chat_id", "user_id", "thread_id", "business_connection_id", "destiny")
        )

    # ---- sync-часть (вызывается через to_thread) ----
    def _set_state(self, key: str, state: Optional[str]) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO fsm (key, state, data) VALUES (?, ?, '{}') "
                "ON CONFLICT(key) DO UPDATE SET state=excluded.state",
                (key, state),
            )

    def _get_state(self, key: str) -> Optional[str]:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT state FROM fsm WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def _set_data(self, key: str, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO fsm (key, state, data) VALUES (?, NULL, ?) "
                "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                (key, payload),
            )

    def _get_data(self, key: str) -> Dict[str, Any]:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT data FROM fsm WHERE key=?", (key,)).fetchone()
        if not row or not row[0]:
            return {}
        try:
            value = json.loads(row[0])
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    # ---- BaseStorage API ----
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await asyncio.to_thread(self._set_state, self._key(key), value)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return await asyncio.to_thread(self._get_state, self._key(key))

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await asyncio.to_thread(self._set_data, self._key(key), dict(data))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_data, self._key(key))

    async def close(self) -> None:
        return None


class closing_conn:
    """Контекст: commit/rollback + ОБЯЗАТЕЛЬНОЕ закрытие соединения
    (штатный `with sqlite3.connect()` соединение не закрывает -> утечка дескрипторов)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
