"""Движок ХУКОВ агентов: регистрация команды плагина в настройках агента.

Второй механизм автономного онбординга рядом с managed-блоками (``inject``).
Managed-блок говорит агенту, КАК себя вести; хук заставляет агента САМОГО позвать
команду плагина на своё событие (``SessionStart`` у Claude Code — впрыснуть
контекст в начало сессии).

Три вещи, ради которых это обобщено из ``atlas setup`` (2026-07-21):

1. **Хук — ДАННЫЕ, а не код.** «Какой агент» описан в реестре
   (``AgentSpec.hooks`` → ``HookLayout``: файл настроек, формат, известные
   события, env-override корня), «какое событие + какая команда» — в
   ``HookSpec`` от плагина. У большинства агентов механизма хуков нет вовсе:
   ``AgentSpec.hooks is None`` — и это НОРМАЛЬНЫЙ ответ (``action="skipped"``,
   ``reason="агент не поддерживает хуки"``), а не ошибка и не повод угадывать.
2. **Namespace, как у managed-блоков.** Свой хук узнаётся по namespace-примете
   в команде (``markers``), поэтому в одном ``settings.json`` мирно живут хуки
   нескольких плагинов, и установка НЕ ТРОГАЕТ чужие хуки, чужие группы и
   чужие события. Это главное свойство движка — оно под тестами.
3. **Внешние эффекты инъектируются.** Путь настроек резолвится из ``home``/
   ``cwd``/``env`` (аргументы, не глобальное состояние), чтение/запись — через
   ``SettingsIO``. Тесты идут по временным каталогам и НИКОГДА не касаются
   боевого ``~/.claude/settings.json``.

Выстраданное из atlas (#877), сохранено: команда хука — ВСТРОЕННАЯ команда CLI
плагина (``atlas session-hook``), а не файл-скрипт, иначе она зависит от
``python``/``python3`` в PATH, которого uv-tool не кладёт; и снятие/переустановка
обязаны узнавать СТАРЫЕ регистрации (legacy-маркеры), иначе на диске копятся
дубли хука.

Ядро — чистый stdlib (как весь кит): своя ``HookError``, без clikit; перевод в
``CliError`` делает CLI.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .spec import AgentSpec, HookLayout, get_agent_spec

#: Единственный поддерживаемый формат: ``settings.json`` Claude Code —
#: ``{"hooks": {"<Событие>": [{"matcher": "…", "hooks": [{"type": "command", …}]}]}}``.
CLAUDE_SETTINGS_FORMAT = "claude-settings-json"
#: Корневой ключ секции хуков по умолчанию (у агента он переопределяется данными
#: реестра — ``HookLayout.root_key``).
DEFAULT_ROOT_KEY = "hooks"


def _word_in(word: str, text: str) -> bool:
    """Встречается ли ``word`` в ``text`` ЦЕЛЫМ словом (границы — не ``\\w``).

    ``atlas`` находится в ``atlas session-hook`` и в ``C:/bin/atlas.exe run``
    (``/`` и ``.`` — не буквы), но НЕ в ``atlassian-sync``/``myatlas``/
    ``atlas_backup``. Ровно эта разница отделяет наш хук от чужого.
    """
    if not word:
        return False
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


class HookError(RuntimeError):
    """Ошибка движка хуков (нет поддержки, битые настройки, неизвестное событие).

    Своя, а не ``clikit.CliError``: ядро кита обязано оставаться чистым stdlib
    (``import agentskit`` не тянет зависимостей). У неё те же поля ``code``/
    ``message``, что у ``CliError``, — CLI перекладывает один в один.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── что ставим: заявка плагина ───────────────────────────────────────────────

