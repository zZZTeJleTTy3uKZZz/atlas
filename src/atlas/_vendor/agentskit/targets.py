"""Резолв целевых файлов онбординга под scope (global/project) и выбор агентов.

Перенос Atlas ``commands/init._resolve_targets`` + ``_global_path_for``,
обобщённый на реестр ``agentskit.spec``. ``_global_claude_md`` — единая
override-точка (мокается в тестах), как в Atlas.

ВАЖНО (определение системы/путей): agentskit намеренно НЕ использует
``librarykit.AppPaths`` / ``platformdirs`` (OS-native app-dirs живут там — это
владелец «определения системы», clikit лишь реэкспортит). Агентские папки
(``~/.claude`` / ``~/.codex`` / ``~/.gemini`` / …) хардкодят САМИ агенты —
одинаково на всех ОС, под ``$HOME``, а НЕ ``%APPDATA%``/XDG. Поэтому global-путь =
``Path.home()/<config_dir>/…`` (верно), а ``platformdirs.user_config_dir`` дал бы
путь, куда агент не смотрит. Платформенный mapping (``.claude`` и т.п.) — это
ДАННЫЕ реестра (``AgentSpec.config_dir``/``global_subpath``), не «детект системы».
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .spec import (
    UNVERIFIED,
    AgentSpec,
    Evidence,
    SkillLayout,
    agent_registry,
    get_agent_spec,
    list_agents,
)


def _global_claude_md() -> Path:
    """Глобальный CLAUDE.md (override-точка для тестов)."""
    return Path.home() / ".claude" / "CLAUDE.md"


def global_path_for(spec: AgentSpec) -> Path | None:
    """Глобальный memory-файл агента под $HOME (claude — через override-точку)."""
    if spec.key == "claude":
        return _global_claude_md()
    if spec.global_subpath is None:
        return None
    return Path.home().joinpath(*spec.global_subpath)


def _check_skill_name(skill: str) -> str:
    """Имя навыка — плоский slug, иначе путь уезжает из папки агента.

    Находка 2026-07-21: в новой раскладке ``{skill}`` — это КАТАЛОГ, а имена
    приезжают снаружи (source-репозитории в ``~/.agents/.skill-lock.json``).
    ``"C:/x"`` в ``Path.joinpath`` абсолютен и выбрасывает базу целиком, ``".."``
    уводит наверх, ``""`` схлопывает все навыки в один ``<корень>/SKILL.md``.
    """
    s = (skill or "").strip()
    bad = (not s or s in (".", "..") or "/" in s or "\\" in s or ":" in s
           or Path(s).is_absolute())
    if bad:
        raise ValueError(
            f"Имя навыка должно быть плоским slug'ом без разделителей пути, не {skill!r}."
        )
    return s


def _config_dir_of(spec: AgentSpec, layout: SkillLayout) -> str:
    """Папка раскладки: у устаревших она могла быть своя (см. ``SkillLayout.config_dir``)."""
    return layout.config_dir or spec.config_dir


def skill_layout_for(spec: AgentSpec, *, scope: str = "project") -> SkillLayout | None:
    """Раскладка, по которой кит СТАВИТ навык в этом скоупе (``None`` — ставить некуда).

    Первая раскладка скоупа, которая является МЕСТОМ ЗАПИСИ
    (``SkillLayout.installable``), а не просто действует в нём. Разница
    принципиальна: у antigravity в проектном скоупе действует только
    непроверенная ``.agents/skills``, и «первая подходящая» раньше молча делала
    её местом установки — навык уезжал в каталог, который agy не читает
    (проба DELTA, 2026-07-21). Теперь ответ честный: ``None`` — «в этом скоупе
    у агента доказанного места нет», а перечислить читаемые каталоги можно через
    ``skill_paths_for`` / ``readonly_skill_paths_for``.

    Через неё же потребитель достаёт улику пути программно:
    ``skill_layout_for(spec, scope="global").evidence``.
    """
    layouts = spec.install_layouts_for(scope)
    return layouts[0] if layouts else None


def _root_by_layout(spec: AgentSpec, layout: SkillLayout, *, scope: str,
                    cwd: Path | None, home: Path | None) -> Path | None:
    base = _skill_base(layout, scope=scope, cwd=cwd, home=home)
    if base is None:
        return None
    head: list[str] = []
    for part in layout.subdir:
        if "{skill}" in part:
            break
        head.append(part)
    return base.joinpath(_config_dir_of(spec, layout), *head)


def skill_root_for(
    spec: AgentSpec, *, scope: str = "project", cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    """Корень навыков агента: ``<base>/<config_dir>/<subdir-без-{skill}>``.

    ``base`` — cwd проекта (``scope="project"``) или ``$HOME`` (``scope="global"``).
    ``None`` — у агента нет раскладки, действующей в этом скоупе.

    Обрезаем ``subdir`` до первого ``{skill}``: для claude корень —
    ``.claude/skills`` (в нём лежат КАТАЛОГИ навыков), для copilot —
    ``.github/prompts`` (в нём лежат файлы).
    """
    layout = skill_layout_for(spec, scope=scope)
    if layout is None:
        return None
    return _root_by_layout(spec, layout, scope=scope, cwd=cwd, home=home)


def skill_roots_for(
    spec: AgentSpec, *, scope: str = "project", cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    """ВСЕ корни навыков агента в скоупе, включая те, куда кит НЕ пишет.

    Порядок — реестровый, и первый элемент местом установки быть НЕ обязан:
    у antigravity в проектном скоупе единственный корень — read-only
    ``.agents/skills`` (опровергнут пробой DELTA), а ``skill_root_for`` там
    честно отдаёт ``None``. Потребитель, принявший первый элемент за место
    записи, положит навык ровно туда, ради чего правка и делалась. Куда ПИШЕМ —
    отвечают ``skill_root_for`` / ``skill_path_for``; список нужен, чтобы ИСКАТЬ уже
    поставленный навык (опыт 2026-07-21: opencode читает навыки сразу из
    нескольких корней, и без полного списка найти уже стоящий невозможно).
    """
    out: list[Path] = []
    for layout in spec.skill_layouts_for(scope):
        p = _root_by_layout(spec, layout, scope=scope, cwd=cwd, home=home)
        if p is not None and p not in out:
            out.append(p)
    return out


def skill_path_for(
    spec: AgentSpec, skill: str, *, scope: str = "project", cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    """Путь ФАЙЛА навыка ``skill``, куда кит его СТАВИТ (``None`` — ставить некуда).

    Раскладка (плоский файл vs каталог + ``SKILL.md``, и в какой config-папке) —
    это ДАННЫЕ ``skill_layout``/``extra_skill_layouts``, а не код; здесь они лишь
    превращаются в путь. Берётся первая раскладка скоупа, являющаяся МЕСТОМ
    ЗАПИСИ (``installable``): первая = самая доказанная (см. ``Evidence``).

    Опыт 2026-07-21 (живые прогоны ``agy --print`` с навыками-пробами): у
    antigravity место ровно одно — ``~/.gemini/config/skills/<name>/SKILL.md``
    (глобальное). В ПРОЕКТНОМ скоупе у него места записи нет (``None``):
    ``<project>/.agents/skills`` заявлен только IDE и опровергнут для CLI
    (проба DELTA), а ставить в непроверенный каталог — та же ошибка, что
    снятый ``~/.agents/skills``.

    ``ValueError`` — если имя навыка не плоский slug (см. ``_check_skill_name``).
    Имя проверяется ДО выбора раскладки: «ставить некуда» не должно прикрывать
    кривое имя, иначе ошибка всплывёт у следующего агента.
    """
    _check_skill_name(skill)
    layout = skill_layout_for(spec, scope=scope)
    if layout is None:
        return None
    return _path_by_layout(spec, layout, skill, scope=scope, cwd=cwd, home=home)


def skill_paths_for(
    spec: AgentSpec, skill: str, *, scope: str = "project", cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    """ВСЕ места, откуда агент прочитал бы навык в этом скоупе (и read-only тоже).

    Нужно, чтобы искать уже установленное, а не плодить копии: opencode
    доказанно читает и ``~/.config/opencode/skills``, и ``~/.opencode/skills``,
    и ``~/.agents/skills`` (пробы JULIET/KILO/HOTEL, 2026-07-21).

    Здесь перечисляются ВСЕ действующие раскладки, включая read-only: искать
    надо и там, куда кит не пишет (навык мог положить туда человек или другой
    инструмент). Поэтому первый элемент местом записи быть НЕ обязан (у
    antigravity в проекте он read-only); «куда пишем» — это ``skill_path_for``.
    """
    _check_skill_name(skill)
    out: list[Path] = []
    for layout in spec.skill_layouts_for(scope):
        p = _path_by_layout(spec, layout, skill, scope=scope, cwd=cwd, home=home)
        if p is not None and p not in out:
            out.append(p)
    return out


def _path_by_layout(
    spec: AgentSpec, layout: SkillLayout, skill: str, *, scope: str,
    cwd: Path | None, home: Path | None,
) -> Path | None:
    """Общий резолв «раскладка + имя → путь» (канон и легаси идут одной дорогой)."""
    name = _check_skill_name(skill)
    base = _skill_base(layout, scope=scope, cwd=cwd, home=home)
    if base is None:
        return None
    return base.joinpath(_config_dir_of(spec, layout), *layout.parts(name))


def readonly_skill_paths_for(
    spec: AgentSpec, skill: str, *, scope: str = "project", cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Пути навыка в раскладках ТОЛЬКО ДЛЯ ЧТЕНИЯ этого скоупа (кит туда не пишет).

    Не то же, что легаси: легаси — прошлая форма самого кита, а это живой
    каталог-кандидат, про который у нас нет улики (или есть опровергающая). Его
    надо уметь НАЗВАТЬ, иначе «мы туда не ставим» неотличимо от «мы про него не
    знаем», и путь заведут заново. Пример 2026-07-21: у antigravity в проектном
    скоупе это ``<project>/.agents/skills/<name>/SKILL.md``.
    """
    _check_skill_name(skill)
    out: list[Path] = []
    for layout in spec.readonly_layouts_for(scope):
        p = _path_by_layout(spec, layout, skill, scope=scope, cwd=cwd, home=home)
        if p is not None and p not in out:
            out.append(p)
    return out


