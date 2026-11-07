import pytest

from validators import (
    validate_age,
    validate_country,
    validate_discord_nick,
    validate_minecraft_nick,
    validate_name,
)


@pytest.mark.parametrize("raw,ok", [
    ("Иван", True),
    ("Mary-Jane", True),
    ("А", False),
    ("123", False),
    ("a" * 51, False),
    ("   ", False),
])
def test_name(raw, ok):
    assert validate_name(raw)[0] is ok


@pytest.mark.parametrize("raw,ok", [
    ("16", True),
    ("12", True),
    ("99", True),
    ("11", False),
    ("100", False),
    ("abc", False),
    ("16.5", False),
    ("", False),
])
def test_age(raw, ok):
    assert validate_age(raw)[0] is ok


@pytest.mark.parametrize("raw,ok", [
    ("Россия", True),
    ("United States", True),
    ("РФ", True),
    ("", False),
    ("a" * 61, False),
])
def test_country(raw, ok):
    assert validate_country(raw)[0] is ok


@pytest.mark.parametrize("raw,expected", [
    ("Steve", "Steve"),
    ("Steve_2026", "Steve_2026"),
    ("@Steve", "Steve"),
    ("ab", None),               # слишком короткий
    ("a" * 17, None),           # слишком длинный
    ("Стив", None),             # кириллица
    ("bad nick", None),         # пробел
    ("bad-nick", None),         # дефис запрещён
])
def test_minecraft(raw, expected):
    ok, val = validate_minecraft_nick(raw)
    assert ok is (expected is not None)
    if ok:
        assert val == expected


@pytest.mark.parametrize("raw,expected", [
    ("@steve_2026", "@steve_2026"),
    ("steve_2026", "@steve_2026"),
    ("steve.2026", "@steve.2026"),
    ("Steve#1234", "Steve#1234"),
    ("", None),
])
def test_discord(raw, expected):
    ok, val = validate_discord_nick(raw)
    assert ok is (expected is not None)
    if ok:
        assert val == expected


from validators import validate_password


@pytest.mark.parametrize("raw,ok", [
    ("Cube!2026x", True),
    ("123456", True),
    ("a" * 32, True),
    ("short", False),
    ("a" * 33, False),
    ("with space1", False),
    ("пароль123", False),
    ("", False),
])
def test_password(raw, ok):
    assert validate_password(raw)[0] is ok