@dataclass(frozen=True)
class HookSpec:
    """Хук плагина как ДАННЫЕ: чей, на какое событие, какую команду звать.

    ``namespace`` играет ту же роль, что у managed-блока: он делает хук СВОИМ и
    отличимым от чужих. По умолчанию приметой владения служит сам namespace,
    найденный в строке команды (``atlas session-hook`` ← namespace ``atlas``),
    поэтому команда обязана его содержать; если это не так (или нужно узнать
    ПРЕЖНИЕ команды при миграции) — приметы задаются явно в ``markers``.

    ``markers`` заменяют namespace-примету целиком: чем уже примета, тем меньше
    шанс схватить чужой хук. Точное совпадение команды считается «нашим» всегда —
    иначе смена пути к exe (``shutil.which``) плодила бы дубли.

    Находка ревью 2026-07-21: namespace-примета по умолчанию сравнивается по
    ЦЕЛОМУ СЛОВУ, а не подстрокой. Голая подстрока ``"atlas" in command``
    объявляла нашими и удаляла ЧУЖИЕ хуки ``atlassian-sync``, ``myatlas-tool``,
    ``atlas_backup.sh`` — то есть ровно то, что движок обязан не трогать.
    Явные ``markers`` остаются подстрокой осознанно: их пишет плагин про самого
    себя, и в них попадают куски путей (``session_atlas.py``), которые целым
    словом не выражаются.
    """

    namespace: str
    command: str
    event: str = "SessionStart"
    #: matcher группы (у Claude Code — фильтр вида ``startup|resume``).
    #: ``None`` — группа без matcher.
    matcher: str | None = None
    timeout: int | None = None
    status_message: str | None = None
    #: Приметы владения (подстроки команды). Пусто → ``(namespace,)``.
    markers: tuple[str, ...] = ()

    @classmethod
    def from_manifest(cls, data: Mapping[str, Any], *, namespace: str | None = None) -> HookSpec:
        """Заявка из МАНИФЕСТА инструмента (``[[hooks]]`` в toml/json навыка).

        Ключи манифеста: ``event``/``command``/``matcher``/``timeout``/
        ``status_message``/``marker``. Нужна, чтобы объявление хука жило рядом с
        объявлением самого навыка (как ``[[cli]]``/``[[mcp]]``) и не требовало
        писать питон. ``namespace`` по умолчанию = примета (``marker``) или сама
        команда: у манифеста своего namespace нет, а идентичность нужна.
        """
        def _s(key: str) -> str:
            return str(data.get(key) or "").strip()

        command, marker = _s("command"), _s("marker")
        timeout = data.get("timeout")
        return cls(
            namespace=(namespace or marker or command or "hook"),
            command=command,
            event=_s("event") or "SessionStart",
            matcher=_s("matcher") or None,
            timeout=timeout if isinstance(timeout, int)
            and not isinstance(timeout, bool) and timeout > 0 else None,
            status_message=_s("status_message") or None,
            markers=(marker,) if marker else ((command,) if command else ()),
        )

    def __post_init__(self) -> None:
        if not (self.namespace or "").strip():
            raise HookError("bad_hook", "HookSpec: пустой namespace.")
        if not (self.event or "").strip():
            raise HookError("bad_hook", "HookSpec: пустое событие.")

    def identity_markers(self) -> tuple[str, ...]:
        """Приметы, по которым регистрация считается НАШЕЙ."""
        return self.markers or (self.namespace,)

    def owns(self, command: Any) -> bool:
        """Наш ли это хук (точная команда, явная примета-подстрока или namespace-слово).

        Порядок ровно такой: точное совпадение (переезд exe не плодит дубли) →
        явные ``markers`` подстрокой (миграция со старых команд) → namespace
        целым словом (см. докстринг класса: чужое ``atlassian-sync`` нашим не
        считается). Если плагин зовёт свой exe под ИМЕНЕМ, где namespace слит с
        другими буквами (``atlas-cli``), пусть передаст ``markers`` явно.
        """
        if not isinstance(command, str):
            return False
        if self.command.strip() and command.strip() == self.command.strip():
            return True
        if self.markers:
            return any(m and m in command for m in self.markers)
        return _word_in(self.namespace, command)

    def entry(self) -> dict[str, Any]:
        """Одна регистрация хука в формате агента (``{"type": "command", …}``)."""
        e: dict[str, Any] = {"type": "command", "command": self.command}
        if self.timeout is not None:
            e["timeout"] = self.timeout
        if self.status_message:
            e["statusMessage"] = self.status_message
        return e

    def group(self) -> dict[str, Any]:
        """Группа хуков события (``matcher`` + список регистраций)."""
        g: dict[str, Any] = {}
        if self.matcher is not None:
            g["matcher"] = self.matcher
        g["hooks"] = [self.entry()]
        return g


