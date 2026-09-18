"""Реестр AI-агентов: ``AgentSpec`` как ДАННЫЕ (загружаются из ``data/agents.json``).

Принцип (из эталона ui-ux-pro-max): layout у каждого агента СВОЙ и описан
декларативно — добавить агента = добавить запись в ``agents.json`` (или
``register_agent_spec`` / entry-points ``agentskit.agent_specs``), без правки кода.
``agents.json`` — единый language-neutral источник правды (его же прочитает будущий
npm/npx-CLI).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

#: Доказано ЖИВЫМ прогоном агента (дата + способ обязательны).
EVIDENCE_EMPIRICAL = "empirical"
#: Взято из документации агента (ссылка обязательна), живьём не проверялось.
EVIDENCE_DOCS = "docs"
#: Никем не проверено: ни опыта, ни ссылки. Честный дефолт.
EVIDENCE_UNVERIFIED = "unverified"
EVIDENCE_KINDS = (EVIDENCE_EMPIRICAL, EVIDENCE_DOCS, EVIDENCE_UNVERIFIED)


@dataclass(frozen=True)
class Evidence:
    """ОТКУДА известно то, что утверждает запись реестра — машиночитаемо.

    Корень всех ошибок с путями навыков (``~/.antigravity/skills``,
    ``~/.agents/skills`` у antigravity, ``.codex/{skill}.md``) один: реестр не
    отличал «прочитано в документации» от «кто-то когда-то предположил», и
    установщик рапортовал успех в каталог, куда агент не смотрит. Поэтому у
    каждого утверждения (агента и КАЖДОГО пути навыка) есть своя улика.

    ``kind``:

    - ``empirical`` — доказано живым прогоном (``date`` + ``method`` обязаны быть
      заполнены: без даты и способа это не улика, а мнение);
    - ``docs`` — есть ссылка на документацию (``source``), живьём НЕ проверено;
    - ``unverified`` — не проверено ничем (дефолт).

    Поле доступно потребителю кита программно: ``AgentSpec.evidence``,
    ``SkillLayout.evidence``, ``Target.evidence`` и ``as_dict()`` для JSON/CLI.
    """

    kind: str = EVIDENCE_UNVERIFIED
    #: URL страницы документации ИЛИ описание опыта («agy --print, навык-проба BRAVO»).
    source: str = ""
    #: Дата живого прогона, ISO ``YYYY-MM-DD`` (только для ``empirical``).
    date: str | None = None
    #: Способ проверки — чтобы опыт можно было ПОВТОРИТЬ, а не поверить на слово.
    method: str | None = None
    #: Что именно осталось неизвестным (важнее всего для ``unverified``).
    note: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(
                f"evidence.kind: {'|'.join(EVIDENCE_KINDS)}, не {self.kind!r}."
            )

    @property
    def proven(self) -> bool:
        """Доказано ЖИВЫМ опытом (единственный уровень, которому можно верить)."""
        return self.kind == EVIDENCE_EMPIRICAL

    @property
    def known(self) -> bool:
        """Есть хоть какое-то основание (опыт или документация), а не догадка."""
        return self.kind in (EVIDENCE_EMPIRICAL, EVIDENCE_DOCS)

    def as_dict(self) -> dict:
        """Плоский вид для JSON/CLI (пустые поля не печатаем)."""
        out: dict = {"kind": self.kind}
        for name in ("source", "date", "method", "note"):
            val = getattr(self, name)
            if val:
                out[name] = val
        return out


#: Единственный дефолт для всего, что никто не проверял. Не ``None``: потребитель
#: не должен разбирать «поля нет» — «не проверено» это тоже ответ.
UNVERIFIED = Evidence()


@dataclass(frozen=True)
class SkillLayout:
    """Per-agent layout для full-материализации (фаза 2) — ДАННЫЕ, не код.

    Две формы раскладки выражаются ОДНИМ полем ``subdir``:

    - **плоский файл** — ``subdir`` без ``{skill}``: ``.codex/{skill}.md``;
    - **каталог на навык** — ``{skill}`` внутри ``subdir`` + фиксированный
      ``filename``: claude ``("skills","{skill}") + "SKILL.md"`` →
      ``.claude/skills/<name>/SKILL.md``.

    Отдельного поля под «каталог + SKILL.md» не нужно — модель его уже
    выражает (см. ``parts``).
    """

    subdir: tuple[str, ...]            # claude: ("skills","{skill}"); copilot: ("prompts",)
    filename: str                      # "SKILL.md" | "{skill}.prompt.md"
    install_type: str = "full"         # "full" | "reference"
    frontmatter: bool = True
    #: Поддерживает ли агент ГЛОБАЛЬНУЮ установку навыков под
    #: ``$HOME/<config_dir>/<subdir…>``. Опт-ин (по умолчанию False = как было:
    #: глобальной full-установки нет), чтобы включение у одного агента не начало
    #: сыпать файлы в $HOME у остальных.
    global_install: bool = False
    #: Действует ли раскладка в ПРОЕКТНОМ скоупе. Почти всегда да, но бывает
    #: наоборот: у antigravity доказанный глобальный корень (``~/.gemini/config``)
    #: и рабочий (``.agents``) — это РАЗНЫЕ папки, и валить их в одну раскладку
    #: значит снова начать писать туда, куда агент не смотрит (опыт 2026-07-21).
    project_install: bool = True
    #: Пишет ли КИТ в эту раскладку. ``False`` — поверхность ТОЛЬКО ДЛЯ ЧТЕНИЯ:
    #: путь по-прежнему называется (``skill_paths_for`` — найти уже поставленный
    #: навык, отчёты, ``skill-path``), но местом установки не становится НИКОГДА.
    #: Заведено 2026-07-21: у antigravity доказанная раскладка (``.gemini/config``)
    #: действует только глобально, и в проектном скоупе «первой подходящей»
    #: молча становилась НЕПРОВЕРЕННАЯ ``.agents/skills`` — каталог codex/opencode,
    #: где проба DELTA агентом не видна. Это тот же класс ошибки, что снятый
    #: ``~/.agents/skills``: кит рапортует успех туда, куда агент не смотрит.
    #: Инвариант (проверяется в ``AgentSpec.__post_init__``): у агента, чьи
    #: поверхности изучались живьём, раскладка без улики не может быть installable.
    installable: bool = True
    #: Переопределение ``config_dir`` агента: раскладка живёт в ДРУГОЙ папке, а не
    #: просто под другим именем файла. Нужно и каноническим путям (глобальные
    #: навыки antigravity лежат в ``.gemini/config``, а не в ``.agents``; глобальные
    #: навыки opencode — в ``.config/opencode``), и устаревшим
    #: (``AgentSpec.legacy_skill_layouts``): без этого поля старое место невозможно
    #: назвать, а значит и показать человеку перед уборкой.
    config_dir: str | None = None
    #: Откуда известен ИМЕННО ЭТОТ путь (у каждого пути своя улика — см. ``Evidence``).
    evidence: Evidence = UNVERIFIED

    def supports_scope(self, scope: str) -> bool:
        """Действует ли раскладка в этом скоупе (``project``/``repo``/``global``).

        Скоуп — не декорация: путь ``~/.agents/skills`` доказанно НЕ читается agy,
        а ``<project>/.agents/skills`` заявлен IDE. Одна раскладка на оба скоупа
        такое различие выразить не может.
        """
        if scope in ("project", "repo"):
            return self.project_install
        if scope == "global":
            return self.global_install
        raise ValueError(f"scope: project|repo|global, не '{scope}'.")

    def installs_in_scope(self, scope: str) -> bool:
        """Можно ли СЮДА писать в этом скоупе (действует И место записи).

        Два разных вопроса, которые до 2026-07-21 были одним: «агент читает
        отсюда?» и «кит имеет право сюда положить?». Раскладка-гипотеза отвечает
        «да» на первый и «нет» на второй (см. ``installable``).
        """
        return self.installable and self.supports_scope(scope)

    def parts(self, skill: str) -> tuple[str, ...]:
        """Путь навыка ОТНОСИТЕЛЬНО ``config_dir`` агента (подстановка ``{skill}``).

        claude/antigravity → ``("skills", "<name>", "SKILL.md")``;
        codex → ``("<name>.md",)``; copilot → ``("prompts", "<name>.prompt.md")``.
        """
        return (*(p.format(skill=skill) for p in self.subdir),
                self.filename.format(skill=skill))


@dataclass(frozen=True)
class HookLayout:
    """Механизм ХУКОВ агента как ДАННЫЕ (симметрично ``SkillLayout``).

    Хук — команда, которую агент сам зовёт на своё событие (у Claude Code это
    ``SessionStart`` и т.п.). Механизм у каждого агента свой и у большинства его
    НЕТ вовсе — тогда в реестре просто нет этого поля, а движок
    (``agentskit.hooks``) честно отвечает «агент не поддерживает хуки», вместо
    того чтобы гадать и писать в чужие файлы.

    Находка 2026-07-21 (снято с машины владельца): у Claude Code хуки лежат в
    ``settings.json`` под config-папкой, а корень этой папки переопределяется
    переменной окружения — поэтому и файл, и имя переменной здесь ДАННЫЕ.
    """

    #: Путь файла настроек ОТНОСИТЕЛЬНО ``config_dir`` агента: ``("settings.json",)``.
    settings_file: tuple[str, ...]
    #: Формат файла — под него в движке написан мёрж (``claude-settings-json``).
    #: Незнакомый формат → отказ, а не порча чужого файла наугад.
    format: str = "claude-settings-json"
    #: Корневой ключ, под которым агент держит хуки в файле настроек. Данные, а
    #: не константа кода: файл настроек общий, и у другого агента того же формата
    #: секция вполне может называться иначе.
    root_key: str = "hooks"
    #: Известные события агента. Непустой список = валидация: опечатку в имени
    #: события иначе видно только по молча не сработавшему хуку.
    events: tuple[str, ...] = ()
    #: Умеет ли агент читать хуки ПРЯМО ИЗ НАВЫКА (frontmatter ``SKILL.md``).
    #:
    #: Разница не в удобстве записи, а в сроке жизни. Хук в общем
    #: ``settings.json`` переживает свой навык: удалили навык — регистрация
    #: осталась, и агент зовёт команду, которой больше нет. Хук во frontmatter
    #: живёт и умирает вместе с компонентом, и чужой файл настроек не трогается
    #: вовсе. Улика (Claude, ``0026_core_hooks.md``): «hooks can be defined
    #: directly in skills and subagents using frontmatter … scoped to the
    #: component's lifecycle».
    in_frontmatter: bool = False
    #: Env-переменная, переопределяющая КОРЕНЬ настроек целиком (``CLAUDE_CONFIG_DIR``).
    config_dir_env: str | None = None
    #: Где у агента бывают настройки: ``global`` (под ``$HOME``) и/или ``project``.
    scopes: tuple[str, ...] = ("global",)


@dataclass(frozen=True)
class PermissionLayout:
    """Права агента как ДАННЫЕ: что навыку разрешено, запрещено, что спросить.

    Права живут в общем файле настроек рядом с правилами, которые человек
    писал для себя. Поэтому здесь важнее не «уметь записать», а НЕ ПОТЕРЯТЬ
    чужое: правило, которое владелец завёл руками, обязано пережить и
    установку навыка, и его снятие.

    Свои записи узнаются по примете (namespace плагина) — тем же приёмом, что
    у хуков. Всё, что приметы не несёт, не трогается вовсе: непонятное
    возвращается побайтово, а не «нормализуется».

    Улика (Claude, ``0054_core_settings.md``): секция ``permissions`` в
    ``settings.json`` со списками ``allow`` / ``deny`` / ``ask``.
    """

    #: Файл настроек ОТНОСИТЕЛЬНО ``config_dir``.
    settings_file: tuple[str, ...]
    #: Формат — под него написан мёрж. Незнакомый → отказ, а не правка наугад.
    format: str = "claude-settings-json"
    #: Корневой ключ секции прав.
    root_key: str = "permissions"
    #: Списки внутри секции: разрешено, запрещено, спросить.
    buckets: tuple[str, ...] = ("allow", "deny", "ask")
    scopes: tuple[str, ...] = ("global", "project")


@dataclass(frozen=True)
class ResourceLayout:
    """Каталог поставляемых ресурсов агента: субагенты, команды, правила.

    Три поверхности из аудита оказались одной формой: папка под ``config_dir``,
    в ней файл на единицу. Разводить их тремя почти одинаковыми классами
    значило бы трижды повторить одну ошибку при первой же правке — поэтому вид
    здесь ДАННЫЕ (``kind``), а не имя класса.

    Улики (все — ``docs/AGENTS_TOOLING_AUDIT.md`` §2–4):

    * субагенты — claude ``agents/`` (живьём: на машине владельца там лежат
      три готовых субагента), codex ``agents/*.toml``, antigravity
      ``.agents/agents/<имя>/agent.md``, opencode ``agents/*.md``;
    * команды — claude ``commands/*.md``, opencode ``commands/*.md``,
      codex ``prompts/*.md`` (помечено deprecated — поэтому отдельным полем);
    * правила — claude ``rules/*.md`` с path-scoping, antigravity ``rules/``
      с режимами Always On / Glob / Model Decision.

    Форма файла у видов разная (``.md`` против ``.toml``), и подставлять своё
    расширение нельзя: агент читает каталог по маске и чужой файл просто не
    заметит — молча, без единой жалобы.
    """

    #: Что это: ``agents`` | ``commands`` | ``rules``.
    kind: str
    #: Путь ОТНОСИТЕЛЬНО ``config_dir``: ``("agents",)``.
    subdir: tuple[str, ...]
    #: Расширение файла единицы, с точкой: ``".md"``.
    suffix: str = ".md"
    #: Каталог на единицу вместо плоского файла: ``.agents/agents/<имя>/agent.md``.
    #: Тогда ``filename`` — имя файла внутри этого каталога.
    per_unit_dir: bool = False
    filename: str = ""
    #: Где поверхность действует.
    scopes: tuple[str, ...] = ("global", "project")
    #: Агент читает её, но объявил устаревшей: ставить туда новое не надо,
    #: а находить уже поставленное — надо.
    deprecated: bool = False


@dataclass(frozen=True)
class MemoryLayout:
    """Где агент держит СВОЮ автоматическую память (симметрично ``HookLayout``).

    Это единственная поверхность из аудита, которой кит не знал вовсе, — при
    том что рабочий контекст самого кита живёт именно там. Пока адрес памяти
    не назван данными, инструменты пишут в него по строке-константе, и любая
    смена раскладки у агента ломает их молча.

    Улика (Claude, ``0034_core_memory.md``): ``~/.claude/projects/<проект>/
    memory/MEMORY.md``, корень переопределяется ``autoMemoryDirectory``.
    Улика (Codex, ``041_customization_memories.md``): ``~/.codex/memories/``.

    Ключевая тонкость — ИМЯ ПАПКИ ПРОЕКТА. У Claude это не путь и не хеш, а
    путь, в котором каждый не буквенно-цифровой символ заменён дефисом. Правило
    снято с живой машины и сверено на трёх разных путях: угадать его по одному
    примеру нельзя, потому что двойной дефис возникает и от двоеточия с
    разделителем, и от разделителя с подчёркиванием.
    """

    #: Путь ОТНОСИТЕЛЬНО ``config_dir`` до корня памяти: ``("projects",)``.
    root: tuple[str, ...]
    #: Есть ли внутри корня папка НА ПРОЕКТ. У Claude да, у Codex — общая куча.
    per_project: bool = True
    #: Подпапка внутри проекта: ``("memory",)``. Пусто — файлы лежат в корне.
    subdir: tuple[str, ...] = ()
    #: Головной файл, который агент читает первым.
    index_file: str = "MEMORY.md"
    #: Как из пути проекта получается имя папки. ``slug-dashes`` — замена всех
    #: не буквенно-цифровых символов дефисом (проверено на живой машине).
    project_key: str = "slug-dashes"
    #: Настройка агента, переопределяющая корень памяти целиком.
    root_setting: str | None = None


@dataclass(frozen=True)
class ToggleLayout:
    """Как агент ВЫКЛЮЧАЕТ навык, не удаляя его (симметрично ``HookLayout``).

    Разница между «снять» и «выключить» существенная и для человека, и для
    кита. Снятый навык надо ставить заново — с зависимостями, проверкой,
    правами. Выключенный лежит на месте и возвращается одной строкой. Пока кит
    умеет только install/remove, любая пауза стоит переустановки.

    Механизм у агентов разный и у большинства его НЕТ — тогда поля в реестре
    просто нет, и движок честно отвечает «агент не умеет выключать», а не
    правит чужие файлы наугад.

    Улика (Codex, ``020_build-skills.md``): «Use ``[[skills.config]]`` entries
    in ``~/.codex/config.toml`` to disable a skill without deleting it». То
    есть состояние живёт НЕ в самом навыке, а в настройках агента — поэтому
    здесь путь к файлу настроек, а не имя поля во frontmatter.
    """

    #: Файл настроек ОТНОСИТЕЛЬНО ``config_dir`` агента: ``("config.toml",)``.
    settings_file: tuple[str, ...]
    #: Формат файла — под него в движке написана правка. Незнакомый формат →
    #: отказ, а не порча чужого файла наугад (то же правило, что у хуков).
    format: str = "codex-config-toml"
    #: Секция, в которой агент держит состояние навыков. Данные, а не
    #: константа: файл настроек общий, у другого агента секция назовётся иначе.
    section: str = "skills.config"
    #: Имя поля с путём к навыку внутри секции.
    path_key: str = "path"
    #: Имя булева поля «включён».
    enabled_key: str = "enabled"
    #: Где у агента бывают настройки. У Codex это ``~/.codex/config.toml``.
    scopes: tuple[str, ...] = ("global",)


@dataclass(frozen=True)
class AgentSpec:
    """Один AI-агент как ДАННЫЕ: куда и как писать инструкции."""

    key: str                                   # "claude" | "cursor" | ...
    label: str                                 # "Claude Code"
    config_dir: str                            # корневая config-папка: ".claude"
    repo_file: str | None = None            # reference: файл инструкций в корне репо
    global_subpath: tuple[str, ...] | None = None  # reference: путь под $HOME
    skill_layout: SkillLayout | None = None        # full: ОСНОВНАЯ раскладка
    aliases: tuple[str, ...] = ()              # дружественные алиасы для --agents
    #: ДОПОЛНИТЕЛЬНЫЕ места, откуда агент тоже читает навыки, в порядке убывания
    #: приоритета. Не украшение: opencode доказанно читает НЕСКОЛЬКО корней
    #: (опыт 2026-07-21), и потребителю кита нужно уметь их перечислить —
    #: например, чтобы найти уже установленный навык, а не поставить ещё один.
    #: Ставит кит НЕ БОЛЕЕ ЧЕМ в одно место — первую раскладку скоупа, которая
    #: помечена ``installable`` (см. ``install_layouts_for``). Действующих в
    #: скоупе раскладок может быть несколько и все read-only — тогда мест записи
    #: в этом скоупе НЕТ вовсе (так у antigravity в проектном скоупе).
    extra_skill_layouts: tuple[SkillLayout, ...] = ()
    #: Устаревшие раскладки навыков этого же агента — ТОЛЬКО для распознавания
    #: уже лежащего на диске (миграция/уборка делается осознанно, не китом).
    #: Находка 2026-07-21: у antigravity это плоский ``.agents/{skill}.md``
    #: (прежняя запись реестра), который агент не читает.
    legacy_skill_layouts: tuple[SkillLayout, ...] = ()
    #: Дополнительные пути ДЕТЕКТА (относительно cwd/``$HOME``), кроме
    #: ``config_dir``. Опыт 2026-07-21: у opencode пользовательский корень —
    #: ``~/.config/opencode``, и детект по одному ``~/.opencode`` ложноотрицателен.
    detect_paths: tuple[str, ...] = ()
    #: Механизм хуков — ``None`` у всех, у кого его нет (это нормальный ответ).
    hooks: HookLayout | None = None
    #: Как агент выключает навык без удаления. ``None`` — не умеет,
    #: и кит об этом говорит прямо вместо догадок.
    toggle: ToggleLayout | None = None
    #: Где агент держит свою автоматическую память. ``None`` — не держит
    #: либо адрес ещё не изучен; кит тогда об этом говорит прямо.
    memory: MemoryLayout | None = None
    #: Каталоги поставляемых ресурсов: субагенты, команды, правила.
    #: Пусто — агент таких поверхностей не имеет либо они не изучены.
    resources: tuple[ResourceLayout, ...] = ()
    #: Как агент держит права. ``None`` — поверхности нет либо не изучена.
    permissions: PermissionLayout | None = None
    #: Откуда известна запись целиком (пути навыков имеют СВОЮ улику, см. ``SkillLayout``).
    evidence: Evidence = UNVERIFIED

    def __post_init__(self) -> None:
        """Непроверенная раскладка не может быть местом ЗАПИСИ у изученного агента.

        Правило (2026-07-21, после третьей ошибки одного класса): если хоть один
        путь агента доказан ЖИВЫМ прогоном, значит его поверхности реально
        изучались — и раскладка, оставшаяся при этом без улики, это гипотеза, а
        не место установки. Молча ставить в неё нельзя: ровно так навык agy
        уезжал в ``.agents/skills`` (проба DELTA — агент не видит), а установщик
        рапортовал успех. Пометь такую раскладку ``installable=false`` (читаем и
        ищем, но не пишем) — или добудь улику и стань installable честно.

        У агентов, чьи пути никто не проверял живьём, empirical-раскладок нет
        вовсе — правило их не касается: там кит знает ровно столько, сколько
        знает. Числа таких агентов здесь намеренно нет: оно меняется с каждой
        записью реестра, а цифра в прозе устаревает молча (правило и проверка —
        ``tests/test_prose_matches_registry.py``).
        """
        if not any(x.evidence.proven for x in self.skill_layouts):
            return
        for x in self.skill_layouts:
            if x.installable and not x.evidence.known:
                where = "/".join((x.config_dir or self.config_dir, *x.subdir))
                raise ValueError(
                    f"{self.key}: раскладка '{where}' помечена местом записи "
                    f"(installable), но её улика '{x.evidence.kind}', а у агента "
                    "есть пути, доказанные живым прогоном. Непроверенное место "
                    "записи — это гипотеза: поставь installable=false (только "
                    "чтение/поиск) либо добудь улику."
                )

    @property
    def skill_layouts(self) -> tuple[SkillLayout, ...]:
        """Все живые раскладки навыков: основная, затем дополнительные."""
        if self.skill_layout is None:
            return self.extra_skill_layouts
        return (self.skill_layout, *self.extra_skill_layouts)

    def skill_layouts_for(self, scope: str) -> tuple[SkillLayout, ...]:
        """Раскладки, ДЕЙСТВУЮЩИЕ в скоупе — всё, откуда агент читает.

        Для поиска уже поставленного навыка и отчётов. Место установки среди них
        не обязано быть: см. ``install_layouts_for``.
        """
        return tuple(x for x in self.skill_layouts if x.supports_scope(scope))

    def install_layouts_for(self, scope: str) -> tuple[SkillLayout, ...]:
        """Раскладки, куда кит имеет право ПИСАТЬ в этом скоупе (первая — куда ставим).

        Пустой кортеж — валидный ответ «в этом скоупе ставить некуда» (у
        antigravity так в проектном скоупе: доказан только глобальный корень).
        """
        return tuple(x for x in self.skill_layouts if x.installs_in_scope(scope))

    def readonly_layouts_for(self, scope: str) -> tuple[SkillLayout, ...]:
        """Действующие, но НЕ пишущие раскладки скоупа (только чтение/поиск).

        Их надо уметь назвать: иначе «кит сюда не пишет» выглядит как «кит про
        этот каталог не знает», и путь заводят заново.
        """
        return tuple(x for x in self.skill_layouts
                     if x.supports_scope(scope) and not x.installable)


def _evidence_from_dict(d: dict | None) -> Evidence:
    """Улика из данных реестра (нет поля → честное «не проверено»)."""
    if not d:
        return UNVERIFIED
    return Evidence(
        kind=d.get("kind", EVIDENCE_UNVERIFIED),
        source=d.get("source", ""),
        date=d.get("date"),
        method=d.get("method"),
        note=d.get("note"),
    )


def _layout_from_dict(sl: dict) -> SkillLayout:
    """Одна раскладка (каноническая, дополнительная или устаревшая) из данных."""
    return SkillLayout(
        subdir=tuple(sl.get("subdir") or ()),
        filename=sl["filename"],
        install_type=sl.get("install_type", "full"),
        frontmatter=bool(sl.get("frontmatter", True)),
        global_install=bool(sl.get("global_install", False)),
        project_install=bool(sl.get("project_install", True)),
        installable=bool(sl.get("installable", True)),
        config_dir=sl.get("config_dir"),
        evidence=_evidence_from_dict(sl.get("evidence")),
    )


def _hooks_from_dict(h: dict) -> HookLayout:
    """Механизм хуков агента из данных реестра (см. ``HookLayout``)."""
    return HookLayout(
        settings_file=tuple(h.get("settings_file") or ()),
        format=h.get("format", "claude-settings-json"),
        root_key=h.get("root_key", "hooks"),
        events=tuple(h.get("events") or ()),
        in_frontmatter=bool(h.get("in_frontmatter", False)),
        config_dir_env=h.get("config_dir_env"),
        scopes=tuple(h.get("scopes") or ("global",)),
    )


def _toggle_from_dict(t: dict) -> ToggleLayout:
    """Механизм выключения навыка из данных реестра (см. ``ToggleLayout``)."""
    return ToggleLayout(
        settings_file=tuple(t.get("settings_file") or ()),
        format=t.get("format", "codex-config-toml"),
        section=t.get("section", "skills.config"),
        path_key=t.get("path_key", "path"),
        enabled_key=t.get("enabled_key", "enabled"),
        scopes=tuple(t.get("scopes") or ("global",)),
    )


def _permissions_from_dict(p: dict) -> PermissionLayout:
    """Раскладка прав агента из данных реестра (см. ``PermissionLayout``)."""
    return PermissionLayout(
        settings_file=tuple(p.get("settings_file") or ()),
        format=p.get("format", "claude-settings-json"),
        root_key=p.get("root_key", "permissions"),
        buckets=tuple(p.get("buckets") or ("allow", "deny", "ask")),
        scopes=tuple(p.get("scopes") or ("global", "project")),
    )


def _resource_from_dict(r: dict) -> ResourceLayout:
    """Каталог ресурсов агента из данных реестра (см. ``ResourceLayout``)."""
    return ResourceLayout(
        kind=r["kind"],
        subdir=tuple(r.get("subdir") or ()),
        suffix=r.get("suffix", ".md"),
        per_unit_dir=bool(r.get("per_unit_dir", False)),
        filename=r.get("filename", ""),
        scopes=tuple(r.get("scopes") or ("global", "project")),
        deprecated=bool(r.get("deprecated", False)),
    )


def _memory_from_dict(m: dict) -> MemoryLayout:
    """Раскладка памяти агента из данных реестра (см. ``MemoryLayout``)."""
    return MemoryLayout(
        root=tuple(m.get("root") or ()),
        per_project=bool(m.get("per_project", True)),
        subdir=tuple(m.get("subdir") or ()),
        index_file=m.get("index_file", "MEMORY.md"),
        project_key=m.get("project_key", "slug-dashes"),
        root_setting=m.get("root_setting"),
    )


def _spec_from_dict(key: str, d: dict) -> AgentSpec:
    sl = d.get("skill_layout")
    layout = _layout_from_dict(sl) if sl else None
    extra = tuple(_layout_from_dict(x) for x in (d.get("extra_skill_layouts") or ()))
    legacy = tuple(_layout_from_dict(x) for x in (d.get("legacy_skill_layouts") or ()))
    gs = d.get("global_subpath")
    hooks = d.get("hooks")
    toggle = d.get("toggle")
    memory = d.get("memory")
    resources = d.get("resources") or []
    permissions = d.get("permissions")
    return AgentSpec(
        key=key,
        label=d.get("label", key),
        config_dir=d["config_dir"],
        repo_file=d.get("repo_file"),
        global_subpath=tuple(gs) if gs else None,
        skill_layout=layout,
        aliases=tuple(d.get("aliases") or ()),
        extra_skill_layouts=extra,
        legacy_skill_layouts=legacy,
        detect_paths=tuple(d.get("detect_paths") or ()),
        hooks=_hooks_from_dict(hooks) if hooks else None,
        toggle=_toggle_from_dict(toggle) if toggle else None,
        memory=_memory_from_dict(memory) if memory else None,
        resources=tuple(_resource_from_dict(r) for r in resources),
        permissions=_permissions_from_dict(permissions) if permissions else None,
        evidence=_evidence_from_dict(d.get("evidence")),
    )


@lru_cache(maxsize=1)
def _builtin_registry() -> dict[str, AgentSpec]:
    """Канон из ``data/agents.json`` + расширения из entry-points ``agentskit.agent_specs``."""
    raw = resources.files(__package__).joinpath("data", "agents.json").read_text("utf-8")
    data = json.loads(raw)
    reg: dict[str, AgentSpec] = {
        key: _spec_from_dict(key, d) for key, d in data["agents"].items()
    }
    # внешние пакеты добавляют агентов через entry-points (не правя кит).
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="agentskit.agent_specs"):
            try:
                obj = ep.load()
                spec = obj() if callable(obj) else obj
                if isinstance(spec, AgentSpec):
                    reg[spec.key] = spec
            except Exception:  # pragma: no cover — битый плагин не валит реестр
                continue
    except Exception:  # pragma: no cover
        pass
    return reg


#: In-tree ручные расширения (перебивают встроенный канон). Заполняется
#: ``register_agent_spec``; собирается в финальный реестр в ``agent_registry()``.
_OVERRIDES: dict[str, AgentSpec] = {}


def register_agent_spec(spec: AgentSpec) -> None:
    """Зарегистрировать/переопределить агента в процессе (перебивает встроенный)."""
    _OVERRIDES[spec.key] = spec
    agent_registry.cache_clear()


@lru_cache(maxsize=1)
def agent_registry() -> dict[str, AgentSpec]:
    """Итоговый реестр: встроенный канон + entry-points + ручные override."""
    reg = dict(_builtin_registry())
    reg.update(_OVERRIDES)
    return reg


def _alias_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for key, spec in agent_registry().items():
        idx[key] = key
        for a in spec.aliases:
            idx[a.lower()] = key
    return idx


def list_agents() -> list[AgentSpec]:
    """Весь канонический реестр (для меню/CLI ``agents``)."""
    return list(agent_registry().values())


def get_agent_spec(key: str) -> AgentSpec | None:
    """AgentSpec по ключу или алиасу (или ``None``)."""
    return agent_registry().get(_alias_index().get(key.strip().lower(), key))


def resolve_agent_keys(raw: str) -> list[str]:
    """Разобрать строку ``--agents`` в список валидных ключей реестра.

    Принимает ``"all"`` или CSV ключей/алиасов (``"claude,cursor"`` /
    ``"agents, agy"``). Дубликаты схлопываются с сохранением порядка.
    Неизвестный ключ → ``ValueError`` со списком валидных.
    """
    raw = (raw or "").strip()
    idx = _alias_index()
    if raw.lower() == "all":
        return list(agent_registry().keys())
    out: list[str] = []
    for tok in raw.split(","):
        name = tok.strip().lower()
        if not name:
            continue
        key = idx.get(name)
        if key is None:
            valid = ", ".join([*agent_registry().keys(), "all"])
            raise ValueError(f"Неизвестный агент '{tok.strip()}'. Доступно: {valid}.")
        if key not in out:
            out.append(key)
    if not out:
        raise ValueError("Пустой список --agents.")
    return out
