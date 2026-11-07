"""HTTP API для серверного мода Minecraft (intermezzoauth).

Все запросы — с заголовком `Authorization: Bearer <PLUGIN_API_TOKEN>`.

GET  /api/status?nick=Steve
     -> {"status": "none|pending|approved|blocked"}
POST /api/verify  {"nick": "Steve", "password": "..."}
     -> {"ok": true}  или  {"ok": false, "reason": "bad_password|none|pending|blocked|no_password|locked"}
POST /api/online  {"players": [все на сервере], "authenticated": [прошедшие /login]}
     -> {"kick": [{"nick": "...", "reason": "none|pending|blocked"}]}
     Бот запоминает, кто онлайн (для кнопки «Профиль»), и сообщает, кого надо выгнать
     (например, админ только что заблокировал аккаунт).

Пароль в ответах API НЕ отдаётся — он проверяется здесь.
"""
import hmac
import logging
import time

from aiohttp import web

import config
import storage

log = logging.getLogger(__name__)

# --- защита от перебора пароля: 5 ошибок -> блок ника на 5 минут ---
MAX_FAILS = 5
LOCK_SECONDS = 300
_fails: dict[str, tuple[int, float]] = {}

# --- кто онлайн (присылает мод) ---
ONLINE_TTL = 120  # если мод молчит дольше — статус «неизвестен»
_online: dict = {"ts": 0.0, "nicks": set()}


def is_online(nick: str) -> bool | None:
    if time.time() - _online["ts"] > ONLINE_TTL:
        return None
    return nick.lower() in _online["nicks"]


def _status_of(reg) -> str:
    return reg["status"] if reg and reg["status"] in ("pending", "approved", "blocked") else "none"


@web.middleware
async def _auth(request: web.Request, handler):
    token = config.PLUGIN_API_TOKEN
    header = request.headers.get("Authorization", "")
    if not token or not hmac.compare_digest(header.encode(), f"Bearer {token}".encode()):
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        raise web.HTTPBadRequest(text="bad json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="bad json")
    return data


async def status(request: web.Request) -> web.Response:
    nick = request.query.get("nick", "").strip()
    if not nick:
        raise web.HTTPBadRequest(text="nick required")
    return web.json_response({"status": _status_of(storage.get_by_nick(nick))})


async def verify(request: web.Request) -> web.Response:
    data = await _json(request)
    nick = str(data.get("nick", "")).strip()
    password = str(data.get("password", ""))
    if not nick:
        raise web.HTTPBadRequest(text="nick required")
    key = nick.lower()

    count, until = _fails.get(key, (0, 0.0))
    if count >= MAX_FAILS:
        if time.time() < until:
            return web.json_response({"ok": False, "reason": "locked"})
        _fails.pop(key, None)

    reg = storage.get_by_nick(nick)
    st = _status_of(reg)
    if st != "approved":
        return web.json_response({"ok": False, "reason": st})
    stored = reg["password"] or ""
    if not stored:
        return web.json_response({"ok": False, "reason": "no_password"})

    if hmac.compare_digest(password.encode(), stored.encode()):
        _fails.pop(key, None)
        log.info("API: %s вошёл на сервер", nick)
        return web.json_response({"ok": True})

    count = _fails.get(key, (0, 0.0))[0] + 1
    _fails[key] = (count, time.time() + LOCK_SECONDS)
    log.warning("API: неверный пароль для %s (попытка %s)", nick, count)
    return web.json_response({"ok": False, "reason": "bad_password"})


async def online(request: web.Request) -> web.Response:
    data = await _json(request)
    players = [str(x) for x in data.get("players", []) if isinstance(x, str)]
    authed = [str(x) for x in data.get("authenticated", []) if isinstance(x, str)]
    _online["ts"] = time.time()
    _online["nicks"] = {n.lower() for n in authed}

    kick = []
    for nick in players:
        st = _status_of(storage.get_by_nick(nick))
        if st != "approved":
            kick.append({"nick": nick, "reason": st})
    return web.json_response({"kick": kick})


def register(app: web.Application) -> None:
    """Подключает /api/* к aiohttp-приложению (только если задан PLUGIN_API_TOKEN)."""
    if not config.PLUGIN_API_TOKEN:
        log.info("PLUGIN_API_TOKEN не задан — API для мода выключен.")
        return
    sub = web.Application(middlewares=[_auth], client_max_size=64 * 1024)
    sub.router.add_get("/status", status)
    sub.router.add_post("/verify", verify)
    sub.router.add_post("/online", online)
    app.add_subapp("/api", sub)
    log.info("API для мода включён: /api/status, /api/verify, /api/online")