# ── внешние эффекты одной инъектируемой точкой ───────────────────────────────

def _default_read(path: Path) -> str:
    # utf-8-sig снимает BOM (Notepad на Windows его пишет) — иначе json падает
    # на файле, который для человека выглядит совершенно нормальным.
    return path.read_text(encoding="utf-8-sig")


def _default_write(path: Path, text: str) -> None:
    """Запись АТОМАРНО: сначала во временный файл рядом, потом ``os.replace``.

    Находка ревью 2026-07-21: прямой ``write_text`` открывает боевой
    ``settings.json`` на ``w`` — то есть УСЕКАЕТ его ещё до записи. Оборвись
    процесс (или кончись место) на середине — у владельца остаётся обрубок
    конфига, которым Claude Code уже не поднимется. Подмена целиком либо не
    происходит вовсе, либо происходит одним шагом.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".agentskit-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # не оставляем мусор рядом с чужим конфигом
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _default_exists(path: Path) -> bool:
    return path.exists()


@dataclass(frozen=True)
class SettingsIO:
    """Чтение/запись/наличие файла настроек — ЕДИНСТВЕННАЯ дверь наружу.

    Подменяется в тестах (и потребителями со своим хранилищем), чтобы движок
    можно было гонять, не имея настоящего ``~/.claude`` под руками.
    """

    read: Callable[[Path], str] = _default_read
    write: Callable[[Path, str], None] = _default_write
    exists: Callable[[Path], bool] = _default_exists


DEFAULT_IO = SettingsIO()


# ── результат ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HookResult:
    """Итог применения к одному агенту (словарь глаголов — как у ``ApplyResult``)."""

    agent_key: str
    event: str
    #: installed | updated | unchanged | removed | skipped | would-<глагол>
    action: str
    path: str | None = None
    reason: str = ""
    #: dry-run: текст, который БЫЛ БЫ записан (ничего не пишем).
    preview: str | None = None


@dataclass(frozen=True)
class HookStatus:
    """Что сейчас стоит у агента (ничего не меняя)."""

    agent_key: str
    event: str
    supported: bool
    path: str | None = None
    settings_exist: bool = False
    installed: bool = False
    #: Наши команды, найденные в настройках (обычно одна).
    commands: tuple[str, ...] = ()
    #: Сколько ЧУЖИХ регистраций на этом же событии — их мы не трогаем.
    foreign: int = 0
    reason: str = ""


# ── реестр: поддерживает ли агент хуки и где его настройки ───────────────────

def _spec_of(agent: str | AgentSpec) -> AgentSpec:
    if isinstance(agent, AgentSpec):
        return agent
    spec = get_agent_spec(agent)
    if spec is None:
        raise HookError("unknown_agent", f"Неизвестный агент '{agent}'.")
    return spec


def hook_layout_for(agent: str | AgentSpec) -> HookLayout | None:
    """Механизм хуков агента из реестра (``None`` — у агента хуков нет)."""
    return _spec_of(agent).hooks


def supports_hooks(agent: str | AgentSpec) -> bool:
    """Есть ли у агента механизм хуков вообще (реестр обязан уметь ответить «нет»)."""
    return hook_layout_for(agent) is not None


def agents_with_hooks() -> list[str]:
    """Ключи агентов, у которых механизм хуков описан в реестре."""
    from .spec import list_agents

    return [s.key for s in list_agents() if s.hooks is not None]


def hook_settings_path(
    agent: str | AgentSpec,
    *,
    scope: str = "global",
    home: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Файл настроек агента под скоуп (``~/.claude/settings.json`` и т.п.).

    Корень: ``env[config_dir_env]`` (если задан — он и есть config-папка
    целиком), иначе ``<home|cwd>/<config_dir>``. ``home``/``cwd``/``env``
    приходят аргументами — тесты подставляют временные каталоги.
    """
    spec = _spec_of(agent)
    layout = _require_layout(spec)
    if scope not in layout.scopes:
        raise HookError(
            "hook_scope_unsupported",
            f"У агента '{spec.key}' нет настроек в скоупе '{scope}' "
            f"(доступно: {', '.join(layout.scopes)}).",
        )
    environ = os.environ if env is None else env
    override = layout.config_dir_env and environ.get(layout.config_dir_env)
    if scope == "global":
        root = Path(override) if override else _home(home) / spec.config_dir
    else:
        root = (Path(cwd) if cwd is not None else Path.cwd()) / spec.config_dir
    return root.joinpath(*layout.settings_file)


