# 🚀 Деплой на Railway (бесплатно) — за 5 минут

## Что тебе нужно
- Аккаунт на GitHub
- Аккаунт на Railway (https://railway.app) — вход через GitHub
- Твой Telegram ID уже вписан: 1471598630, 843394746, 8896083018

## Шаг 1. Залить код на GitHub
1. Создай репозиторий: https://github.com/new (Public или Private — неважно)
2. Залей файлы из этой папки `minecraft-registration-bot`, КРОМЕ `.env` (он в .gitignore)
3. Проверь, что файл `.env` НЕ попал на GitHub!

## Шаг 2. Создать проект на Railway
1. Открой https://railway.app → New Project
2. Deploy from GitHub → выбери свой репозиторий
3. Railway сам найдёт `railway.toml` и `requirements.txt`

## Шаг 3. Вписать переменные (Variables)
В Railway: проект → Variables → New Variable, добавь по одному:

```
BOT_TOKEN=9156367348:AAEJjt7P_Qd0CJhPlzTsINn9Xm8q9lAHMQg
ADMIN_IDS=1471598630,843394746,8896083018
MIN_AGE=12
MAX_AGE=99
SERVER_NAME=Minecraft Server
SERVER_IP=mc.example.com
USE_WEBHOOK=1
WEBHOOK_SECRET=intermezzo_secret_2026
```

## Шаг 4. Домен и вебхук
1. В Railway: проект → Settings → Networking → Generate Domain
   Скопируй домен, например: `intermezzo-bot.up.railway.app`
2. Добавь переменную:
   ```
   WEBHOOK_BASE_URL=https://intermezzo-bot.up.railway.app
   ```
   (подставь СВОЙ домен)
3. Redeploy

## Шаг 5. Проверка
1. Открой https://твой-домен.up.railway.app/health → должно быть `{"ok": true}`
2. Напиши боту @Intermezzoregisterbot → `/start`
3. Нажми «📝 Заполнить заявку» и пройди опросник до конца
4. Все 3 админа должны получить заявку в личку

## ⚠️ Важно
- Админы должны сначала написать боту `/start`, иначе бот не сможет им писать!
- Токен светился в чате — после запуска обнови его:
  @BotFather → /mybots → @Intermezzoregisterbot → API Token → Revoke
  и вставь новый токен в Railway Variables.
- База SQLite на Railway имеет эфемерный диск: при передеплое заявки могут сброситься.
  Для продакшна позже можно переехать на Postgres, но для старта — ок.

## Если что-то не работает
- Смотри логи: Railway → Deployments → Latest → View Logs
- Самая частая проблема: забыли `USE_WEBHOOK=1` или `WEBHOOK_BASE_URL` без `https://`
