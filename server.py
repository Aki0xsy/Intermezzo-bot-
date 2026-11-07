"""Связь с Minecraft-сервером. Плагин регистрации пока не написан — это точка подключения.

is_online(nick) -> True / False / None (None = статус неизвестен).
Когда будет плагин, достаточно реализовать здесь запрос к его API
(например HTTP: GET {PLUGIN_API_URL}/online/<nick>) — остальной бот менять не придётся.
"""
import logging

log = logging.getLogger(__name__)


async def is_online(nick: str) -> bool | None:
    return None


async def push_password(nick: str, password: str) -> bool:
    """Передать пароль плагину (когда он появится). Сейчас ничего не делает."""
    return False


async def set_blocked(nick: str, blocked: bool) -> bool:
    """Заблокировать/разблокировать вход игрока на сервере (когда появится плагин)."""
    return False