def legacy_skill_paths_for(
    spec: AgentSpec, skill: str, *, scope: str = "project", cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Пути навыка в УСТАРЕВШИХ раскладках агента (только чтение, порядок реестра).

    Находка 2026-07-21: смена раскладки не стирает то, что уже лежит у людей на
    дисках — плоский ``.agents/<name>.md`` остаётся мёртвым грузом (agy его не
    читает, а инструменты считают навык установленным). Кит такие места только
    НАЗЫВАЕТ; переносит/чистит человек осознанно.
    """
    out: list[Path] = []
    for layout in spec.legacy_skill_layouts:
        p = _path_by_layout(spec, layout, skill, scope=scope, cwd=cwd, home=home)
        if p is not None:
            out.append(p)
    return out


def _skill_base(
    layout: SkillLayout, *, scope: str, cwd: Path | None, home: Path | None
) -> Path | None:
    """База под скоуп: cwd проекта или ``$HOME`` (оба — опт-ин раскладки).

    ``"repo"`` — синоним ``"project"``: словарь ``--scope`` кита (global|repo|all)
    и словарь раскладки навыков (project|global) должны сходиться.
    ``None`` — раскладка в этом скоупе не действует (``supports_scope``): так
    выражается, что глобальный путь агента лежит в ДРУГОЙ папке, чем рабочий.
    """
    if not layout.supports_scope(scope):
        return None
    if scope in ("project", "repo"):
        return Path(cwd) if cwd is not None else Path.cwd()
    return Path(home) if home is not None else Path.home()


@dataclass(frozen=True)
class Target:
    """Цель онбординга: путь + создавать-ли-если-нет + агент + режим + улика.

    ``evidence`` едет вместе с путём осознанно: потребитель (установщик навыков)
    обязан иметь возможность СПРОСИТЬ программно, доказан этот путь живым
    прогоном или он «не проверено», и предупредить — именно отсутствие такого
    вопроса дало серию ошибок «поставили туда, куда агент не смотрит».
    """

    path: Path
    create_if_missing: bool
    agent_key: str
    mode: str = "reference"
    evidence: Evidence = UNVERIFIED


def _reference_repo_files() -> list[tuple[str, str]]:
    """[(agent_key, repo_file)] для агентов с reference-файлом (порядок реестра)."""
    return [(s.key, s.repo_file) for s in list_agents() if s.repo_file]


def resolve_targets(
    *,
    scope: str = "all",
    agents: list[str] | None = None,
    mode: str = "reference",
    create: bool = False,
    cwd: Path | None = None,
) -> list[Target]:
    """Список целей под scope.

    - ``agents is None`` — ЛЕГАСИ (как Atlas): global → ~/.claude/CLAUDE.md; repo →
      существующие reference-файлы агентов в cwd (или AGENTS.md при --create).
    - ``agents`` задан — точечный выбор: для каждого выбранного агента его
      global-файл (создаётся всегда — выбор явный) и/или repo-файл (--create).
      Reference-режим: агенты без ``repo_file`` пропускаются в repo-scope.
    """
    base = Path(cwd) if cwd is not None else Path.cwd()
    targets: list[Target] = []

    if agents is not None:
        for key in agents:
            spec = agent_registry().get(key)
            if spec is None:
                continue
            if scope in ("global", "all"):
                gp = global_path_for(spec)
                if gp is not None:
                    targets.append(Target(gp, True, key, mode))
            if scope in ("repo", "all") and spec.repo_file:
                targets.append(Target(base / spec.repo_file, create, key, mode))
        return targets

    # Легаси (без --agents) — прежнее поведение Atlas.
    if scope in ("global", "all"):
        targets.append(Target(_global_claude_md(), True, "claude", mode))
    if scope in ("repo", "all"):
        seen: set[Path] = set()
        found: list[Target] = []
        for key, rf in _reference_repo_files():
            p = base / rf
            if p in seen:
                continue
            if p.exists():
                seen.add(p)
                found.append(Target(p, False, key, mode))
        if found:
            targets.extend(found)
        elif create:
            # дефолт Atlas: создать AGENTS.md (codex-конвенция).
            targets.append(Target(base / "AGENTS.md", True, "codex", mode))
    return targets


def resolve_skill_targets(
    skill: str,
    *,
    scope: str = "all",
    agents: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> list[Target]:
    """Цели full-режима (файл навыка ``skill``) по ``skill_layout`` агентов.

    Единая точка, из которой раскладка превращается в пути: её зовут CLI
    (``skill-path``) и потребители кита (напр. установщик навыков), чтобы форма
    жила ровно в одном месте — в реестре-данных, а не размазывалась по коду.

    ``agents=None`` → все агенты реестра, у которых есть ``skill_layout``;
    элементы списка — ключи ИЛИ алиасы (``"agy"`` — такой же вход, как
    ``"antigravity"``: алиас заводили ровно чтобы им пользовались).
    Скоуп ``global`` даёт цель только у агентов с ``global_install`` (опт-ин).

    Возвращаются ТОЛЬКО места записи: раскладка без улики (``installable=false``)
    сюда не попадает НИКОГДА — иначе онбординг снова начнёт создавать файлы там,
    где агент их не читает (agy + ``.agents/skills``, 2026-07-21). Отсутствие
    цели у агента в скоупе — валидный ответ; назвать читаемые каталоги можно
    через ``resolve_readonly_skill_targets``.
    """
    out: list[Target] = []
    for spec in _specs_for(agents):
        for sc in _scopes(scope):
            layout = skill_layout_for(spec, scope=sc)
            if layout is None:
                continue
            p = _path_by_layout(spec, layout, skill, scope=sc, cwd=cwd, home=home)
            if p is not None:
                # улика едет ВМЕСТЕ с путём: потребитель обязан иметь возможность
                # спросить «это доказано или предположено» до записи на диск.
                out.append(Target(p, True, spec.key, "full", layout.evidence))
    return out


def resolve_readonly_skill_targets(
    skill: str,
    *,
    scope: str = "all",
    agents: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    existing_only: bool = False,
) -> list[Target]:
    """Цели ТОЛЬКО ДЛЯ ЧТЕНИЯ (``mode="readonly"``) — читаемые, но не пишущие места.

    ``create_if_missing=False``: сюда никогда не пишут, только ищут уже лежащее
    и показывают человеку, почему установки в этот каталог не будет (улика едет
    рядом — ``Target.evidence``).
    """
    out: list[Target] = []
    for spec in _specs_for(agents):
        for sc in _scopes(scope):
            for layout in spec.readonly_layouts_for(sc):
                p = _path_by_layout(spec, layout, skill, scope=sc, cwd=cwd, home=home)
                if p is None or (existing_only and not p.exists()):
                    continue
                out.append(Target(p, False, spec.key, "readonly", layout.evidence))
    return out


def skill_evidence_for(spec: AgentSpec, *, scope: str = "project") -> Evidence | None:
    """Улика пути, по которому кит ПОСТАВИТ навык в этом скоупе (``None`` — некуда).

    Ради этого поле и заводилось (2026-07-21): установщик должен уметь
    предупредить «путь не проверен», а не рапортовать успех в каталог, куда
    агент не смотрит. ``None`` теперь означает не только «агент не умеет
    навыки», но и «в этом скоупе доказанного места нет» — так у antigravity в
    проектном скоупе (см. ``skill_layout_for``).
    """
    layout = skill_layout_for(spec, scope=scope)
    return None if layout is None else layout.evidence


def resolve_legacy_skill_targets(
    skill: str,
    *,
    scope: str = "all",
    agents: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    existing_only: bool = False,
) -> list[Target]:
    """Цели УСТАРЕВШИХ раскладок (``mode="legacy"``) — что осталось от прошлой формы.

    Находка 2026-07-21: у antigravity раскладка сменилась, поэтому кит обязан
    уметь показать старые места, иначе мигрировать/убирать нечего — их просто
    не видно. ``create_if_missing=False``: сюда НИКОГДА не пишут.
    ``existing_only=True`` — оставить только реально существующие на диске.
    """
    out: list[Target] = []
    for spec in _specs_for(agents):
        for sc in _scopes(scope):
            for layout in spec.legacy_skill_layouts:
                p = _path_by_layout(spec, layout, skill, scope=sc, cwd=cwd, home=home)
                if p is None or (existing_only and not p.exists()):
                    continue
                out.append(Target(p, False, spec.key, "legacy", layout.evidence))
    return out


def _specs_for(agents: list[str] | None) -> list[AgentSpec]:
    """Ключи/алиасы → спеки (порядок как задан; ``None`` — весь реестр)."""
    if agents is None:
        return list_agents()
    specs = [get_agent_spec(k) for k in agents]
    return [s for s in specs if s is not None]


def _scopes(scope: str) -> tuple[str, ...]:
    return ("repo", "global") if scope == "all" else (scope,)