def _home(home: Path | None) -> Path:
    return Path(home) if home is not None else Path.home()


def _require_layout(spec: AgentSpec) -> HookLayout:
    layout = spec.hooks
    if layout is None:
        raise HookError(
            "hooks_unsupported",
            f"У агента '{spec.key}' нет механизма хуков (в реестре нет поля hooks).",
        )
    if layout.format != CLAUDE_SETTINGS_FORMAT:
        raise HookError(
            "hook_format_unknown",
            f"Неизвестный формат хуков '{layout.format}' у агента '{spec.key}'.",
        )
    return layout


def _check_event(spec: AgentSpec, layout: HookLayout, event: str) -> None:
    """Опечатку в имени события иначе видно только по молча не сработавшему хуку."""
    if layout.events and event not in layout.events:
        raise HookError(
            "hook_event_unknown",
            f"Агент '{spec.key}' не знает события '{event}'. "
            f"Известные: {', '.join(layout.events)}.",
        )


# ── чистый мёрж настроек (без I/O — сердце идемпотентности) ──────────────────

def _as_settings(data: Any, where: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HookError("settings_broken", f"Настройки не JSON-объект: {where}")
    return data


def _coerce(hook: HookSpec | Mapping[str, Any]) -> HookSpec:
    """Заявка может прийти и словарём манифеста — приводим к ``HookSpec``."""
    return hook if isinstance(hook, HookSpec) else HookSpec.from_manifest(hook)


def _event_groups(
    settings: dict[str, Any], event: str, *, create: bool, key: str = DEFAULT_ROOT_KEY
) -> list[Any] | None:
    """Список групп события. ``create=False`` и ключа нет → ``None`` (не создаём)."""
    hooks = settings.get(key)
    if hooks is None:
        if not create:
            return None
        hooks = settings.setdefault(key, {})
    if not isinstance(hooks, dict):
        raise HookError("settings_broken", f"settings: '{key}' не объект.")
    groups = hooks.get(event)
    if groups is None:
        if not create:
            return None
        groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise HookError("settings_broken", f"settings: '{key}.{event}' не список.")
    return groups


def _command_of(entry: Any) -> Any:
    return entry.get("command") if isinstance(entry, dict) else None


def _entries_of(group: Any) -> list[Any]:
    """Регистрации внутри группы — ТОЛЬКО если это список, иначе «их нет».

    Находка ревью 2026-07-21: без проверки типа ``for e in group["hooks"]`` падал
    ``TypeError`` на группе вида ``{"hooks": 5}`` — то есть кит валился трейсбеком
    на чужом мусоре в конфиге вместо того, чтобы его не заметить.
    """
    inner = group.get("hooks") if isinstance(group, dict) else None
    return inner if isinstance(inner, list) else []


#: «Группу целиком уносим» — отдельная метка, чтобы отличать её от группы,
#: которая САМА равна ``None`` (чужой мусор ``[null]`` обязан выжить дословно).
_DROP = object()


def _strip_ours(group: Any, hook: HookSpec) -> tuple[Any, bool]:
    """Убрать НАШИ регистрации из группы. → (группа или ``_DROP``, менялось ли).

    Чужие группы возвращаются тем же объектом побайтово: всё, что мы не поняли
    (не dict, без списка ``hooks``), не трогается вообще.
    """
    if not isinstance(group, dict):
        return group, False
    inner = group.get("hooks")
    if not isinstance(inner, list):
        return group, False
    kept = [h for h in inner if not hook.owns(_command_of(h))]
    if len(kept) == len(inner):
        return group, False
    if not kept:
        # группа держала ТОЛЬКО наш хук → уносим её целиком, чтобы не копить
        # пустые группы (они бы накапливались с каждой переустановкой).
        return _DROP, True
    new_group = dict(group)
    new_group["hooks"] = kept
    return new_group, True


def merge_hook(
    settings: dict[str, Any],
    hook: HookSpec | Mapping[str, Any],
    *,
    key: str = DEFAULT_ROOT_KEY,
) -> tuple[dict[str, Any], bool]:
    """Вписать ровно ОДИН наш хук в настройки. Чужое не трогается.

    Идемпотентно и стабильно по позиции: если наша группа уже стоит ровно в
    нужном виде — она остаётся на своём месте, а ``changed=False``. Все прочие
    наши регистрации (дубли, старые команды по ``markers``) снимаются — иначе
    агент звал бы хук дважды.

    Чистая функция над словарём (никакого I/O) — её же зовут потребители со
    своим слоем записи. ``key`` — корневой ключ секции хуков у агента.
    """
    hook = _coerce(hook)
    settings = _as_settings(settings, "merge")
    groups = _event_groups(settings, hook.event, create=True, key=key)
    assert groups is not None  # create=True
    desired = hook.group()

    new_groups: list[Any] = []
    placed = False
    changed = False
    for group in groups:
        if not placed and group == desired:
            new_groups.append(group)   # уже стоит как надо — не двигаем
            placed = True
            continue
        kept, touched = _strip_ours(group, hook)
        changed = changed or touched
        if kept is not _DROP:
            new_groups.append(kept)
    if not placed:
        new_groups.append(desired)
        changed = True
    if changed:
        settings[key][hook.event] = new_groups
    return settings, changed


def remove_hook(
    settings: dict[str, Any],
    hook: HookSpec | Mapping[str, Any],
    *,
    key: str = DEFAULT_ROOT_KEY,
) -> tuple[dict[str, Any], bool]:
    """Снять ТОЛЬКО наши регистрации. Файл остаётся валидным, чужое — на месте.

    Опустевшие после уборки ключи (событие, а следом и сам ``key``) удаляются:
    это наш же мусор, а не чужие настройки.
    """
    hook = _coerce(hook)
    settings = _as_settings(settings, "remove")
    groups = _event_groups(settings, hook.event, create=False, key=key)
    if groups is None:
        return settings, False
    new_groups: list[Any] = []
    changed = False
    for group in groups:
        kept, touched = _strip_ours(group, hook)
        changed = changed or touched
        if kept is not _DROP:
            new_groups.append(kept)
    if not changed:
        return settings, False
    hooks = settings[key]
    if new_groups:
        hooks[hook.event] = new_groups
    else:
        hooks.pop(hook.event, None)
        if not hooks:
            settings.pop(key, None)
    return settings, True


def find_hook_entries(
    settings: Any, hook: HookSpec | Mapping[str, Any], *, key: str = DEFAULT_ROOT_KEY
) -> list[dict[str, Any]]:
    """Наши регистрации, уже стоящие в настройках (только чтение)."""
    if not isinstance(settings, dict):
        return []
    hook = _coerce(hook)
    groups = _event_groups(settings, hook.event, create=False, key=key) or []
    out: list[dict[str, Any]] = []
    for group in groups:
        for entry in _entries_of(group):
            if isinstance(entry, dict) and hook.owns(entry.get("command")):
                out.append(entry)
    return out


def count_foreign_entries(
    settings: Any, hook: HookSpec | Mapping[str, Any], *, key: str = DEFAULT_ROOT_KEY
) -> int:
    """Сколько ЧУЖИХ регистраций на том же событии (их мы обязаны сохранить)."""
    if not isinstance(settings, dict):
        return 0
    hook = _coerce(hook)
    groups = _event_groups(settings, hook.event, create=False, key=key) or []
    n = 0
    for group in groups:
        for entry in _entries_of(group):
            if not (isinstance(entry, dict) and hook.owns(entry.get("command"))):
                n += 1
    return n


# ── I/O-обёртки: установка / снятие / статус ─────────────────────────────────

@dataclass(frozen=True)
class _Resolved:
    """Резолв «агент + скоуп» → путь и слой (внутреннее)."""

    spec: AgentSpec
    layout: HookLayout
    path: Path


def _resolve(
    hook: HookSpec, agent: str | AgentSpec, scope: str, home: Path | None,
    cwd: Path | None, env: Mapping[str, str] | None, path: Path | None,
) -> _Resolved:
    spec = _spec_of(agent)
    layout = _require_layout(spec)
    _check_event(spec, layout, hook.event)
    p = Path(path) if path is not None else hook_settings_path(
        spec, scope=scope, home=home, cwd=cwd, env=env
    )
    return _Resolved(spec, layout, p)


def _load(path: Path, io: SettingsIO) -> dict[str, Any]:
    """Настройки с диска (нет файла → ``{}``; битый — честная ошибка, не затирание)."""
    if not io.exists(path):
        return {}
    try:
        raw = io.read(path)
    except OSError as exc:
        raise HookError("settings_broken", f"Настройки нечитаемы: {path} ({exc})") from None
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Молча перезаписать чужой сломанный конфиг — потерять его.
        raise HookError("settings_broken", f"Настройки — битый JSON: {path} ({exc})") from None
    return _as_settings(data, str(path))


def _dump(settings: dict[str, Any]) -> str:
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def _save(path: Path, text: str, io: SettingsIO) -> None:
    """Записать настройки, переводя отказ ФС в доменную ошибку.

    Находка ревью 2026-07-21: файл только на чтение (или каталог без прав) давал
    голый ``PermissionError`` трейсбеком мимо ``HookError`` — CLI показывал бы
    его как краш кита, а не как «не могу писать в этот файл». Содержимое при
    этом цело (см. атомарную запись), но сказать об этом надо словами.
    """
    try:
        io.write(path, text)
    except OSError as exc:
        raise HookError(
            "settings_unwritable", f"Не могу записать настройки: {path} ({exc})"
        ) from None


def install_hook(
    hook: HookSpec | Mapping[str, Any],
    *,
    agent: str | AgentSpec = "claude",
    scope: str = "global",
    home: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    path: Path | None = None,
    io: SettingsIO = DEFAULT_IO,
    dry_run: bool = False,
    strict: bool = False,
) -> HookResult:
    """Зарегистрировать хук у агента (идемпотентно, чужие хуки целы).

    ``strict=False`` (по умолчанию): агент без механизма хуков — не ошибка, а
    ``action="skipped"`` с причиной; так потребитель зовёт установку для списка
    агентов, не разбираясь заранее, у кого хуки есть.
    ``dry_run=True`` — ничего не пишем, а отдаём в ``preview`` тот самый текст,
    который был бы записан.
    """
    hook = _coerce(hook)
    try:
        r = _resolve(hook, agent, scope, home, cwd, env, path)
    except HookError as exc:
        if strict or exc.code not in ("hooks_unsupported", "hook_format_unknown"):
            raise
        return HookResult(_key(agent), hook.event, "skipped", None, reason=exc.message)

    settings = _load(r.path, io)
    had = bool(find_hook_entries(settings, hook, key=r.layout.root_key))
    settings, changed = merge_hook(settings, hook, key=r.layout.root_key)
    verb = "updated" if had else "installed"
    if not changed:
        return HookResult(r.spec.key, hook.event, "unchanged", str(r.path),
                          reason="хук уже стоит")
    text = _dump(settings)
    if dry_run:
        return HookResult(r.spec.key, hook.event, f"would-{verb}", str(r.path), preview=text)
    _save(r.path, text, io)
    return HookResult(r.spec.key, hook.event, verb, str(r.path))


def uninstall_hook(
    hook: HookSpec | Mapping[str, Any],
    *,
    agent: str | AgentSpec = "claude",
    scope: str = "global",
    home: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    path: Path | None = None,
    io: SettingsIO = DEFAULT_IO,
    dry_run: bool = False,
    strict: bool = False,
) -> HookResult:
    """Снять наш хук. Чужие хуки/группы/события остаются нетронутыми."""
    hook = _coerce(hook)
    try:
        r = _resolve(hook, agent, scope, home, cwd, env, path)
    except HookError as exc:
        if strict or exc.code not in ("hooks_unsupported", "hook_format_unknown"):
            raise
        return HookResult(_key(agent), hook.event, "skipped", None, reason=exc.message)

    if not io.exists(r.path):
        return HookResult(r.spec.key, hook.event, "skipped", str(r.path),
                          reason="файла настроек нет")
    settings = _load(r.path, io)
    settings, changed = remove_hook(settings, hook, key=r.layout.root_key)
    if not changed:
        return HookResult(r.spec.key, hook.event, "skipped", str(r.path),
                          reason="нашего хука нет")
    text = _dump(settings)
    if dry_run:
        return HookResult(r.spec.key, hook.event, "would-removed", str(r.path), preview=text)
    _save(r.path, text, io)
    return HookResult(r.spec.key, hook.event, "removed", str(r.path))


def hook_status(
    hook: HookSpec | Mapping[str, Any],
    *,
    agent: str | AgentSpec = "claude",
    scope: str = "global",
    home: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    path: Path | None = None,
    io: SettingsIO = DEFAULT_IO,
) -> HookStatus:
    """Что стоит сейчас: наш ли хук, где файл, сколько чужих регистраций рядом."""
    hook = _coerce(hook)
    try:
        r = _resolve(hook, agent, scope, home, cwd, env, path)
    except HookError as exc:
        if exc.code not in ("hooks_unsupported", "hook_format_unknown"):
            raise
        return HookStatus(_key(agent), hook.event, supported=False, reason=exc.message)

    exists = io.exists(r.path)
    settings = _load(r.path, io) if exists else {}
    ours = find_hook_entries(settings, hook, key=r.layout.root_key)
    return HookStatus(
        agent_key=r.spec.key,
        event=hook.event,
        supported=True,
        path=str(r.path),
        settings_exist=exists,
        installed=bool(ours),
        commands=tuple(str(e.get("command")) for e in ours),
        foreign=count_foreign_entries(settings, hook, key=r.layout.root_key),
    )


def _key(agent: str | AgentSpec) -> str:
    return agent.key if isinstance(agent, AgentSpec) else str(agent)


__all__ = [
    "CLAUDE_SETTINGS_FORMAT",
    "DEFAULT_IO",
    "DEFAULT_ROOT_KEY",
    "HookError",
    "HookResult",
    "HookSpec",
    "HookStatus",
    "SettingsIO",
    "agents_with_hooks",
    "count_foreign_entries",
    "find_hook_entries",
    "hook_layout_for",
    "hook_settings_path",
    "hook_status",
    "install_hook",
    "merge_hook",
    "remove_hook",
    "supports_hooks",
    "uninstall_hook",
]


def hook_home(agent: str | AgentSpec) -> str:
    """Где хуку этого агента место: ``"frontmatter"`` | ``"settings"`` | ``"нет"``.

    Разница не в удобстве записи, а в сроке жизни. Хук в общем ``settings.json``
    переживает свой навык: навык удалили — регистрация осталась, и агент зовёт
    команду, которой больше нет. Хук во frontmatter умирает вместе с
    компонентом и чужой файл настроек не трогает вовсе.

    Поэтому там, где агент умеет читать хуки из навыка, писать их в общий файл
    не следует — даже если это «тоже работает».
    """
    layout = _spec_of(agent).hooks
    if layout is None:
        return "нет"
    return "frontmatter" if layout.in_frontmatter else "settings"
