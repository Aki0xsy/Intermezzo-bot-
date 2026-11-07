# 🤖 Minecraft Registration Bot

Telegram-бот для регистрации новых участников на Minecraft-сервер.
Человек проходит текстовый опросник: **имя → возраст → страна → ник Minecraft → ник Discord**,
подтверждает данные кнопкой, заявка сохраняется в базу и уходит администратору.

## ✨ Что умеет

- Пошаговый опросник с подсказками и отменой в любой момент (`/cancel`)
- Валидация каждого поля с понятными ошибками (невалидный ник/возраст не пройдёт)
- Проверка дублей: один Telegram = одна заявка, ник Minecraft уникален
- Экран подтверждения перед отправкой
- Сохранение в SQLite (`data/registrations.db`) + JSON-слепок заявки
- Уведомление всех админов в Telegram о новой заявке
- Кнопки и reply-клавиатура, меню «О сервере» (IP/название из `.env`)

## 🚀 Запуск

```bash
pip install -r requirements.txt
cp .env.example .env   # на Windows: copy .env.example .env
# впиши в .env: BOT_TOKEN, ADMIN_IDS
python bot.py
```

> Токен: создай бота через [@BotFather](https://t.me/BotFather) → `/newbot`.
> Свой ID для `ADMIN_IDS`: напиши [@userinfobot](https://t.me/userinfobot) или [@getmyid_bot](https://t.me/getmyid_bot).

## ⚙️ Настройки (`.env`)

| Переменная | Что это |
|---|---|
| `BOT_TOKEN` | Токен бота от BotFather |
| `ADMIN_IDS` | ID админов через запятую (получают заявки) |
| `MIN_AGE` / `MAX_AGE` | Допустимый возраст (по умолчанию 12–99) |
| `SERVER_NAME` / `SERVER_IP` | Название и IP сервера для меню |

## 🧪 Тесты

```bash
python -m pytest test_validators.py -v
```

## 🗂 Структура

```
bot.py          # вся логика: FSM, клавиатуры, обработчики
validators.py   # валидация полей (чистые функции, легко тестировать)
storage.py      # SQLite-хранилище заявок
config.py       # настройки из .env
.env.example    # шаблон настроек
test_validators.py
```

## 🔒 Правила валидации

- **Имя** — 2–50 символов, буквы/пробелы/дефис/апостроф
- **Возраст** — число от `MIN_AGE` до `MAX_AGE`
- **Страна** — 2–60 символов, буквы/пробелы/дефис
- **Minecraft** — 3–16 символов: `A–Z a–z 0–9 _` (официальные правила Mojang)
- **Discord** — `@username` / `username` (`a–z 0–9 . _`) или `Name#1234`
