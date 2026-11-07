"""Telegram-бот для регистрации на Minecraft-сервер.

Поток: /start -> кнопка "Заполнить заявку" -> опросник из 5 вопросов ->
проверка данных -> превью -> подтверждение -> сохранение + уведомление админа.

ВАЖНО: FSM storage — SQLite на диске (Railway даёт volume). В memory всё слетает
при каждом redeploy/краше, и кнопка подтверждения уходит в "Сессия устарела".
"""
import asyncio
import html
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, ExceptionTypeFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

import config
import sheets
import storage
from fsm_storage import SQLiteStorage
from validators import (
    validate_age,
    validate_country,
    validate_discord_nick,
    validate_minecraft_nick,
    validate_name,
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


def main_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Заполнить заявку", callback_data="start_reg")
    b.button(text="ℹ️ О сервере", callback_data="about_server")
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
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    was = await state.get_state()
    await state.clear()
    if was:
        await message.answer("❌ Заявка отменена.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Меню:", reply_markup=main_menu_kb())


@router.message(F.text.in_({"❌ Отмена", "Отмена", "cancel"}))
async def text_cancel(message: Message, state: FSMContext) -> None:
    was = await state.get_state()
    await state.clear()
    if was:
        await message.answer("❌ Заявка отменена.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Меню:", reply_markup=main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Команды:\n/start — главное меню\n/cancel — отменить заполнение\n/help — эта справка\n\n"
        "Вопросы по заполнению — просто напиши, и бот подскажет.",
        reply_markup=main_menu_kb(),
    )


@router.message(F.text == "ℹ️ О сервере")
async def text_about(message: Message) -> None:
    await message.answer(
        f"<b>{config.SERVER_NAME}</b>\nIP: <code>{config.SERVER_IP}</code>\n\n"
        "Заполни заявку — и администрация рассмотрит её. После одобрения ты получишь доступ.",
        reply_markup=main_menu_kb(),
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
        reply_markup=main_menu_kb(),
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
    await callback.message.answer("❌ Заявка отменена.", reply_markup=main_menu_kb())
    await _safe_answer(callback)


async def _begin_registration(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RegStates.name)
    await state.update_data(step_index=0, data={})
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

    fsm_data = await state.get_data()
    data: dict = fsm_data.get("data", {})
    data[field] = result
    await state.update_data(data=data)
    log.info("FSM save: %s (%s)", field, list(data.keys()))

    next_index = step_index + 1
    if next_index < len(STEPS):
        await _ask_step(message, state, next_index)
    else:
        await state.set_state(RegStates.confirm)
        await state.update_data(data=data)
        log.info("FSM confirm: %s", list(data.keys()))
        await message.answer(
            format_summary(data),
            reply_markup=confirm_kb(),
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == "confirm_reg", StateFilter(RegStates.confirm))
async def cb_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    fsm_data = await state.get_data()
    data: dict = fsm_data.get("data", {})
    log.info("FSM confirm data: %s", list(data.keys()))

    if storage.check_duplicate_telegram(callback.from_user.id):
        await callback.message.answer(
            "⚠️ У тебя уже есть заявка. Если хочешь подать ещё одну — напиши админу.",
            reply_markup=main_menu_kb(),
        )
        await state.clear()
        await _safe_answer(callback)
        return
    if storage.check_duplicate_minecraft_nick(data.get("minecraft_nick", "")):
        await callback.message.answer(
            "⚠️ Этот ник Minecraft уже занят. Заполни заявку заново с другим ником.",
            reply_markup=main_menu_kb(),
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
            reply_markup=main_menu_kb(),
        )
        return

    ok, msg = storage.save_application(
        telegram_id=callback.from_user.id,
        telegram_username=callback.from_user.username,
        data=data,
    )
    if not ok:
        await callback.message.answer(f"❌ Не удалось сохранить: {msg}", reply_markup=main_menu_kb())
        await _safe_answer(callback)
        return

    await state.clear()
    await _safe_answer(callback, "Заявка отправлена!")
    await callback.message.answer(
        "🎉 <b>Заявка отправлена!</b>\n\n"
        f"{format_summary(data)}\n\n"
        "Администрация проверит её и, если всё ок, выдаст доступ.\n"
        "Обычно это занимает от пары часов до суток. Спасибо за терпение!",
        reply_markup=main_menu_kb(),
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
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001 — не падаем, если админ заблокировал бота
            log.warning("Не удалось отправить админу %s: %s", admin_id, e)


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
        await message.answer("Нажми /start, чтобы открыть меню.", reply_markup=main_menu_kb())


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
            await bot.session.close()
    else:
        log.info("Бот запущен (polling).")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        finally:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
