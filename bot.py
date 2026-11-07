"""Telegram-бот для регистрации на Minecraft-сервер.

Поток: /start -> кнопка "Заполнить заявку" -> опросник из 5 вопросов ->
проверка данных -> превью -> подтверждение -> сохранение + уведомление админа.

ВАЖНО: FSM storage — SQLite на диске (Railway даёт volume). В memory всё слетает
при каждом redeploy/краше, и кнопка подтверждения уходит в "Сессия устарела".
"""
import asyncio
import html
import logging
from datetime import datetime, timezone
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart, ExceptionTypeFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.types.error_event import ErrorEvent
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import api
import config
import sheets
import server
import storage
from fsm_storage import SQLiteStorage
from validators import (
    validate_age,
    validate_country,
    validate_discord_nick,
    validate_minecraft_nick,
    validate_name,
    validate_password,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)


# ---------- FSM ----------

class RegStates(StatesGroup):
    name = State()
    age = State()
    country = State()
    minecraft_nick = State()
    discord_nick = State()
    confirm = State()


class PasswordStates(StatesGroup):
    set_password = State()      # после одобрения заявки
    change_password = State()   # смена из профиля


STEPS = [
    ("name", "👤 Как тебя зовут?", "Напиши имя (2–50 символов).", validate_name),
    ("age", "🎂 Сколько тебе лет?", "Введи возраст числом.", lambda v: validate_age(v, config.MIN_AGE, config.MAX_AGE)),
    ("country", "🌍 Из какой ты страны?", "Напиши страну, например: Россия.", validate_country),
    ("minecraft_nick", "⛏️ Твой ник в Minecraft?", "3–16 символов: английские буквы, цифры и _. Например: Steve_2026", validate_minecraft_nick),
    ("discord_nick", "🎮 Твой ник в Discord?", "Например: @steve_2026 или steve_2026", validate_discord_nick),
]

STEP_FIELDS = [s[0] for s in STEPS]
FIELD_LABELS = {
    "name": "👤 Имя",
    "age": "🎂 Возраст",
    "country": "🌍 Страна",
    "minecraft_nick": "⛏️ Ник Minecraft",
    "discord_nick": "🎮 Ник Discord",
}


def main_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    reg = storage.get_by_telegram(user_id) if user_id else None
    if reg and reg["status"] == "approved":
        b.button(text="👤 Профиль", callback_data="profile")
    elif reg and reg["status"] == "blocked":
        b.button(text="🔒 Аккаунт заблокирован", callback_data="restore_req")
    elif not reg or reg["status"] == "rejected":
        b.button(text="📝 Заполнить заявку", callback_data="start_reg")
    b.button(text="ℹ️ О сервере", callback_data="about_server")
    b.adjust(1)
    return b.as_markup()


