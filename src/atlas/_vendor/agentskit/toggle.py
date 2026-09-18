"""Движок ВЫКЛЮЧЕНИЯ навыка: пауза вместо переустановки.

До сих пор кит умел только ``install`` и ``remove``. Разница между «снять» и
«выключить» существенная: снятый навык надо ставить заново — с зависимостями,
проверкой, правами, — а выключенный лежит на месте и возвращается одной
строкой. Пока состояния «поставлен, но выключен» нет, любая пауза стоит
переустановки, и поэтому её просто не делают: навык остаётся включённым и
шумит в каталоге агента.

Три правила, общие с движком хуков (``agentskit.hooks``):

1. **Механизм — ДАННЫЕ.** Что за файл, какая секция, как зовутся поля —
   в реестре (``AgentSpec.toggle`` → ``ToggleLayout``). У большинства агентов
   механизма нет вовсе: ``toggle is None`` — это НОРМАЛЬНЫЙ ответ
   (``action="skipped"``), а не ошибка и не повод угадывать формат чужого
   конфига.
2. **Не трогаем чужое.** В файле настроек живут секции других инструментов и
   записи других навыков. Правится РОВНО одна запись — та, чей путь совпал;
   остальное переносится дословно, включая комментарии.
3. **Запись атомарна.** Прямая запись усекает боевой конфиг ещё до того, как
   в него что-то попало: оборвись процесс на середине — у владельца остаётся
   обрубок, которым агент уже не поднимется.

Формат ``codex-config-toml`` — единственный известный (улика:
``020_build-skills.md``, «Use ``[[skills.config]]`` entries in
``~/.codex/config.toml`` to disable a skill without deleting it»). Незнакомый
формат — отказ, а не правка наугад.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .hooks import SettingsIO, _spec_of
from .spec import AgentSpec, ToggleLayout

KNOWN_FORMATS = ("codex-config-toml",)


@dataclass(frozen=True)
class ToggleResult:
    """Что произошло — в тех же словах, что у остальных движков кита."""

    agent: str
    action: str  # "enabled" | "disabled" | "unchanged" | "skipped" | "failed"
    reason: str = ""
    path: str | None = None
    skill: str | None = None

    @property
    def ok(self) -> bool:
        return self.action in ("enabled", "disabled", "unchanged", "skipped")


def toggle_layout_for(agent: str | AgentSpec) -> ToggleLayout | None:
    """Умеет ли агент выключать навык. ``None`` — не умеет, и это ответ."""
    spec = _spec_of(agent)
    return spec.toggle


def settings_path(
    agent: str | AgentSpec, *, home: Path, layout: ToggleLayout | None = None
) -> Path | None:
    """Где лежит файл состояния навыков. ``None`` — агент не умеет выключать."""
    spec = _spec_of(agent)
    layout = layout or spec.toggle
    if layout is None:
        return None
    return home.joinpath(spec.config_dir, *layout.settings_file)


#: Запись секции вида ``[[skills.config]]`` вместе с её телом до следующей секции.
def _entries(text: str, section: str) -> list[tuple[int, int, str]]:
    """Найти записи секции: ``(начало, конец, тело)``.

    Разбираем текстом, а не через сериализатор TOML, по той же причине, что и
    у хуков: обратная запись через библиотеку теряет комментарии и порядок,
    то есть возвращает владельцу ДРУГОЙ файл. Здесь меняется одно поле в одной
    записи, всё остальное обязано остаться байт в байт.
    """
    header = re.compile(rf"^\[\[{re.escape(section)}\]\]\s*$", re.M)
    any_header = re.compile(r"^\[\[?[^\]]+\]\]?\s*$", re.M)
    out: list[tuple[int, int, str]] = []
    for m in header.finditer(text):
        start = m.start()
        nxt = any_header.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        out.append((start, end, text[start:end]))
    return out


def _field(body: str, key: str) -> str | None:
    m = re.search(rf'^[ \t]*{re.escape(key)}[ \t]*=[ \t]*"([^"]*)"[ \t]*$', body, re.M)
    return m.group(1) if m else None


def _set_enabled(body: str, key: str, value: bool) -> str:
    """Проставить булево поле, сохранив остальное тело записи дословно."""
    literal = "true" if value else "false"
    pattern = re.compile(
        rf"^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)(true|false)[ \t]*$", re.M
    )
    if pattern.search(body):
        return pattern.sub(rf"\g<1>{literal}", body, count=1)
    # поля не было — дописываем в конец записи, перед пустым хвостом
    return body.rstrip() + f"\n{key} = {literal}\n"


def set_skill_enabled(
    agent: str | AgentSpec,
    skill_path: str | Path,
    *,
    enabled: bool,
    home: Path,
    io: SettingsIO | None = None,
) -> ToggleResult:
    """Включить или выключить навык, не удаляя его.

    ``skill_path`` — путь, которым навык записан у агента. Сверяется как есть:
    подставлять свою нормализацию нельзя, иначе кит «не найдёт» запись, которую
    человек завёл руками, и молча заведёт вторую.
    """
    spec = _spec_of(agent)
    layout = spec.toggle
    if layout is None:
        return ToggleResult(spec.key, "skipped", "агент не умеет выключать навыки")
    if layout.format not in KNOWN_FORMATS:
        return ToggleResult(
            spec.key, "failed",
            f"незнакомый формат настроек {layout.format!r} — правка наугад опаснее отказа",
        )

    io = io or SettingsIO()
    path = settings_path(spec, home=home, layout=layout)
    assert path is not None
    wanted = str(skill_path)

    text = io.read(path) if io.exists(path) else ""
    found = None
    for start, end, body in _entries(text, layout.section):
        if _field(body, layout.path_key) == wanted:
            found = (start, end, body)
            break

    if found is None:
        # Записи нет — навык включён по умолчанию. Выключить = завести запись;
        # включить = не делать ничего, иначе файл растёт пустыми утверждениями.
        if enabled:
            return ToggleResult(spec.key, "unchanged", "навык и так включён",
                                str(path), wanted)
        block = (
            f"\n[[{layout.section}]]\n"
            f'{layout.path_key} = "{wanted}"\n'
            f"{layout.enabled_key} = false\n"
        )
        io.write(path, (text.rstrip() + "\n" if text.strip() else "") + block)
        return ToggleResult(spec.key, "disabled", "запись заведена", str(path), wanted)

    start, end, body = found
    already = _field_bool(body, layout.enabled_key)
    if already is enabled:
        return ToggleResult(spec.key, "unchanged", "состояние уже такое",
                            str(path), wanted)
    io.write(path, text[:start] + _set_enabled(body, layout.enabled_key, enabled) + text[end:])
    return ToggleResult(spec.key, "enabled" if enabled else "disabled",
                        "состояние изменено", str(path), wanted)


def _field_bool(body: str, key: str) -> bool | None:
    m = re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(true|false)[ \t]*$", body, re.M)
    if m is None:
        return None  # поля нет — по умолчанию включён
    return m.group(1) == "true"


def is_skill_enabled(
    agent: str | AgentSpec,
    skill_path: str | Path,
    *,
    home: Path,
    io: SettingsIO | None = None,
) -> bool:
    """Включён ли навык. Нет записи или нет поля — значит включён.

    Умолчание именно такое: агент читает навык, пока ему явно не запретили.
    Считать «нет записи» выключением значило бы объявить выключенными все
    навыки, которых человек не трогал.
    """
    spec = _spec_of(agent)
    layout = spec.toggle
    if layout is None:
        return True
    io = io or SettingsIO()
    path = settings_path(spec, home=home, layout=layout)
    if path is None or not io.exists(path):
        return True
    text = io.read(path)
    for _, _, body in _entries(text, layout.section):
        if _field(body, layout.path_key) == str(skill_path):
            value = _field_bool(body, layout.enabled_key)
            return True if value is None else value
    return True


def list_disabled(
    agent: str | AgentSpec, *, home: Path, io: SettingsIO | None = None
) -> list[str]:
    """Пути навыков, выключенных у агента. Для отчёта «что стоит, но молчит»."""
    spec = _spec_of(agent)
    layout = spec.toggle
    if layout is None:
        return []
    io = io or SettingsIO()
    path = settings_path(spec, home=home, layout=layout)
    if path is None or not io.exists(path):
        return []
    out: list[str] = []
    for _, _, body in _entries(io.read(path), layout.section):
        if _field_bool(body, layout.enabled_key) is False:
            value = _field(body, layout.path_key)
            if value:
                out.append(value)
    return out


__all__: list[str] = [
    "ToggleResult",
    "is_skill_enabled",
    "list_disabled",
    "set_skill_enabled",
    "settings_path",
    "toggle_layout_for",
]
