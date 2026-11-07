"""Валидация полей регистрации. Чистые функции без зависимости от Telegram —
их легко тестировать."""
import re

# Ник Minecraft: только латиница, цифры и подчёркивание, 3-16 символов (официальные правила)
MC_NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
# Discord: новый формат username (2-32, строчные/цифры/._ ) или старый Name#1234
DISCORD_NEW_RE = re.compile(r"^[a-z0-9_.]{2,32}$")
DISCORD_OLD_RE = re.compile(r"^.{2,32}#\d{4}$")


def _clean(value: str) -> str:
    return (value or "").strip()


def validate_name(value: str) -> tuple[bool, str]:
    """Имя: 2-50 символов, буквы, пробелы, дефисы, апострофы."""
    v = _clean(value)
    if not (2 <= len(v) <= 50):
        return False, "Имя должно быть от 2 до 50 символов."
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё Ā-ſ'\-]+", v):
        return False, "Имя может содержать только буквы, пробелы, дефис и апостроф."
    if len(v.split()) > 4:
        return False, "Слишком много слов. Напиши короткое имя."
    return True, v


def validate_age(value: str, min_age: int = 12, max_age: int = 99) -> tuple[bool, str]:
    """Возраст: целое число в диапазоне [min_age, max_age]."""
    v = _clean(value)
    if not re.fullmatch(r"\d{1,3}", v):
        return False, f"Введи возраст числом, например: 16. Допустимо от {min_age} до {max_age}."
    age = int(v)
    if not (min_age <= age <= max_age):
        return False, f"Возраст должен быть от {min_age} до {max_age}. Укажи реальный возраст."
    return True, str(age)


def validate_country(value: str) -> tuple[bool, str]:
    """Страна: 2-60 символов, буквы/пробелы/дефис."""
    v = _clean(value)
    if not (2 <= len(v) <= 60):
        return False, "Страна должна быть от 2 до 60 символов."
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё Ā-ſ'\-]+", v):
        return False, "Страна может содержать только буквы, пробелы и дефис."
    return True, v


def validate_minecraft_nick(value: str) -> tuple[bool, str]:
    """Ник Minecraft: 3-16 символов, латиница/цифры/_ ."""
    v = _clean(value)
    # Часто пишут с @ или лишними пробелами — срезаем @ в начале
    if v.startswith("@"):
        v = v[1:]
    if not MC_NICK_RE.fullmatch(v):
        return False, (
            "Ник Minecraft: 3–16 символов, только английские буквы, цифры и «_». "
            "Без пробелов и кириллицы. Например: Steve_2026"
        )
    return True, v


def validate_discord_nick(value: str) -> tuple[bool, str]:
    """Ник Discord: @username, username или Name#1234."""
    raw = _clean(value)
    v = raw[1:] if raw.startswith("@") else raw
    v_lower = v.lower()
    if DISCORD_NEW_RE.fullmatch(v_lower):
        return True, f"@{v_lower}"
    if DISCORD_OLD_RE.fullmatch(v):
        return True, v
    # Человекочитаемый fallback: если похоже на отображаемое имя — принимаем как есть, но не пустое
    if 2 <= len(v) <= 40 and " " not in v:
        # Разрешаем display name из 1 слова на всякий случай, но предупреждаем форматом
        # Строго: принимаем всё без пробелов до 40 символов
        return True, v
    return False, (
        "Ник Discord: укажи username, например @steve или steve_2026 "
        "(строчные буквы, цифры, точки и «_»). Можно с @ в начале."
    )
