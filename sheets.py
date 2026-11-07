"""Запись заявок в Google Sheets через Service Account.

Поддерживает:
- GOOGLE_SERVICE_ACCOUNT_JSON — содержимое JSON файла (одна строка),
  например из Railway/Render Variables
- GOOGLE_SERVICE_ACCOUNT_FILE — путь к JSON файлу

Таблица должна быть расшарена на client_email сервисного аккаунта с правом Editor.
Лист должен уже содержать шапку:
Дата | Имя | Возраст | Страна | Ник Minecraft | Ник Discord | Telegram ID | Telegram | Статус
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)


def _get_creds():
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    raw = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "") or config.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if raw:
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=scopes)

    file_path = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "") or config.GOOGLE_SERVICE_ACCOUNT_FILE or "").strip()
    if file_path:
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(__file__).parent / path
        if path.exists():
            return Credentials.from_service_account_file(str(path), scopes=scopes)

    # Фолбэк: стандартный путь рядом с бот-ом
    default = Path(__file__).parent / "service_account.json"
    if default.exists():
        return Credentials.from_service_account_file(str(default), scopes=scopes)

    return None


def _get_client():
    import gspread

    creds = _get_creds()
    if creds is None:
        return None
    return gspread.authorize(creds)


def _resolve_worksheet(sh):
    """Ищет лист по имени, если не нашёл — берёт первый лист."""
    name = config.GOOGLE_SHEET_NAME or "Sheet1"
    try:
        return sh.worksheet(name)
    except Exception:
        try:
            return sh.worksheets()[0]
        except Exception:
            return sh.sheet1


def append_row_sync(row: list[str]) -> bool:
    """Синхронная запись, вызывать через asyncio.to_thread."""
    sheet_id = config.GOOGLE_SHEETS_ID
    sheet_name = config.GOOGLE_SHEET_NAME or "Sheet1"
    if not sheet_id:
        return False
    try:
        client = _get_client()
        if client is None:
            log.warning("Google Sheets: credentials not found, skip")
            return False
        sh = client.open_by_key(sheet_id)
        ws = _resolve_worksheet(sh)
        ws.append_row(row, value_input_option="RAW")
        log.info("Google Sheets: row appended")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Google Sheets append failed: %s", e)
        return False


def ensure_header_sync() -> bool:
    """Проверяет/создаёт шапку, если лист пустой."""
    sheet_id = config.GOOGLE_SHEETS_ID
    sheet_name = config.GOOGLE_SHEET_NAME or "Sheet1"
    if not sheet_id:
        return False
    try:
        client = _get_client()
        if client is None:
            return False
        sh = client.open_by_key(sheet_id)
        ws = _resolve_worksheet(sh)
        vals = ws.get_all_values()
        if not vals or not any(c.strip() for c in vals[0]):
            ws.append_row(config.SHEET_HEADER, value_input_option="RAW")
            log.info("Google Sheets: header created")
        else:
            # дописываем новые колонки (Принял / Пароль / Блокировка), не трогая существующую шапку
            row1 = vals[0]
            for idx, title in enumerate(config.SHEET_HEADER):
                if idx >= 9 and (len(row1) <= idx or not row1[idx].strip()):
                    ws.update_cell(1, idx + 1, title)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Google Sheets ensure_header failed: %s", e)
        return False


def update_row_sync(telegram_id: int, values: dict[int, str]) -> bool:
    """Обновляет ячейки в строке игрока (поиск по Telegram ID в 7-й колонке; если строк
    несколько — берётся последняя). values: {номер_колонки: значение}. Колонки: 9 — Статус,
    10 — Принял (админ), 11 — Пароль, 12 — Блокировка."""
    if not config.GOOGLE_SHEETS_ID:
        return False
    try:
        from gspread.utils import rowcol_to_a1

        client = _get_client()
        if client is None:
            return False
        ws = _resolve_worksheet(client.open_by_key(config.GOOGLE_SHEETS_ID))
        cells = ws.findall(str(telegram_id), in_column=7)
        if not cells:
            log.warning("Google Sheets: строка с Telegram ID %s не найдена", telegram_id)
            return False
        row = cells[-1].row
        ws.batch_update(
            [{"range": rowcol_to_a1(row, col), "values": [[val]]} for col, val in values.items()],
            value_input_option="RAW",
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Google Sheets row update failed: %s", e)
        return False


async def update_row(telegram_id: int, values: dict[int, str]) -> bool:
    if not config.GOOGLE_SHEETS_ID:
        return False
    return await asyncio.to_thread(update_row_sync, telegram_id, values)


def build_row(data: dict, telegram_id: int, telegram_username: str | None) -> list[str]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tg = f"@{telegram_username}" if telegram_username else ""
    return [
        now,
        data.get("name", ""),
        str(data.get("age", "")),
        data.get("country", ""),
        data.get("minecraft_nick", ""),
        data.get("discord_nick", ""),
        str(telegram_id),
        tg,
        "pending",
    ]


async def append_application(data: dict, telegram_id: int, telegram_username: str | None) -> bool:
    """Асинхронная обёртка, не блокирует event loop."""
    if not config.GOOGLE_SHEETS_ID:
        return False
    row = build_row(data, telegram_id, telegram_username)
    return await asyncio.to_thread(append_row_sync, row)