def admin_kb(reg_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Принять", callback_data=f"adm:ok:{reg_id}")
    b.button(text="❌ Отклонить", callback_data=f"adm:no:{reg_id}")
    b.adjust(2)
    return b.as_markup()


def block_kb(reg_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔒 Заблокировать на 24 ч", callback_data=f"adm:blk:{reg_id}")
    return b.as_markup()


def block_confirm_kb(reg_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ Да, заблокировать", callback_data=f"adm:blkyes:{reg_id}")
    b.button(text="↩️ Нет", callback_data=f"adm:blkno:{reg_id}")
    b.adjust(2)
    return b.as_markup()


def restore_admin_kb(reg_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Восстановить", callback_data=f"rst:ok:{reg_id}")
    b.button(text="❌ Отказать", callback_data=f"rst:no:{reg_id}")
    b.adjust(2)
    return b.as_markup()


def restore_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📨 Подать заявку на восстановление", callback_data="restore_req")
    return b.as_markup()


def profile_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data="profile")
    b.button(text="🔑 Сменить пароль", callback_data="change_password")
    b.adjust(1)
    return b.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Всё верно, отправить", callback_data="confirm_reg")
    b.button(text="✏️ Заполнить заново", callback_data="restart_reg")
    b.button(text="❌ Отмена", callback_data="cancel_reg")
    b.adjust(1)
    return b.as_markup()


def cancel_kb() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.button(text="❌ Отмена")
    b.adjust(1)
    return b.as_markup(resize_keyboard=True)


def _who(user) -> str:
    return f"@{user.username}" if user.username else str(user.id)


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _remaining(blocked_until: str) -> str:
    left = int((_parse_utc(blocked_until) - datetime.now(timezone.utc)).total_seconds())
    if left <= 0:
        return "меньше минуты"
    h, m = left // 3600, (left % 3600) // 60
    return f"{h} ч {m} мин" if h else f"{m} мин"


def _collect(fsm_data: dict) -> dict:
    return {f: fsm_data[f] for f in STEP_FIELDS if f in fsm_data}


async def _safe_answer(callback: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """callback.answer() падает с 'query is too old' — это не повод ронять хендлер."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception as e:  # noqa: BLE001
        log.debug("callback.answer failed: %s", e)


def _e(value) -> str:
    return html.escape(str(value), quote=False)


def format_summary(data: dict) -> str:
    return (
        "📋 <b>Проверь данные:</b>\n\n"
        f"{FIELD_LABELS['name']}: <b>{_e(data.get('name','—'))}</b>\n"
        f"{FIELD_LABELS['age']}: <b>{_e(data.get('age','—'))}</b>\n"
        f"{FIELD_LABELS['country']}: <b>{_e(data.get('country','—'))}</b>\n"
        f"{FIELD_LABELS['minecraft_nick']}: <b>{_e(data.get('minecraft_nick','—'))}</b>\n"
        f"{FIELD_LABELS['discord_nick']}: <b>{_e(data.get('discord_nick','—'))}</b>\n\n"
        "Если всё верно — нажимай «✅ Всё верно»."
    )


def format_admin_message(data: dict, tg_id: int, tg_username: str | None) -> str:
    user_line = _e(f"@{tg_username}" if tg_username else str(tg_id))
    return (
        f"🆕 <b>Новая заявка!</b>\n\n"
        f"{FIELD_LABELS['name']}: <b>{_e(data.get('name','—'))}</b>\n"
        f"{FIELD_LABELS['age']}: <b>{_e(data.get('age','—'))}</b>\n"
        f"{FIELD_LABELS['country']}: <b>{_e(data.get('country','—'))}</b>\n"
        f"{FIELD_LABELS['minecraft_nick']}: <b>{_e(data.get('minecraft_nick','—'))}</b>\n"
        f"{FIELD_LABELS['discord_nick']}: <b>{_e(data.get('discord_nick','—'))}</b>\n"
        f"Telegram: {user_line} (id={tg_id})\n"
        f"Статус: pending"
    )


# ---------- Router ----------

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"👋 Привет! Я бот для регистрации на <b>{config.SERVER_NAME}</b>.\n\n"
        "Чтобы подать заявку, нажми «📝 Заполнить заявку» и ответь на 5 коротких вопросов:\n"
        "имя, возраст, страна, ник Minecraft и ник Discord.\n\n"
        "В любой момент можно прервать: /cancel",
        reply_markup=main_menu_kb(message.from_user.id),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    was = await state.get_state()
    await state.clear()
    if was:
        await message.answer("❌ Заявка отменена.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Меню:", reply_markup=main_menu_kb(message.from_user.id))


@router.message(F.text.in_({"❌ Отмена", "Отмена", "cancel"}))
async def text_cancel(message: Message, state: FSMContext) -> None:
    was = await state.get_state()
    await state.clear()
    if was:
        await message.answer("❌ Заявка отменена.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Меню:", reply_markup=main_menu_kb(message.from_user.id))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Команды:\n/start — главное меню\n/cancel — отменить заполнение\n/help — эта справка\n\n"
        "Вопросы по заполнению — просто напиши, и бот подскажет.",
        reply_markup=main_menu_kb(message.from_user.id),
    )


@router.message(F.text == "ℹ️ О сервере")
async def text_about(message: Message) -> None:
    await message.answer(
        f"<b>{config.SERVER_NAME}</b>\nIP: <code>{config.SERVER_IP}</code>\n\n"
        "Заполни заявку — и администрация рассмотрит её. После одобрения ты получишь доступ.",
        reply_markup=main_menu_kb(message.from_user.id),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "📝 Заполнить заявку")
async def text_start_reg(message: Message, state: FSMContext) -> None:
    await _begin_registration(message, state)


@router.callback_query(F.data == "about_server")
async def cb_about(callback: CallbackQuery) -> None:
    await callback.message.answer(
        f"<b>{config.SERVER_NAME}</b>\nIP: <code>{config.SERVER_IP}</code>\n\n"
        "Нажми «📝 Заполнить заявку», чтобы пройти опросник.",
        reply_markup=main_menu_kb(callback.from_user.id),
        parse_mode=ParseMode.HTML,
    )
    await _safe_answer(callback)


@router.callback_query(F.data == "start_reg")
async def cb_start_reg(callback: CallbackQuery, state: FSMContext) -> None:
    await _begin_registration(callback.message, state)
    await _safe_answer(callback)


@router.callback_query(F.data == "restart_reg")
async def cb_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _begin_registration(callback.message, state)
    await _safe_answer(callback)


@router.callback_query(F.data == "cancel_reg")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("❌ Заявка отменена.", reply_markup=main_menu_kb(callback.from_user.id))
    await _safe_answer(callback)


async def _begin_registration(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RegStates.name)
    await state.update_data(step_index=0)
    _, title, hint, _ = STEPS[0]
    await message.answer(
        f"{title}\n\n{hint}\n\nОтмена — /cancel",
        reply_markup=cancel_kb(),
        parse_mode=ParseMode.HTML,
    )


async def _ask_step(message: Message, state: FSMContext, step_index: int) -> None:
    field, title, hint, _ = STEPS[step_index]
    await state.set_state(getattr(RegStates, field))
    await state.update_data(step_index=step_index)
    await message.answer(
        f"{title}\n\n{hint}\n\nОтмена — /cancel",
        reply_markup=cancel_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(RegStates.name, RegStates.age, RegStates.country,
                            RegStates.minecraft_nick, RegStates.discord_nick))
async def handle_answer(message: Message, state: FSMContext) -> None:
    fsm_state = await state.get_state()
    field = fsm_state.split(":")[-1]
    step_index = STEP_FIELDS.index(field)
    _, _, _, validator = STEPS[step_index]

    if message.text and len(message.text) > 200:
        await message.answer("⚠️ Сообщение слишком длинное. Коротко и по делу 😄\n\nПопробуй ещё раз:")
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Напиши ответ текстом 👇")
        return

    ok, result = validator(raw)

    if ok and field == "minecraft_nick":
        if storage.check_duplicate_minecraft_nick(result):
            ok = False
            result = "Этот ник Minecraft уже зарегистрирован. Проверь раскладку/регистр или выбери другой ник."

    if not ok:
        _, title, hint, _ = STEPS[step_index]
        await message.answer(
            f"❌ {result}\n\n{title}\n\n{hint}",
            reply_markup=cancel_kb(),
            parse_mode=ParseMode.HTML,
        )
        return

    # ВАЖНО: update_data(data=...) попадает в позиционный параметр data, а не в kwargs,
    # поэтому поля храним плоско: {name:..., age:...}.
    await state.update_data({field: result})
    data = _collect(await state.get_data())
    log.info("FSM save: %s (%s)", field, list(data.keys()))

    next_index = step_index + 1
    if next_index < len(STEPS):
        await _ask_step(message, state, next_index)
    else:
        await state.set_state(RegStates.confirm)
        log.info("FSM confirm: %s", list(data.keys()))
        await message.answer(
            format_summary(data),
            reply_markup=confirm_kb(),
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == "confirm_reg", StateFilter(RegStates.confirm))
async def cb_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = _collect(await state.get_data())
    log.info("FSM confirm data: %s", list(data.keys()))

    if storage.check_duplicate_telegram(callback.from_user.id):
        await callback.message.answer(
            "⚠️ У тебя уже есть заявка. Если хочешь подать ещё одну — напиши админу.",
            reply_markup=main_menu_kb(callback.from_user.id),
        )
        await state.clear()
        await _safe_answer(callback)
        return
    if storage.check_duplicate_minecraft_nick(data.get("minecraft_nick", "")):
        await callback.message.answer(
            "⚠️ Этот ник Minecraft уже занят. Заполни заявку заново с другим ником.",
            reply_markup=main_menu_kb(callback.from_user.id),
        )
        await state.clear()
        await _safe_answer(callback)
        return

    required = ("name", "age", "country", "minecraft_nick", "discord_nick")
    if any(not str(data.get(field, "")).strip() for field in required):
        log.warning("Incomplete data in confirm: %s", data)
        await state.clear()
        await _safe_answer(callback, "Сессия заявки устарела", show_alert=True)
        await callback.message.answer(
            "⚠️ Сессия заявки устарела.\n\nНачни регистрацию заново: нажми «📝 Заполнить заявку».",
            reply_markup=main_menu_kb(callback.from_user.id),
        )
        return

    ok, msg = storage.save_application(
        telegram_id=callback.from_user.id,
        telegram_username=callback.from_user.username,
        data=data,
    )
    if not ok:
        await callback.message.answer(f"❌ Не удалось сохранить: {msg}", reply_markup=main_menu_kb(callback.from_user.id))
        await _safe_answer(callback)
        return

    await state.clear()
    await _safe_answer(callback, "Заявка отправлена!")
    await callback.message.answer(
        "🎉 <b>Заявка отправлена!</b>\n\n"
        f"{format_summary(data)}\n\n"
        "Администрация проверит её и, если всё ок, выдаст доступ.\n"
        "Обычно это занимает от пары часов до суток. Спасибо за терпение!",
        reply_markup=main_menu_kb(callback.from_user.id),
        parse_mode=ParseMode.HTML,
    )

    # Пишем в Google Sheets
    try:
        sheets_ok = await sheets.append_application(data, callback.from_user.id, callback.from_user.username)
        if not sheets_ok and config.GOOGLE_SHEETS_ID:
            log.warning("Заявка сохранена локально, но не попала в Google Sheets")
    except Exception as e:  # noqa: BLE001
        log.warning("Google Sheets error: %s", e)

    # Уведомляем админов
    admin_text = format_admin_message(data, callback.from_user.id, callback.from_user.username)
    reg = storage.get_by_telegram(callback.from_user.id)
    admin_markup = admin_kb(reg["id"]) if reg else None
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode=ParseMode.HTML, reply_markup=admin_markup)
        except Exception as e:  # noqa: BLE001 — не падаем, если админ заблокировал бота
            log.warning("Не удалось отправить админу %s: %s", admin_id, e)


# ---------- Решение админа ----------

@router.callback_query(F.data.regexp(r"^adm:(ok|no):\d+$"))
async def cb_admin_decision(callback: CallbackQuery, bot: Bot, dispatcher: Dispatcher) -> None:
    if callback.from_user.id not in config.ADMIN_IDS:
        await _safe_answer(callback, "Только для администраторов.", show_alert=True)
        return
    _, action, reg_id_s = callback.data.split(":")
    reg = storage.get_by_id(int(reg_id_s))
    if not reg:
        await _safe_answer(callback, "Заявка не найдена.", show_alert=True)
        return

    who = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    new_status = "approved" if action == "ok" else "rejected"
    if not storage.set_status(reg["id"], new_status, callback.from_user.id):
        # уже рассмотрена (другим админом или повторным нажатием) — просто убираем кнопки
        fresh = storage.get_by_id(reg["id"])
        await _safe_answer(callback, f"Уже рассмотрена: {fresh['status']}", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        return

    mark = "✅ Принята" if new_status == "approved" else "❌ Отклонена"
    try:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n<b>{mark}</b> — {_e(who)}",
            parse_mode=ParseMode.HTML,
            reply_markup=block_kb(reg["id"]) if new_status == "approved" else None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить сообщение админа: %s", e)
    await _safe_answer(callback, mark)

    reviewer = who if new_status == "approved" else f"отклонено: {who}"
    await sheets.update_row(reg["telegram_id"], {9: new_status, 10: reviewer})

    uid = reg["telegram_id"]
    try:
        if new_status == "approved":
            # переводим игрока в состояние «придумай пароль»
            ctx = FSMContext(storage=dispatcher.storage, key=StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid))
            await ctx.clear()
            await ctx.set_state(PasswordStates.set_password)
            await bot.send_message(
                uid,
                "🎉 <b>Твоя заявка принята!</b>\n\n"
                "Теперь придумай <b>пароль для входа на сервер</b> и отправь его следующим сообщением.\n"
                "6–32 символа: английские буквы, цифры, знаки, без пробелов.\n\n"
                "Сообщение с паролем я удалю из чата после сохранения.",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb(),
            )
        else:
            await bot.send_message(
                uid,
                "😔 К сожалению, заявка отклонена. Ты можешь подать новую — нажми «📝 Заполнить заявку».",
                reply_markup=main_menu_kb(uid),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось уведомить игрока %s: %s", uid, e)
        await callback.message.answer(
            f"⚠️ Не смог написать игроку {uid} (он не запускал бота или заблокировал его)."
        )


# ---------- Пароль и профиль ----------

async def _save_password(message: Message, state: FSMContext, first_time: bool) -> None:
    ok, result = validate_password(message.text or "")
    if not ok:
        await message.answer(f"❌ {result}\n\nПопробуй ещё раз:", reply_markup=cancel_kb())
        return
    if not storage.set_password(message.from_user.id, result):
        await state.clear()
        await message.answer("⚠️ Пароль можно задать только после одобрения заявки.",
                             reply_markup=main_menu_kb(message.from_user.id))
        return
    await state.clear()
    try:  # пароль не должен оставаться в истории чата
        await message.delete()
    except Exception:  # noqa: BLE001
        pass
    reg = storage.get_by_telegram(message.from_user.id)
    await sheets.update_row(message.from_user.id, {11: result})
    try:
        await server.push_password(reg["minecraft_nick"], result)
    except Exception as e:  # noqa: BLE001
        log.warning("push_password failed: %s", e)
    await message.answer(
        ("✅ Пароль создан!" if first_time else "✅ Пароль изменён!")
        + "\nПосмотреть его можно в профиле.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Открой профиль:", reply_markup=main_menu_kb(message.from_user.id))


@router.message(StateFilter(PasswordStates.set_password))
async def handle_set_password(message: Message, state: FSMContext) -> None:
    await _save_password(message, state, first_time=True)


@router.message(StateFilter(PasswordStates.change_password))
async def handle_change_password(message: Message, state: FSMContext) -> None:
    await _save_password(message, state, first_time=False)


async def _profile_text(reg) -> str:
    online = await server.is_online(reg["minecraft_nick"])
    online_line = {True: "🟢 Онлайн", False: "🔴 Не в сети", None: "⚪ Неизвестно (плагин сервера ещё не подключён)"}[online]
    password = reg["password"]
    return (
        "👤 <b>Профиль</b>\n\n"
        f"⛏️ Ник: <b>{_e(reg['minecraft_nick'])}</b>\n"
        f"🔑 Пароль: <code>{_e(password) if password else 'не задан'}</code>\n"
        f"📡 Статус на сервере: {online_line}"
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    reg = storage.get_by_telegram(message.from_user.id)
    if not reg or reg["status"] != "approved":
        await message.answer("Профиль появится после одобрения заявки.", reply_markup=main_menu_kb(message.from_user.id))
        return
    await message.answer(await _profile_text(reg), reply_markup=profile_kb(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery) -> None:
    reg = storage.get_by_telegram(callback.from_user.id)
    if reg and reg["status"] == "blocked":
        await _safe_answer(callback, "Аккаунт заблокирован.", show_alert=True)
        return
    if not reg or reg["status"] != "approved":
        await _safe_answer(callback, "Профиль появится после одобрения заявки.", show_alert=True)
        return
    text = await _profile_text(reg)
    try:
        if callback.message.html_text == text:  # «Обновить» без изменений — Telegram ругается на одинаковый текст
            await _safe_answer(callback, "Без изменений")
            return
        await callback.message.edit_text(text, reply_markup=profile_kb(), parse_mode=ParseMode.HTML)
    except Exception:  # noqa: BLE001
        await callback.message.answer(text, reply_markup=profile_kb(), parse_mode=ParseMode.HTML)
    await _safe_answer(callback)


@router.callback_query(F.data == "change_password")
async def cb_change_password(callback: CallbackQuery, state: FSMContext) -> None:
    reg = storage.get_by_telegram(callback.from_user.id)
    if not reg or reg["status"] != "approved":
        await _safe_answer(callback, "Сначала нужно одобрение заявки.", show_alert=True)
        return
    await state.clear()
    await state.set_state(PasswordStates.change_password)
    await callback.message.answer(
        "🔑 Отправь <b>новый пароль</b> (6–32 символа: английские буквы, цифры, знаки, без пробелов).\n"
        "Сообщение с ним я удалю после сохранения.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    await _safe_answer(callback)


# ---------- Блокировка и восстановление ----------

ADMIN_ONLY = F.from_user.id.in_(set(config.ADMIN_IDS))


async def _do_block(bot: Bot, reg, admin) -> str | None:
    """Блокирует аккаунт на 24 ч. Возвращает «до …» (UTC) или None, если блокировать нельзя."""
    if not storage.block(reg["id"], admin.id, hours=24):
        return None
    fresh = storage.get_by_id(reg["id"])
    until = fresh["blocked_until"]
    await sheets.update_row(reg["telegram_id"], {9: "blocked", 12: f"до {until} UTC ({_who(admin)})"})
    try:
        await server.set_blocked(reg["minecraft_nick"], True)
    except Exception as e:  # noqa: BLE001
        log.warning("server.set_blocked failed: %s", e)
    try:
        await bot.send_message(
            reg["telegram_id"],
            "🔒 <b>Твой аккаунт заблокирован администрацией на 24 часа.</b>\n\n"
            "Когда срок закончится, я напишу тебе, и ты сможешь подать заявку на восстановление.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(reg["telegram_id"]),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось уведомить игрока %s о блокировке: %s", reg["telegram_id"], e)
    return until


@router.callback_query(F.data.regexp(r"^adm:(blk|blkyes|blkno):\d+$"))
async def cb_admin_block(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id not in config.ADMIN_IDS:
        await _safe_answer(callback, "Только для администраторов.", show_alert=True)
        return
    _, action, reg_id_s = callback.data.split(":")
    reg = storage.get_by_id(int(reg_id_s))
    if not reg:
        await _safe_answer(callback, "Заявка не найдена.", show_alert=True)
        return
    if action == "blk":      # шаг 1: просим подтвердить, чтобы не заблокировать случайным тапом
        await callback.message.edit_reply_markup(reply_markup=block_confirm_kb(reg["id"]))
        await _safe_answer(callback)
        return
    if action == "blkno":
        await callback.message.edit_reply_markup(reply_markup=block_kb(reg["id"]))
        await _safe_answer(callback)
        return
    until = await _do_block(bot, reg, callback.from_user)
    if until is None:
        await _safe_answer(callback, "Аккаунт сейчас нельзя заблокировать (уже заблокирован?).", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    try:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n<b>🔒 Заблокирован на 24 ч</b> (до {until} UTC) — {_e(_who(callback.from_user))}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить сообщение админа: %s", e)
    await _safe_answer(callback, "Заблокирован")


@router.message(Command("block"), ADMIN_ONLY)
async def cmd_block(message: Message, command: CommandObject, bot: Bot) -> None:
    nick = (command.args or "").strip().lstrip("@")
    reg = storage.get_by_nick(nick) if nick else None
    if not reg:
        await message.answer("Использование: /block <ник Minecraft>\nИгрок с таким ником не найден.")
        return
    until = await _do_block(bot, reg, message.from_user)
    await message.answer(
        f"🔒 {_e(reg['minecraft_nick'])} заблокирован до {until} UTC." if until
        else f"Нельзя заблокировать: статус {_e(reg['status'])} (блокируются только одобренные)."
    )


@router.message(Command("unblock"), ADMIN_ONLY)
async def cmd_unblock(message: Message, command: CommandObject, bot: Bot) -> None:
    nick = (command.args or "").strip().lstrip("@")
    reg = storage.get_by_nick(nick) if nick else None
    if not reg or not storage.unblock(reg["id"]):
        await message.answer("Использование: /unblock <ник Minecraft>\nЗаблокированный игрок с таким ником не найден.")
        return
    await sheets.update_row(reg["telegram_id"], {9: "approved", 12: f"снята вручную ({_who(message.from_user)})"})
    try:
        await server.set_blocked(reg["minecraft_nick"], False)
    except Exception as e:  # noqa: BLE001
        log.warning("server.set_blocked failed: %s", e)
    try:
        await bot.send_message(reg["telegram_id"], "🔓 Блокировка снята администратором.",
                               reply_markup=main_menu_kb(reg["telegram_id"]))
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось уведомить игрока: %s", e)
    await message.answer(f"🔓 {_e(reg['minecraft_nick'])} разблокирован.")


@router.callback_query(F.data == "restore_req")
async def cb_restore_req(callback: CallbackQuery, bot: Bot) -> None:
    uid = callback.from_user.id
    result = storage.request_restore(uid)
    reg = storage.get_by_telegram(uid)
    if result == "not_blocked":
        await _safe_answer(callback, "Твой аккаунт не заблокирован.", show_alert=True)
        return
    if result == "active":
        await _safe_answer(callback, f"Блокировка ещё действует: осталось {_remaining(reg['blocked_until'])}.", show_alert=True)
        return
    if result == "already":
        await _safe_answer(callback, "Обращение уже отправлено. Ждём решения администрации.", show_alert=True)
        return
    text = (
        "📨 <b>Обращение: заявка на восстановление</b>\n\n"
        f"⛏️ Ник: <b>{_e(reg['minecraft_nick'])}</b>\n"
        f"Telegram: {_e('@' + reg['telegram_username'] if reg['telegram_username'] else uid)} (id={uid})\n"
        f"Заблокировал: {_e(reg['blocked_by'])}\n"
        "Срок блокировки (24 ч) истёк."
    )
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML, reply_markup=restore_admin_kb(reg["id"]))
            delivered += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось отправить обращение админу %s: %s", admin_id, e)
    await _safe_answer(callback, "Обращение отправлено!")
    await callback.message.answer(
        "📨 Обращение отправлено администрации. Как только его рассмотрят — я напишу тебе."
        if delivered else "⚠️ Не удалось доставить обращение админам. Попробуй позже.",
    )


@router.callback_query(F.data.regexp(r"^rst:(ok|no):\d+$"))
async def cb_restore_decision(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id not in config.ADMIN_IDS:
        await _safe_answer(callback, "Только для администраторов.", show_alert=True)
        return
    _, action, reg_id_s = callback.data.split(":")
    reg = storage.get_by_id(int(reg_id_s))
    approve = action == "ok"
    if not reg or not storage.decide_restore(reg["id"], approve):
        await _safe_answer(callback, "Обращение уже рассмотрено.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        return
    who = _who(callback.from_user)
    mark = "✅ Доступ восстановлен" if approve else "❌ В восстановлении отказано"
    try:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n<b>{mark}</b> — {_e(who)}",
            parse_mode=ParseMode.HTML, reply_markup=None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить сообщение админа: %s", e)
    await _safe_answer(callback, mark)

    if approve:
        await sheets.update_row(reg["telegram_id"], {9: "approved", 12: f"восстановлен ({who})"})
        try:
            await server.set_blocked(reg["minecraft_nick"], False)
        except Exception as e:  # noqa: BLE001
            log.warning("server.set_blocked failed: %s", e)
    else:
        await sheets.update_row(reg["telegram_id"], {12: f"в восстановлении отказано ({who})"})
    try:
        await bot.send_message(
            reg["telegram_id"],
            "✅ <b>Доступ восстановлен!</b> Профиль снова доступен." if approve
            else "❌ В восстановлении доступа отказано. Ты можешь отправить новое обращение.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(reg["telegram_id"]),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось уведомить игрока %s: %s", reg["telegram_id"], e)


async def _expiry_notifier(bot: Bot) -> None:
    """Раз в минуту: у кого закончилась блокировка — предлагаем подать заявку на восстановление."""
    while True:
        try:
            for reg in await asyncio.to_thread(storage.due_expiry_notifications):
                try:
                    await bot.send_message(
                        reg["telegram_id"],
                        "⏰ <b>Срок блокировки закончился.</b>\n\n"
                        "Чтобы вернуть доступ, подай заявку на восстановление — "
                        "обращение уйдёт администрации.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=restore_kb(),
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("Не удалось отправить уведомление %s: %s", reg["telegram_id"], e)
                await asyncio.to_thread(storage.mark_expiry_notified, reg["id"])
        except Exception:  # noqa: BLE001
            log.exception("expiry notifier error")
        await asyncio.sleep(60)


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    """Любая кнопка, для которой нет состояния (старое сообщение, рестарт, двойной клик)."""
    await _safe_answer(callback, "Сессия устарела. Нажми /start", show_alert=True)


@router.error(ExceptionTypeFilter(Exception))
async def on_error(event: ErrorEvent) -> bool:
    """Глобальный перехват: логируем и вежливо отвечаем, а не оставляем пользователя в тишине."""
    log.exception("Ошибка при обработке апдейта: %s", event.exception)
    update = event.update
    try:
        if update.callback_query:
            await _safe_answer(update.callback_query, "Что-то пошло не так, попробуй ещё раз.", show_alert=True)
        elif update.message:
            await update.message.answer("⚠️ Что-то пошло не так. Попробуй ещё раз или нажми /start.")
    except Exception:  # noqa: BLE001
        log.warning("Не удалось сообщить пользователю об ошибке")
    return True


@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await message.answer("Бот ждёт твой ответ на текущий вопрос 👆\n\nОтмена — /cancel")
    else:
        await message.answer("Нажми /start, чтобы открыть меню.", reply_markup=main_menu_kb(message.from_user.id))


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "bot": "Intermezzo Register"})


# ---------- Bootstrap ----------

async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("❌ Не задан BOT_TOKEN. Создай файл .env (см. .env.example).")
    storage.init_db()
    if config.GOOGLE_SHEETS_ID:
        try:
            await asyncio.to_thread(sheets.ensure_header_sync)
        except Exception as e:  # noqa: BLE001
            log.warning("Sheets init warning: %s", e)

    bot = Bot(token=config.BOT_TOKEN)
    notifier = asyncio.create_task(_expiry_notifier(bot))

    # FSM поверх SQLite — переживает рестарт контейнера (если data/ лежит на volume).
    dp = Dispatcher(storage=SQLiteStorage(storage.DB_PATH.parent / "fsm.db"))
    dp.include_router(router)

    if config.USE_WEBHOOK:
        if not config.WEBHOOK_BASE_URL:
            raise SystemExit("❌ USE_WEBHOOK=1, но не задан WEBHOOK_BASE_URL (публичный URL сервиса).")
        webhook_url = config.WEBHOOK_BASE_URL + config.WEBHOOK_PATH
        await bot.set_webhook(
            url=webhook_url,
            secret_token=config.WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
        log.info("Вебхук установлен: %s", webhook_url)
        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/", health)
        api.register(app)
        # SimpleRequestHandler проверяет секрет, сразу отвечает 200 и обрабатывает апдейт
        # в фоне — ошибки хендлеров не превращаются в 500 и бесконечные ретраи Telegram.
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=config.WEBHOOK_SECRET or None,
        ).register(app, path=config.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.PORT)
        await site.start()
        log.info("Webhook-сервер слушает порт %s", config.PORT)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            notifier.cancel()
            await bot.session.close()
    else:
        log.info("Бот запущен (polling).")
        api_runner = None
        if config.PLUGIN_API_TOKEN:  # в polling-режиме веб-сервер нужен только для API мода
            api_app = web.Application()
            api_app.router.add_get("/health", health)
            api.register(api_app)
            api_runner = web.AppRunner(api_app)
            await api_runner.setup()
            await web.TCPSite(api_runner, "0.0.0.0", config.PORT).start()
            log.info("API для мода слушает порт %s", config.PORT)
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        finally:
            if api_runner:
                await api_runner.cleanup()
            notifier.cancel()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
