"""CLI-команды `atlas projects ...`.

CRUD по проектам портфеля + init + archive engine.

Команды:
- ``init``       — создать БД, применить миграции, seed справочников.
- ``add``        — создать проект (slug/prefix авто или явно).
- ``list``       — список проектов (фильтры по type / status / archived).
- ``get``        — карточка проекта (по slug, full UUID или short UUID prefix).
- ``update``     — изменить поля проекта (любые, кроме slug).
- ``delete``     — soft archive (по умолчанию) или ``--hard`` для физ. удаления.

Archive engine (см. Atlas ARCHITECTURE.md §2.7, ADR-001):
- ``archive``    — физический mv в ``_Archive/<group>/`` + обновление БД.
- ``unarchive``  — обратный mv + установка статуса (default: active).
- ``renew``      — инкремент renewal_count + опц. unarchive (только client-project).
- ``move``       — сменить project_type, физ. mv если группа другая.
- ``reorganize`` — проверить + починить расхождения БД ↔ файловая система.

Справочники types/statuses вынесены в отдельные top-level subapp
(`atlas types ...`, `atlas statuses ...`) — см. types.py и statuses.py.
"""
from __future__ import annotations

from atlas.appconfig import default_actor

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data, emit_message, emit_table
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import (
    ActionLog,
    Participant,
    Project,
    ProjectParticipant,
    ProjectStatus,
    ProjectType,
    Tag,
)
from atlas.junctions import (
    JunctionError,
    SafetyError,
    create_junction,
    is_junction,
    junction_target,
    remove_junction,
)
from atlas.layout import (
    _perform_storage_move,
    container_own_logical,
    get_logical_path,
    get_storage_path,
    resolve_container_logical,
)
from atlas.paths import (
    archive_path,
    expected_project_path,
    get_projects_root,
    group_path,
    type_slug_to_group,
)
from atlas.appconfig import load_config, owner_member_slug, resolve_api_key
from atlas.seeds import seed_all
from atlas.sync.hub_service import HubService
from atlas.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    generate_prefix_from_slug,
    generate_unique_slug,
    resolve_project_ref,
    slugify_text,
)
from atlas.tags import (
    AmbiguousTagRefError,
    InvalidTagCategoryError,
    attach_tags,
    detach_tags,
    filter_projects_by_tags,
    list_project_tags,
    resolve_tag_ref,
)

projects_app = typer.Typer(
    no_args_is_help=True,
    help="Projects management: проекты портфеля (PM-БД), CRUD.",
)
console = Console()

# Git/GitLab integration: sub-typer `atlas projects git ...` (см. projects_git.py).
from atlas.commands.projects_git import git_app as _git_app  # noqa: E402

projects_app.add_typer(_git_app, name="git")

# Суб-ресурсы проекта (канон `atlas project <ресурс> <глагол>`, как `git`/`layout`):
#   project tag    add | rm     — attach/detach тега к проекту (сам словарь тегов — `atlas tag`)
#   project member add|list|rm  — роли участников в проекте (lead/member)
project_tag_app = typer.Typer(no_args_is_help=True, help="Теги проекта (attach/detach).")
projects_app.add_typer(project_tag_app, name="tag")
project_member_app = typer.Typer(
    no_args_is_help=True, help="Участники проекта (роли lead/member)."
)
projects_app.add_typer(project_member_app, name="member")

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
# Роли участия члена в проекте (F4f). lead → видит ВСЕ задачи проекта;
# member → только свои задачи. Литералы совпадают с core_project_member.role
# (ядро), чтобы синк role_in_project → core ложился без преобразования.
VALID_PROJECT_MEMBER_ROLES = {"lead", "member"}
DEFAULT_PROJECT_MEMBER_ROLE = "member"
SLUG_RE = re.compile(r"^[a-z0-9-]{2,50}$")
PREFIX_RE = re.compile(r"^[a-z0-9]{1,5}$")
DEFAULT_ACTOR_SLUG = default_actor()

# Статусы, с которыми можно архивировать проект (status в момент archive).
# Канон W45-39: только `archived` и `cancelled` (отказ).
VALID_ARCHIVE_STATUSES = {"archived", "cancelled"}


# --------------------------------------------------------------------------- #
# DB helpers                                                                  #
# --------------------------------------------------------------------------- #


def _db_url() -> str:
    return resolve_db_url()


def _find_project_root() -> Path:
    """Найти корень проекта (где alembic.ini)."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "alembic.ini").exists():
            return parent
    raise RuntimeError("Не найден alembic.ini: не могу определить корень проекта")


def _actor_id(session: Session) -> Optional[str]:
    """Получить id участника-актора (владелец стора) из seed для action_log."""
    actor = session.execute(
        select(Participant).where(Participant.slug == DEFAULT_ACTOR_SLUG)
    ).scalar_one_or_none()
    return actor.id if actor else None


def _log_action(
    session: Session,
    *,
    action: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    """Добавить запись в action_log (commit вызывает caller)."""
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="project",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        console.print(
            f"[red]Невалидный slug '{slug}': допустимы [a-z0-9-], длина 2-50.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_prefix(prefix: str) -> None:
    if not PREFIX_RE.match(prefix):
        console.print(
            f"[red]Невалидный prefix '{prefix}': допустимы [a-z0-9], длина 1-5.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_priority(priority: str) -> None:
    if priority not in VALID_PRIORITIES:
        console.print(
            f"[red]Невалидный priority '{priority}': допустимы {sorted(VALID_PRIORITIES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _slug_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Project.id).where(Project.slug == candidate)
        ).scalar_one_or_none() is not None
    return _check


def _prefix_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Project.id).where(Project.prefix == candidate)
        ).scalar_one_or_none() is not None
    return _check


def _resolve_tags_or_die(session: Session, tag_refs: list[str]) -> list[Tag]:
    """Резолв списка tag-refs: raise typer.Exit на несуществующий.

    Подсказка в сообщении: `atlas tags add --slug ... --category ...`.
    """
    resolved: list[Tag] = []
    for ref in tag_refs:
        try:
            tag = resolve_tag_ref(session, ref)
        except (AmbiguousTagRefError, InvalidTagCategoryError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if tag is None:
            console.print(
                f"[red]Tag '{ref}' не найден. "
                f"Создайте: `atlas tags add --slug ... --category ...`.[/red]"
            )
            raise typer.Exit(code=1)
        resolved.append(tag)
    return resolved


CANONICAL_README_TEMPLATE = """\
# {name}

> {one_line}

## Статус

- **Type**: `{type_slug}`
- **Status**: `{status_slug}`
- **Priority**: {priority}
- **Slug**: `{slug}`
- **Prefix**: `{prefix}`
- **Tags**: {tags_str}
- Создан: {created_date}

## Atlas

Карточка проекта в Atlas-БД (Atlas):

```sh
atlas projects get {slug}
```

Физический layout:

- Storage: `_storage/{slug}/`
- Junction: `{logical_rel}` → `_storage/{slug}`

## TODO (placeholder)

- [ ] Заполнить README реальным контентом проекта.
- [ ] Подключить GitLab-репозиторий (если ещё нет): `atlas projects git init {slug}`.
"""

CANONICAL_AGENTS_TEMPLATE = """\
# AGENTS.md — {name}

> Контекст для AI-ассистентов (Claude Code, ChatGPT, Cursor и т.п.), работающих
> над этим проектом.

## Что это

{one_line}

## Atlas

Проект зарегистрирован в Atlas-БД (Atlas). Карточка:

```sh
atlas projects get {slug}
```

Любые изменения метаданных (приоритет, статус, теги) — через atlas CLI:

- `atlas projects update {slug} --priority P0` — поменять приоритет
- `atlas project tag add {slug} -t domain:<slug>` — добавить тег
- `atlas projects move {slug} --to-type <type>` — конвертировать тип

## Тип / Статус (на момент создания)

- type=`{type_slug}`, status=`{status_slug}`, priority=`{priority}`

## Правила работы

- Все исходные тексты, документы, код проекта — в этом репо.
- Чувствительные данные (`.env`, токены, ключи) — игнорируются `.gitignore`.
- AI-ассистенту разрешено: читать, генерировать, редактировать в этом репо.

## Канонические команды

- `atlas projects get {slug}` — карточка проекта
- `atlas task list --project {slug}` — задачи проекта (когда W7
  волна будет реализована)

{atlas_prompt_block}
"""

# --------------------------------------------------------------------------- #
# Onboarding prompt block (задача #211)                                        #
#                                                                              #
# Маркер-делимитированный блок-указатель, который Atlas ИДЕМПОТЕНТНО вписывает  #
# в AGENTS.md (и CLAUDE.md при наличии) проекта: «веди задачи/проекты через CLI #
# atlas и вызывай навык atlas». Маркеры позволяют апдейтить блок без дублей.    #
# --------------------------------------------------------------------------- #

ATLAS_PROMPT_START = "<!-- atlas:usage:start -->"
ATLAS_PROMPT_END = "<!-- atlas:usage:end -->"

#: Внутренность блока (между маркерами, без самих маркеров).
ATLAS_PROMPT_BODY = """\
## Управление проектом — через Atlas

Этот проект ведётся в Atlas (личная PM-система портфеля). Для задач/проектов/эпиков/бэкапов
используй CLI `atlas` и вызывай навык `atlas` — вся логика и роутинг внутри навыка."""

#: Полный блок с маркерами (вставляется в файлы и в шаблон AGENTS.md).
ATLAS_PROMPT_BLOCK = f"{ATLAS_PROMPT_START}\n{ATLAS_PROMPT_BODY}\n{ATLAS_PROMPT_END}"

#: Регексп для вычистки ЛЮБОЙ корректной пары маркеров (START..END).
#: Между START и END запрещён вложенный START — иначе сиротский START без своего
#: END «склеился» бы с END следующего легитимного блока и вычистил весь
#: пользовательский контент между ними (потеря данных, #211 finding 1). С запретом
#: вложенного START сиротский START просто не матчится и остаётся как обычный
#: текст, а валидные блоки удаляются по отдельности (дедупликация, findings 2/5/9).
_ATLAS_PROMPT_BLOCK_RE = re.compile(
    re.escape(ATLAS_PROMPT_START)
    + r"(?:(?!" + re.escape(ATLAS_PROMPT_START) + r").)*?"
    + re.escape(ATLAS_PROMPT_END),
    re.DOTALL,
)


def _ensure_atlas_prompt_block(path: Path) -> bool:
    """Идемпотентно вписать блок-указатель «пользуйся Atlas» в существующий файл.

    Поведение (самоисцеляющееся — гарантирует РОВНО одну пару маркеров на любом
    входе, включая повреждённый: несколько пар, сиротский START без END и т.п.):

    - Файла нет → ничего не делаем (CLAUDE.md не создаём), возвращаем ``False``.
    - Все существующие корректные пары маркеров (``START..END``) вычищаются
      регекспом, затем единственный канонический блок дописывается в конец.
      Так файл с 0/1/N парами всегда приводится к инварианту ``(1, 1)``.
    - Сиротский START без END (или END раньше START) НЕ матчится регекспом и
      остаётся в тексте как обычный контент — второй (валидный) блок при этом
      дописывается отдельно, поэтому append НЕ затирает пользовательский контент
      между сиротским маркером и хвостом файла.
    - Если после нормализации текст не изменился → ``False`` (идемпотентность).

    Окончания строк исходного файла сохраняются: если файл был в CRLF — результат
    тоже в CRLF (вставляется только одна секция, а не нормализуется весь файл).
    Чтение через ``newline=""`` отключает universal-newline трансляцию.

    Возвращает ``True``, если файл был изменён. Запись атомарная
    (tmp-файл + ``os.replace``), чтобы не оставить полу-записанный файл.
    """
    if not path.exists():
        return False

    # newline="" → НЕ транслируем CRLF→LF: сохраняем исходные окончания строк.
    original = path.read_text(encoding="utf-8", newline="")

    # Определяем доминирующий стиль окончаний строк исходного файла.
    crlf = "\r\n" in original
    eol = "\r\n" if crlf else "\n"

    # Работаем в LF-нормализованном пространстве, чтобы единообразно вычищать
    # маркеры и собирать блок; стиль окончаний восстанавливаем перед записью.
    lf_original = original.replace("\r\n", "\n")

    # Вычищаем ВСЕ корректные пары маркеров (дедупликация + удаление устаревших).
    stripped = _ATLAS_PROMPT_BLOCK_RE.sub("", lf_original)

    # Единственный канонический блок дописываем в конец с разделителем.
    new_lf = stripped.rstrip("\n") + "\n\n" + ATLAS_PROMPT_BLOCK + "\n"

    # Восстанавливаем исходный стиль окончаний строк.
    new_text = new_lf.replace("\n", eol) if crlf else new_lf

    if new_text == original:
        return False

    _atomic_write_text(path, new_text, eol=eol)
    return True


def _atomic_write_text(path: Path, text: str, *, eol: str = "\n") -> None:
    """Атомарно записать текст в ``path`` (tmp в той же директории + replace).

    ``text`` записывается как есть (``newline=""``): окончания строк в ``text``
    уже приведены к нужному стилю вызывающим, поэтому повторной трансляции нет.
    """
    import tempfile

    directory = path.parent
    fd, tmp_str = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _ensure_atlas_prompt_in_dir(directory: Path) -> list[str]:
    """Вписать онбординг-блок в AGENTS.md/CLAUDE.md директории, если они есть.

    Единый хелпер для обоих онбординг-флоу (`project add --canonical` и
    `idea promote --canonical`), чтобы поведение не расходилось: дополняем
    блоком уже существующие файлы (новый AGENTS.md приходит из шаблона с блоком,
    а существовавший AGENTS.md и любой CLAUDE.md — иначе остались бы без блока).

    Возвращает список имён файлов, которые были изменены.
    """
    prompted: list[str] = []
    for fname in ("AGENTS.md", "CLAUDE.md"):
        if _ensure_atlas_prompt_block(directory / fname):
            prompted.append(fname)
    return prompted


CANONICAL_GITIGNORE_TEMPLATE = """\
# === atlas universal gitignore ===

# OS / IDE
.DS_Store
Thumbs.db
.vscode/
.idea/
*.swp
*.swo

# Sensitive
.env
.env.local
*.key
*.pem
secrets/
private/

# Python
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.ruff_cache/
*.egg-info/

# Node / JS
node_modules/
.next/
dist/
build/

# Temporary / large
*.log
*.tmp
nul
NUL
*.zip
*.rar
*.7z

# Media (selectively unignore via !path/*.ext if needed for fixtures)
*.mp4
*.mov
*.avi
*.mkv
"""


def _create_canonical_files(
    local_path: Path,
    *,
    project: Project,
    type_slug: str,
    status_slug: str,
    tag_slugs: list[str],
    logical_rel: str,
) -> list[str]:
    """Создать README.md / AGENTS.md / .gitignore если их нет.

    Возвращает список созданных файлов (для логирования).
    """
    created: list[str] = []
    common = {
        "name": project.name,
        "slug": project.slug,
        "prefix": project.prefix or "",
        "priority": project.priority,
        "type_slug": type_slug,
        "status_slug": status_slug,
        "one_line": project.one_line_summary or "(заполнить one-line)",
        "tags_str": ", ".join(f"`{t}`" for t in tag_slugs) if tag_slugs else "—",
        "created_date": datetime.now().strftime("%Y-%m-%d"),
        "logical_rel": logical_rel,
        "atlas_prompt_block": ATLAS_PROMPT_BLOCK,
    }
    targets = [
        ("README.md", CANONICAL_README_TEMPLATE),
        ("AGENTS.md", CANONICAL_AGENTS_TEMPLATE),
        (".gitignore", CANONICAL_GITIGNORE_TEMPLATE),
    ]
    for filename, template in targets:
        path = local_path / filename
        if path.exists():
            continue
        path.write_text(template.format(**common), encoding="utf-8")
        created.append(filename)
    return created


def _setup_storage_and_junction(
    slug: str,
    type_slug: str,
    *,
    archived: bool = False,
    archived_group: Optional[str] = None,
    parent_id: Optional[str] = None,
    container_logical: Optional[Path] = None,
) -> tuple[Path, Path, bool]:
    """Создать `_storage/<slug>/` и junction в logical, если нужно.

    Возвращает ``(logical_path, storage_path, junction_created)``.

    Module-aware (#163/#126): если задан ``parent_id`` И ``container_logical``
    (логический путь проекта-контейнера), то junction кладётся в
    ``<container_logical>/modules/<slug>/`` (а не в type-группу). Это делает
    проект ФИЗИЧЕСКИМ модулем контейнера. Для standalone/контейнера — прежнее
    поведение (type-группа). Идемпотентно: правильный junction не пересоздаём.

    NOTE: Если logical уже существует и НЕ junction — оставляем как есть
    (логика migrate-to-storage обработает позднее, через `atlas projects
    layout init`). SAFETY: реальные директории не удаляем; снятие junction —
    только через `remove_junction` (is_junction-проверка внутри).
    """
    root = get_projects_root()
    storage = get_storage_path(slug, root=root)
    storage.mkdir(parents=True, exist_ok=True)

    fake_proj = type(
        "P",
        (),
        {
            "slug": slug,
            "type_slug": type_slug,
            "archived": archived,
            "archived_group": archived_group,
            "parent_id": parent_id,
        },
    )()
    logical = get_logical_path(
        fake_proj, root=root, container_logical=container_logical
    )

    junction_created = False
    if logical.resolve() != storage.resolve():
        if not logical.exists():
            # Для модуля это создаст и промежуточную папку modules/.
            logical.parent.mkdir(parents=True, exist_ok=True)
            create_junction(logical, storage)
            junction_created = True
        elif is_junction(logical):
            current = None
            try:
                current = junction_target(logical)
            except Exception:
                pass
            if current is None or current.resolve() != storage.resolve():
                remove_junction(logical)
                create_junction(logical, storage)
                junction_created = True

    return logical, storage, junction_created


def _view_container(session: Session, pid: str):
    """duck-typed view контейнера по id (несёт local_path для #1/#12).

    Возвращает None, если контейнер не найден (висячий parent_id, #16).
    """
    proj = session.get(Project, pid)
    if proj is None:
        return None
    pt = session.get(ProjectType, proj.type_id)
    return type(
        "C",
        (),
        {
            "slug": proj.slug,
            "type_slug": pt.slug if pt else "business-product",
            "archived": proj.archived_at is not None,
            "archived_group": proj.archived_group,
            "parent_id": proj.parent_id,
            "local_path": proj.local_path,
        },
    )()


def _container_logical_for(
    session: Session, parent_id: str, *, root: Optional[Path] = None
) -> Optional[Path]:
    """Логический путь проекта-контейнера по его id (#163/#126).

    Предпочитает РЕАЛЬНЫЙ ``Project.local_path`` контейнера (#1/#12): модуль
    ляжет в ``<реальный_контейнер>/modules/<slug>``, а не в фантомную
    type-группу. Рекурсивно поднимается по цепочке родителей (#17,
    cycle-safe). None — если контейнер не найден (висячий parent_id, #16).
    """
    def _resolver(pid: str):
        return _view_container(session, pid)

    container = _resolver(parent_id)
    if container is None:
        return None
    # Вложенный контейнер (контейнер сам — модуль): рекурсивно резолвим.
    nested = resolve_container_logical(container, _resolver, root=root)
    return container_own_logical(
        container, root=root, nested_container_logical=nested
    )


def _project_logical_for(
    session: Session,
    project: Project,
    *,
    parent_id: Optional[str],
    root: Optional[Path] = None,
) -> Path:
    """Module-aware логический путь проекта при заданном ``parent_id``.

    Для модуля — ``<container_logical>/modules/<slug>``; для standalone —
    type-группа/архив-путь. Используется при re-parent и module-aware delete.
    """
    root = root or get_projects_root()
    pt = session.get(ProjectType, project.type_id)
    container_logical: Optional[Path] = None
    if parent_id is not None:
        container_logical = _container_logical_for(session, parent_id, root=root)
    view = type(
        "P",
        (),
        {
            "slug": project.slug,
            "type_slug": pt.slug if pt else "business-product",
            "archived": project.archived_at is not None,
            "archived_group": project.archived_group,
            "parent_id": parent_id,
        },
    )()
    return get_logical_path(view, root=root, container_logical=container_logical)


def _reparent_relocate_junction(
    session: Session,
    project: Project,
    *,
    old_parent_id: Optional[str],
    new_parent_id: Optional[str],
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Перенести junction модуля при смене parent (#2/#8/#14).

    Junction-aware: НЕ двигаем storage, только пересоздаём junction в новой
    логической локации (modules/ нового контейнера или type-группа при
    standalone) и снимаем старый junction (modules/ прежнего контейнера или
    прежняя type-группа). Обновляем ``project.local_path``.

    SAFETY: реальные директории не удаляем — снятие только через
    `remove_junction` (is_junction-проверка внутри). Если storage модуля
    отсутствует (проект ещё не разложен) — ничего не делаем (нечего двигать).
    """
    root = root or get_projects_root()
    result: dict[str, Any] = {
        "junction_created": False,
        "old_junction_removed": None,
        "new_local_path": None,
        "warning": None,
    }
    storage = get_storage_path(project.slug, root=root)
    if not storage.exists():
        # Нечего переносить — проект не разложен; обновим только local_path-цель.
        new_logical = _project_logical_for(
            session, project, parent_id=new_parent_id, root=root
        )
        project.local_path = str(new_logical)
        result["new_local_path"] = str(new_logical)
        result["warning"] = (
            "storage модуля отсутствует — junction не пересоздан "
            "(запусти `atlas projects layout init` если нужна физика)."
        )
        return result

    old_logical = _project_logical_for(
        session, project, parent_id=old_parent_id, root=root
    )
    new_logical = _project_logical_for(
        session, project, parent_id=new_parent_id, root=root
    )

    # Снять старый junction (только если это наш junction на наш storage).
    if old_logical.resolve() != new_logical.resolve():
        try:
            if is_junction(old_logical):
                tgt = junction_target(old_logical)
                if tgt is not None and tgt.resolve() == storage.resolve():
                    remove_junction(old_logical)
                    result["old_junction_removed"] = str(old_logical)
        except (SafetyError, JunctionError, OSError) as exc:
            result["warning"] = f"не удалось снять старый junction: {exc}"

    # Создать junction в новой логической локации (если ещё не там).
    try:
        if new_logical.resolve() != storage.resolve():
            if is_junction(new_logical):
                tgt = junction_target(new_logical)
                if tgt is None or tgt.resolve() != storage.resolve():
                    remove_junction(new_logical)
                    new_logical.parent.mkdir(parents=True, exist_ok=True)
                    create_junction(new_logical, storage)
                    result["junction_created"] = True
            elif new_logical.exists():
                # Реальная директория на новом logical — не трогаем (safety).
                result["warning"] = (
                    f"на новом logical {new_logical} реальная директория — "
                    f"junction не создан (ручное вмешательство)."
                )
            else:
                new_logical.parent.mkdir(parents=True, exist_ok=True)
                create_junction(new_logical, storage)
                result["junction_created"] = True
    except (SafetyError, JunctionError, OSError) as exc:
        result["warning"] = f"не удалось создать новый junction: {exc}"

    project.local_path = str(new_logical)
    result["new_local_path"] = str(new_logical)

    # #127: новый контейнер должен игнорировать modules/ в своём git.
    if new_parent_id is not None:
        new_container_logical = _container_logical_for(
            session, new_parent_id, root=root
        )
        if new_container_logical is not None:
            try:
                _ensure_gitignore_modules(new_container_logical)
            except Exception:  # noqa: BLE001 — best-effort
                pass

    return result


def _require_container_laid_out(
    session: Session,
    parent_id: str,
    container_logical: Optional[Path],
    *,
    root: Optional[Path] = None,
) -> None:
    """Гарантировать, что контейнер физически разложен (#6/#12).

    Модуль можно безопасно положить под контейнер только если контейнер уже
    мигрирован в `_storage/<container>/` (его storage существует ИЛИ его
    container_logical — существующий junction). Иначе создание junction модуля
    породит фантомную реальную папку контейнера вне storage. Поднимает CliError
    с подсказкой `atlas projects layout init <container>`.
    """
    container = session.get(Project, parent_id)
    if container is None:
        # Висячий parent_id (#16): контейнер удалён.
        raise CliError(
            "container_not_found",
            f"Контейнер parent_id='{parent_id}' не найден в БД "
            f"(висячий parent_id). Создание модуля невозможно.",
        )
    storage = get_storage_path(container.slug, root=root)
    container_migrated = storage.exists()
    # Контейнерная логическая папка существует (junction ИЛИ реальная папка
    # контейнера). Ключевой инвариант #6: фантом создаётся ТОЛЬКО когда
    # container_logical НЕ существует и мы делаем mkdir его modules/. Если
    # папка контейнера уже есть — модуль ляжет внутрь неё, расщепления нет.
    container_logical_present = (
        container_logical is not None
        and (container_logical.exists() or os.path.islink(str(container_logical)))
    )
    if not (container_migrated or container_logical_present):
        raise CliError(
            "container_not_laid_out",
            f"Контейнер '{container.slug}' ещё не разложен физически "
            f"(нет ни _storage/{container.slug}/, ни его папки "
            f"{container_logical}). Сначала выполните "
            f"`atlas projects layout init {container.slug}`, затем повторите "
            f"создание модуля. (Иначе модуль уехал бы в фантомную папку.)",
        )


def _maybe_local_git_init(storage_path: Path) -> bool:
    """Поднять ЛОКАЛЬНЫЙ git-репо модуля в его storage (#9/#13/#127).

    Идемпотентно: если `.git/` уже существует — noop (False). Без remote/push.
    Best-effort: если git недоступен или init упал — возвращает False (не
    роняем add). Возвращает True только если репозиторий реально создан.
    """
    try:
        if not storage_path.exists():
            return False
        if (storage_path / ".git").exists():
            return False
        result = subprocess.run(
            ["git", "init"],
            cwd=str(storage_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_gitignore_modules(local_path: Path) -> bool:
    """Идемпотентно добавить ``modules/`` в `.gitignore` контейнера (#127).

    Контейнер-monorepo трекает всё КРОМЕ modules/ (каждый модуль — свой git-репо
    со своим backup). Возвращает True, если файл был создан/дополнен, False —
    если ``modules/`` уже игнорируется.
    """
    gitignore = local_path / ".gitignore"
    marker = "modules/"
    # #10: эквивалентные по смыслу записи считаем уже игнорирующими modules/,
    # чтобы не дописывать дубль-блок при повторных прогонах.
    equivalent_markers = {"modules/", "/modules/", "modules", "modules/*"}
    if gitignore.exists():
        try:
            existing = gitignore.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        # Уже есть эквивалентная запись → noop.
        lines = [ln.strip() for ln in existing.splitlines()]
        if any(ln in equivalent_markers for ln in lines):
            return False
        suffix = "" if existing.endswith("\n") or existing == "" else "\n"
        block = f"{suffix}\n# === atlas: модули контейнера живут в своих git-репо ===\n{marker}\n"
        gitignore.write_text(existing + block, encoding="utf-8")
        return True

    local_path.mkdir(parents=True, exist_ok=True)
    gitignore.write_text(
        "# === atlas: модули контейнера живут в своих git-репо ===\n"
        f"{marker}\n",
        encoding="utf-8",
    )
    return True


def _generate_unique_prefix(
    session: Session,
    base: str,
    *,
    max_attempts: int = 100,
) -> str:
    """Авто-prefix с числовым суффиксом: cf, cf2, cf3, ...

    Отдельная функция от ``generate_unique_slug`` потому что суффикс цифровой
    без дефиса (prefix не имеет дефисов по контракту PREFIX_RE).
    """
    exists = _prefix_exists_fn(session)
    if not exists(base):
        return base
    for n in range(2, max_attempts + 1):
        candidate = f"{base}{n}"
        # суффикс может перевалить за 5 chars — если так, обрезаем base
        if len(candidate) > 5:
            trimmed_base = base[: 5 - len(str(n))]
            candidate = f"{trimmed_base}{n}"
        if not exists(candidate):
            return candidate
    raise SlugGenerationError(
        f"Не удалось подобрать уникальный prefix на основе '{base}' "
        f"за {max_attempts} попыток"
    )


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


@projects_app.command("init")
def init_cmd(
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="URL БД (override env ATLAS_DB_URL и default)"
    ),
) -> None:
    """Инициализировать PM-БД: apply migrations + seed справочников."""
    url = db_url or _db_url()
    emit_message(f"Database: {url}")

    emit_message("1. Применяю миграции Alembic...")
    from atlas.db import run_migrations
    try:
        run_migrations(url)  # программно; работает и из pip/uvx-пакета (#880)
    except Exception as exc:  # noqa: BLE001 — показать причину, не «голый» traceback
        console.print("[red]Ошибка миграций:[/red]")
        console.print(str(exc))
        raise typer.Exit(code=1)
    emit_message("✓ миграции применены")

    emit_message(
        "2. Заселяю справочники (project_types, project_statuses, participants, tags)..."
    )
    engine = make_engine(url)
    with make_session(engine) as session:
        counts = seed_all(session)
    tags_counts = counts.get("tags", {"created": 0, "skipped": 0})

    def _render(d: dict[str, Any]) -> None:
        c = d["counts"]
        tc = d["tags"]
        console.print(
            f"[green]   ✓ project_types={c['project_types']}, "
            f"project_statuses={c['project_statuses']}, "
            f"participants={c['participants']}[/green]"
        )
        console.print(
            f"[green]   ✓ Tags: created {tc['created']}, "
            f"skipped {tc['skipped']}[/green]"
        )
        console.print("[bold green]Готово.[/bold green] PM-БД инициализирована.")

    emit_data(
        {
            "database": url,
            "initialized": True,
            "counts": {
                "project_types": counts["project_types"],
                "project_statuses": counts["project_statuses"],
                "participants": counts["participants"],
            },
            "tags": {"created": tags_counts["created"], "skipped": tags_counts["skipped"]},
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@projects_app.command("add")
def add_cmd(
    name: str = typer.Option(..., "--name", help="Человекочитаемое название проекта"),
    type_slug: Optional[str] = typer.Option(None, "--type", help="Тип проекта. Если не задан — personal-project (личный)."),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="Уникальный slug ([a-z0-9-], 2-50). Если не задан — авто из --name.",
    ),
    prefix: Optional[str] = typer.Option(
        None, "--prefix",
        help="Префикс ([a-z0-9], 1-5). Если не задан — авто из slug.",
    ),
    priority: str = typer.Option("P2", "--priority", help="P0 | P1 | P2 | P3"),
    status_slug: str = typer.Option("experiment", "--status", help="Lifecycle status slug"),
    description: Optional[str] = typer.Option(None, "--description"),
    one_line: Optional[str] = typer.Option(None, "--one-line", help="Краткое описание (1 строка)"),
    deadline: Optional[str] = typer.Option(None, "--deadline", help="ISO-дата YYYY-MM-DD"),
    git_repo_url: Optional[str] = typer.Option(None, "--git-repo-url"),
    local_path: Optional[str] = typer.Option(
        None, "--local-path",
        help=(
            "Путь к проекту. Если относительный — резолвится через "
            "ATLAS_PROJECTS_ROOT. Если не задан — auto-derive по type+slug."
        ),
    ),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t",
        help="Тег: 'slug', 'category:slug' или UUID. Можно несколько раз.",
    ),
    setup_layout: bool = typer.Option(
        True, "--setup-layout/--no-setup-layout",
        help="Создать `_storage/<slug>/` + junction в logical (Products/Tests/...).",
    ),
    canonical: bool = typer.Option(
        True, "--canonical/--no-canonical",
        help="Создать README.md / AGENTS.md / .gitignore по канону (если их ещё нет).",
    ),
    init_git: bool = typer.Option(
        False, "--init-git/--no-init-git",
        help="После create — git init + GitLab repo + push (одна команда).",
    ),
    private: bool = typer.Option(
        True, "--private/--public",
        help="(только при --init-git) видимость GitLab репо.",
    ),
    group: Optional[str] = typer.Option(
        None, "--group",
        help="(только при --init-git) GitLab group path. Если опущен — derive по type/owner.",
    ),
    commit_message: Optional[str] = typer.Option(
        None, "--commit-message",
        help="(только при --init-git) сообщение initial коммита.",
    ),
    team: bool = typer.Option(
        False, "--team",
        help="Командный проект (владелец — организация). По умолчанию — личный.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner",
        help="Slug владельца (по умолчанию — ты). Чужой владелец → командный проект.",
    ),
    no_sync: bool = typer.Option(
        False, "--no-sync",
        help="Не раскладывать в ядро/Notion (создать только в Atlas).",
    ),
    parent: Optional[str] = typer.Option(
        None, "--parent",
        help="Родительский проект-контейнер (slug | UUID). Делает проект модулем.",
    ),
) -> None:
    """Создать новый проект в портфеле.

    Канонический порядок (defaults):
      1. add в БД (всегда).
      2. --setup-layout → `_storage/<slug>/` + junction в Products/Tests/...
      3. --canonical    → README.md / AGENTS.md / .gitignore (если нет).
      4. --init-git     → git init + GitLab create + push (опционально).
    """
    _validate_priority(priority)

    # ----- режим проекта: личный по умолчанию (владелец+lead = ты), флаги переопределяют -----
    from atlas.appconfig import load_config, owner_member_slug
    from atlas.commands._provision import resolve_project_mode
    _cfg_pm = load_config()
    mode = resolve_project_mode(
        type_flag=type_slug, team=team, owner=owner,
        default_owner=owner_member_slug(_cfg_pm.portal_id),
        company_owner=_cfg_pm.team_owner,
    )

    deadline_dt: Optional[datetime] = None
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline)
        except ValueError:
            console.print(
                f"[red]Невалидный deadline '{deadline}': ожидаю YYYY-MM-DD.[/red]"
            )
            raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == mode.type_slug)
        ).scalar_one_or_none()
        if pt is None:
            console.print(
                f"[red]Тип '{mode.type_slug}' не найден. См. `atlas projects types`.[/red]"
            )
            raise typer.Exit(code=1)

        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps is None:
            console.print(
                f"[red]Статус '{status_slug}' не найден. См. `atlas projects statuses`.[/red]"
            )
            raise typer.Exit(code=1)

        # ----- parent (опц.): резолв в id; на add цикл невозможен -----
        parent_id: Optional[str] = None
        if parent is not None:
            parent_proj = _resolve_parent_or_die(session, parent)
            parent_id = parent_proj.id

        # ----- slug -----
        slug_auto = False
        if slug:
            _validate_slug(slug)
            if _slug_exists_fn(session)(slug):
                console.print(
                    f"[red]Slug '{slug}' занят. "
                    f"Попробуйте '{slug}-2' или выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            final_slug = slug
        else:
            base = slugify_text(name)
            if not base:
                console.print(
                    f"[red]Не удалось сгенерировать slug из '{name}': "
                    f"передайте --slug явно.[/red]"
                )
                raise typer.Exit(code=1)
            try:
                final_slug = generate_unique_slug(base, _slug_exists_fn(session))
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            slug_auto = True

        # ----- prefix -----
        prefix_auto = False
        if prefix:
            _validate_prefix(prefix)
            if _prefix_exists_fn(session)(prefix):
                console.print(
                    f"[red]Prefix '{prefix}' занят. Выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            final_prefix = prefix
        else:
            base_prefix = generate_prefix_from_slug(final_slug)
            if not base_prefix:
                console.print(
                    f"[red]Не удалось сгенерировать prefix из slug '{final_slug}': "
                    f"передайте --prefix явно.[/red]"
                )
                raise typer.Exit(code=1)
            try:
                final_prefix = _generate_unique_prefix(session, base_prefix)
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            prefix_auto = True

        # ----- resolve local_path (W45-32m: relative→absolute, auto-derive) -----
        # Module-aware (#163/#126): если задан --parent, логический путь модуля =
        # <container_logical>/modules/<slug>, а не type-группа. Резолвим
        # контейнерный логический путь через parent_id (parent_proj уже найден).
        from atlas.layout import get_logical_path
        root = get_projects_root()

        container_logical: Optional[Path] = None
        if parent_id is not None:
            container_logical = _container_logical_for(session, parent_id, root=root)

        if local_path:
            lp = Path(local_path)
            if not lp.is_absolute():
                lp = (root / lp).resolve()
            resolved_local_path = str(lp)
        else:
            fake_proj = type(
                "P",
                (),
                {
                    "slug": final_slug,
                    "type_slug": mode.type_slug,
                    "archived": False,
                    "archived_group": None,
                    "parent_id": parent_id,
                },
            )()
            try:
                logical = get_logical_path(
                    fake_proj, root=root, container_logical=container_logical
                )
                resolved_local_path = str(logical)
            except Exception:
                resolved_local_path = None

        # ----- create -----
        project = Project(
            slug=final_slug,
            prefix=final_prefix,
            name=name,
            type_id=pt.id,
            status_id=ps.id,
            priority=priority,
            description=description,
            one_line_summary=one_line or "",
            estimated_deadline=deadline_dt,
            git_repo_url=git_repo_url,
            local_path=resolved_local_path,
            parent_id=parent_id,
        )
        session.add(project)
        session.flush()  # получить project.id

        # ----- tags -----
        tag_slugs_for_log: list[str] = []
        if tags:
            resolved_tags = _resolve_tags_or_die(session, tags)
            tag_slugs_for_log = [t.slug for t in resolved_tags]
            attach_tags(session, project.id, [t.id for t in resolved_tags])

        # ----- авто-раскладка (этап 1): политика синка + lead-участник (владелец) -----
        # owner-counterparty проставляется в ЯДРЕ на этапе 2 (provision); в Atlas
        # «владелец» = lead-участник (он руководит проектом и видит все задачи).
        # sync_policy — FK на sync_policies: ставим только если значение существует
        # (в неполных тест-средах справочник может быть не засеян → не роняем add).
        from atlas.models import SyncPolicy
        if session.get(SyncPolicy, mode.sync_policy) is not None:
            project.sync_policy = mode.sync_policy
        lead_p = session.execute(
            select(Participant).where(Participant.slug == mode.lead_slug)
        ).scalar_one_or_none()
        if lead_p is not None and session.get(
            ProjectParticipant, (project.id, lead_p.id)
        ) is None:
            session.add(ProjectParticipant(
                project_id=project.id, participant_id=lead_p.id, role_in_project="lead",
            ))

        details: dict[str, Any] = {
            "slug": final_slug,
            "prefix": final_prefix,
            "name": name,
            "type": mode.type_slug,
            "priority": priority,
            "status": status_slug,
            "visibility": mode.visibility,
            "owner": mode.owner_slug,
            "lead": mode.lead_slug,
        }
        if tag_slugs_for_log:
            details["tags"] = tag_slugs_for_log
        if parent_id is not None:
            details["parent"] = parent_proj.slug

        _log_action(
            session,
            action="project_created",
            entity_id=project.id,
            details=details,
        )
        session.commit()

        # Итоговый результат команды (json-консистентно) собираем в payload;
        # промежуточные шаги (layout/canonical/git/provision) — emit_message
        # (в json-режиме идут в stderr, не засоряя JSON-результат в stdout).
        created_payload: dict[str, Any] = {
            "slug": final_slug,
            "prefix": final_prefix,
            "name": name,
            "type": mode.type_slug,
            "priority": priority,
            "status": status_slug,
            "owner": mode.owner_slug,
            "lead": mode.lead_slug,
            "visibility": mode.visibility,
            "local_path": resolved_local_path,
            "slug_auto": slug_auto,
            "prefix_auto": prefix_auto,
            "storage_path": None,
            "junction_created": False,
            "canonical_files": [],
            "git": None,
            "provisioned": None,
            # #5: self-describing — scripted-consumer видит модуль ли это.
            "parent": parent_proj.slug if parent_id is not None else None,
            "is_module": parent_id is not None,
            # #5: стабильная JSON-форма — ключ всегда есть (null когда N/A).
            "container_gitignore_updated": None,
        }

        if slug_auto:
            emit_message(f"slug auto-generated: {final_slug}")
        if prefix_auto:
            emit_message(f"prefix auto-generated: {final_prefix}")

        # ----- setup_layout: _storage + junction -----
        if setup_layout:
            # #6/#12 SAFETY: модуль можно физически разложить только если
            # контейнер уже мигрирован в _storage (его junction/реальная папка
            # существует). Иначе _setup_storage_and_junction молча создаст
            # ФАНТОМНУЮ реальную папку <type-группа>/<container>/modules/ вне
            # storage контейнера, расщепив его содержимое. Отвергаем заранее.
            if parent_id is not None:
                _require_container_laid_out(
                    session, parent_id, container_logical, root=root
                )
            try:
                _, storage_path, junction_created = _setup_storage_and_junction(
                    final_slug, mode.type_slug,
                    parent_id=parent_id,
                    container_logical=container_logical,
                )
                created_payload["storage_path"] = str(storage_path)
                created_payload["junction_created"] = junction_created
                emit_message(f"Storage: {storage_path}")
                if junction_created:
                    emit_message(
                        f"Junction: {resolved_local_path} → {storage_path}"
                    )
                # #127: модуль появился у контейнера → его .gitignore трекает
                # всё КРОМЕ modules/ (каждый модуль — свой git-репо). Идемпотентно.
                if parent_id is not None and container_logical is not None:
                    try:
                        if _ensure_gitignore_modules(container_logical):
                            created_payload["container_gitignore_updated"] = str(
                                container_logical / ".gitignore"
                            )
                            emit_message(
                                f"Container .gitignore += modules/ "
                                f"({container_logical / '.gitignore'})"
                            )
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        emit_message(
                            f"⚠ container .gitignore update failed: {exc}",
                            level="warn",
                        )
            except Exception as exc:
                emit_message(f"⚠ setup_layout failed: {exc}", level="warn")

            # #9/#13: модуль = свой git-репо (#127). Контейнер игнорирует
            # modules/, поэтому каждый модуль обязан иметь СОБСТВЕННЫЙ репозиторий
            # в _storage/<module>/. Поднимаем ЛОКАЛЬНЫЙ git init без ручной работы
            # (без GitLab/push — это делает явный --init-git). Идемпотентно: если
            # .git уже есть или git недоступен — тихо пропускаем.
            if (
                parent_id is not None
                and not init_git
                and created_payload.get("storage_path")
            ):
                module_git = _maybe_local_git_init(
                    Path(created_payload["storage_path"])
                )
                created_payload["module_git_initialized"] = module_git
                if module_git:
                    emit_message(
                        f"✓ Module git initialized (local): "
                        f"{created_payload['storage_path']}"
                    )

        # ----- canonical files -----
        if canonical and resolved_local_path:
            try:
                local_p = Path(resolved_local_path)
                local_p.mkdir(parents=True, exist_ok=True)
                logical_rel = (
                    str(local_p.relative_to(root))
                    if str(local_p).startswith(str(root))
                    else str(local_p)
                )
                created_files = _create_canonical_files(
                    local_p,
                    project=project,
                    type_slug=mode.type_slug,
                    status_slug=status_slug,
                    tag_slugs=tag_slugs_for_log,
                    logical_rel=logical_rel,
                )
                created_payload["canonical_files"] = created_files
                if created_files:
                    emit_message(f"Files: {', '.join(created_files)}")

                # ----- onboarding prompt block (#211) -----
                # Идемпотентно вписываем блок-указатель «пользуйся Atlas» в
                # AGENTS.md (создан из шаблона → уже с блоком; существовавший →
                # дополним) и в CLAUDE.md, но ТОЛЬКО если он уже существует.
                prompted = _ensure_atlas_prompt_in_dir(local_p)
                created_payload["atlas_prompt_files"] = prompted
                if prompted:
                    emit_message(f"Atlas prompt block → {', '.join(prompted)}")
            except Exception as exc:
                emit_message(f"⚠ canonical files failed: {exc}", level="warn")

        # ----- init_git -----
        if init_git:
            from atlas.commands.projects_git import (
                DEFAULT_COMMIT_MESSAGE,
                perform_git_init,
            )

            msg = commit_message or DEFAULT_COMMIT_MESSAGE
            try:
                # Перезагрузим project т.к. session был commit'ed.
                project_for_git = session.execute(
                    select(Project).where(Project.id == project.id)
                ).scalar_one()
                result = perform_git_init(
                    session, project_for_git,
                    group=group,
                    private=private,
                    commit_message=msg,
                    log_action_fn=_log_action,
                )
                session.commit()
                created_payload["git"] = {
                    "url": result["url"],
                    "branch": result["branch"],
                    "group_path": result["group_path"],
                }
                emit_message(
                    f"✓ Git initialized: {result['url']} "
                    f"(branch={result['branch']}, group={result['group_path']})"
                )
            except RuntimeError as exc:
                emit_message(
                    f"✗ Git init failed: {exc}. Проект создан в БД и "
                    f"канонизирован, но без git. Повтор: "
                    f"`atlas projects git init {final_slug}`.",
                    level="warn",
                )

        # Раскладка по ВНЕШНИМ системам (Notion/Б24/порталы) — НЕ дело CLI.
        # Проект создаётся локально. Синхронизацию с backend (когда включена)
        # делает backend-сервис по событиям; он же решает маршрутизацию фанаута во
        # внешние системы. CLI знает только адрес backend, не сами системы.

        def _render(d: dict[str, Any]) -> None:
            console.print(f"[green]✓ Project '{d['slug']}' created[/green]")
            console.print(f"  Name:     {d['name']}")
            console.print(f"  Type:     {d['type']}")
            console.print(f"  Prefix:   {d['prefix']}")
            console.print(f"  Priority: {d['priority']}")
            console.print(f"  Status:   {d['status']}")
            console.print(
                f"  Владелец: {d['owner']}  ·  lead: {d['lead']}  ·  "
                f"{d['visibility']}"
            )
            if d["local_path"]:
                console.print(f"  Path:     {d['local_path']}")
            if d["storage_path"]:
                console.print(f"  Storage:  {d['storage_path']}")
            if d["canonical_files"]:
                console.print(f"  Files:    {', '.join(d['canonical_files'])}")
            if d["git"]:
                console.print(f"  Git:      {d['git']['url']}")

        emit_data(created_payload, text_renderer=_render)


# --------------------------------------------------------------------------- #
# make-personal / link / unlink — правка модели через API ядра (без docker exec) #
# --------------------------------------------------------------------------- #


def _backend_ident(project) -> str:
    """Идентификатор проекта для ядро-API: backend_id (core-id) если связан,
    иначе slug. Ядро резолвит по slug|id|atlas_slug."""
    return project.backend_id or project.slug


@projects_app.command("make-personal")
def make_personal_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
) -> None:
    """Перевести проект в ЛИЧНЫЙ (visibility=personal, владелец+lead=ты) — ядро+Atlas.

    Локально: sync_policy=full + lead-участник. В ядре: PATCH visibility/owner/lead.
    """
    cfg = load_config()
    owner = owner_member_slug(cfg.portal_id)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)
        from atlas.models import SyncPolicy
        if session.get(SyncPolicy, "full") is not None:
            project.sync_policy = "full"
        lead_p = session.execute(
            select(Participant).where(Participant.slug == owner)
        ).scalar_one_or_none()
        if lead_p is not None and session.get(
            ProjectParticipant, (project.id, lead_p.id)
        ) is None:
            session.add(ProjectParticipant(
                project_id=project.id, participant_id=lead_p.id, role_in_project="lead",
            ))
        ident = _backend_ident(project)
        session.commit()
    if not resolve_api_key(cfg):
        emit_data(
            {"ref": ref, "atlas_updated": True, "core_patched": False,
             "reason": "api_key не задан"},
            text_renderer=lambda d: console.print(
                "[yellow]⚠ Atlas обновлён; api_key не задан — ядро не тронуто.[/yellow]"
            ),
        )
        return
    hub = HubService(cfg.base_url, resolve_api_key(cfg))
    try:
        asyncio.run(hub.patch_project(
            ident, visibility="personal",
            owner_slug=owner, lead_slug=owner,
        ))
        emit_data(
            {"ref": ref, "atlas_updated": True, "core_patched": True,
             "visibility": "personal"},
            text_renderer=lambda d: console.print(
                f"[green]✓ '{d['ref']}' переведён в личный (ядро+Atlas).[/green]"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        emit_data(
            {"ref": ref, "atlas_updated": True, "core_patched": False,
             "error": str(exc)},
            text_renderer=lambda d: console.print(
                f"[yellow]⚠ Atlas обновлён; ядро PATCH не удалось: {d['error']}.[/yellow]"
            ),
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@projects_app.command("list")
def list_cmd(
    type_slug: Optional[str] = typer.Option(None, "--type", help="Фильтр: slug типа"),
    status_slug: Optional[str] = typer.Option(None, "--status", help="Фильтр: slug статуса"),
    archived: bool = typer.Option(
        False, "--archived/--no-archived",
        help="Показывать архивные (по умолчанию скрыты)",
    ),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t",
        help="Фильтр по тегу (AND-семантика, можно несколько раз).",
    ),
    parent: Optional[str] = typer.Option(
        None, "--parent",
        help="Только модули этого контейнера (slug | UUID родителя).",
    ),
    standalone: bool = typer.Option(
        False, "--standalone",
        help="Только самостоятельные проекты (без родителя, parent IS NULL).",
    ),
) -> None:
    """Список проектов (--json по умолчанию; --text — таблица)."""
    _PROJECT_LIST_COLUMNS = [
        {"key": "slug", "header": "slug", "style": "cyan", "no_wrap": True},
        {"key": "prefix", "header": "prefix", "style": "dim"},
        {"key": "name", "header": "name"},
        {"key": "type", "header": "type", "style": "magenta"},
        {"key": "status", "header": "status", "style": "green"},
        {"key": "priority", "header": "P", "justify": "center", "style": "bold"},
        {"key": "last_touched", "header": "last touched", "style": "dim"},
    ]

    if parent is not None and standalone:
        console.print(
            "[red]--parent и --standalone взаимоисключающи.[/red]"
        )
        raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    # AND-фильтр по тегам отдельной функцией.
    # Если есть теги — сначала получаем id'шники проходящих, потом
    # добавляем их в общий запрос как фильтр.
    tag_project_ids: Optional[set[str]] = None
    if tags:
        engine_tmp = engine
        with make_session(engine_tmp) as session_tmp:
            # Резолвим каждый tag ref и собираем фактические slug'и.
            resolved_tags = _resolve_tags_or_die(session_tmp, tags)
            resolved_slugs = [t.slug for t in resolved_tags]
            matching = filter_projects_by_tags(
                session_tmp, resolved_slugs, archived=archived,
            )
            tag_project_ids = {p.id for p in matching}
        if not tag_project_ids:
            emit_table(
                [],
                columns=_PROJECT_LIST_COLUMNS,
                title="Projects (0)",
                empty_message="Проектов не найдено.",
            )
            return

    with make_session(engine) as session:
        stmt = select(
            Project.slug,
            Project.prefix,
            Project.name,
            Project.priority,
            Project.last_touched_at,
            Project.archived_at,
            ProjectType.slug.label("type_slug"),
            ProjectStatus.slug.label("status_slug"),
        ).join(
            ProjectType, Project.type_id == ProjectType.id
        ).join(
            ProjectStatus, Project.status_id == ProjectStatus.id
        ).order_by(Project.priority, Project.name)

        if type_slug:
            stmt = stmt.where(ProjectType.slug == type_slug)
        if status_slug:
            stmt = stmt.where(ProjectStatus.slug == status_slug)
        if not archived:
            stmt = stmt.where(Project.archived_at.is_(None))
        if tag_project_ids is not None:
            stmt = stmt.where(Project.id.in_(tag_project_ids))
        if parent is not None:
            parent_proj = _resolve_parent_or_die(session, parent)
            stmt = stmt.where(Project.parent_id == parent_proj.id)
        if standalone:
            stmt = stmt.where(Project.parent_id.is_(None))

        rows = session.execute(stmt).all()

    data = [
        {
            "slug": row.slug,
            "prefix": row.prefix,
            "name": row.name,
            "type": row.type_slug,
            "status": row.status_slug,
            "priority": row.priority,
            "last_touched": (
                row.last_touched_at.strftime("%Y-%m-%d")
                if row.last_touched_at else None
            ),
            "archived": row.archived_at is not None,
        }
        for row in rows
    ]
    emit_table(
        data,
        columns=_PROJECT_LIST_COLUMNS,
        title=f"Projects ({len(data)})",
        empty_message="Проектов не найдено.",
    )


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@projects_app.command("get")
@command
def get_cmd(
    ref: str = typer.Argument(..., help="slug | full UUID | short UUID prefix (≥ 7 chars)"),
) -> None:
    """Показать карточку проекта (--json по умолчанию; --text — карточка)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        pt = session.get(ProjectType, project.type_id)
        ps = session.get(ProjectStatus, project.status_id)

        # parent (если проект — модуль): slug + name
        parent_info: Optional[dict[str, Any]] = None
        if project.parent_id is not None:
            parent_proj = session.get(Project, project.parent_id)
            if parent_proj is not None:
                parent_info = {"slug": parent_proj.slug, "name": parent_proj.name}

        # modules (если проект — контейнер): дети с типом
        module_rows = session.execute(
            select(
                Project.slug,
                Project.name,
                ProjectType.slug.label("type_slug"),
            )
            .join(ProjectType, Project.type_id == ProjectType.id)
            .where(Project.parent_id == project.id)
            .order_by(Project.name)
        ).all()
        modules_info = [
            {"slug": r.slug, "name": r.name, "type": r.type_slug}
            for r in module_rows
        ]

        # участники
        link_rows = session.execute(
            select(ProjectParticipant, Participant)
            .join(Participant, ProjectParticipant.participant_id == Participant.id)
            .where(ProjectParticipant.project_id == project.id)
        ).all()

        # теги
        project_tags = list_project_tags(session, project.id)

        # последние записи action_log
        log_rows = session.execute(
            select(ActionLog)
            .where(ActionLog.entity_type == "project")
            .where(ActionLog.entity_id == project.id)
            .order_by(ActionLog.timestamp.desc())
            .limit(5)
        ).scalars().all()

    payload: dict[str, Any] = {
        "id": project.id,
        "slug": project.slug,
        "prefix": project.prefix,
        "name": project.name,
        "type": pt.slug if pt else None,
        "type_name": pt.name if pt else None,
        "status": ps.slug if ps else None,
        "status_name": ps.name if ps else None,
        "priority": project.priority,
        "description": project.description,
        "one_line_summary": project.one_line_summary,
        "estimated_deadline": (
            project.estimated_deadline.strftime("%Y-%m-%d")
            if project.estimated_deadline else None
        ),
        "git_repo_url": project.git_repo_url,
        "local_path": project.local_path,
        "created_at": (
            project.created_at.isoformat() if project.created_at else None
        ),
        "updated_at": (
            project.updated_at.isoformat() if project.updated_at else None
        ),
        "last_touched_at": (
            project.last_touched_at.isoformat() if project.last_touched_at else None
        ),
        "archived_at": (
            project.archived_at.isoformat() if project.archived_at else None
        ),
        "parent": parent_info,
        "modules": modules_info,
        "participants": [
            {
                "name": p.name,
                "slug": p.slug,
                "role": link.role_in_project,
                "allocated_weekly_hours": link.allocated_weekly_hours,
            }
            for link, p in link_rows
        ],
        "tags": [
            {
                "slug": t.slug,
                "category": t.category,
                "name": t.name,
                "color": t.color,
            }
            for t in project_tags
        ],
        "recent_activity": [
            {
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "action": entry.action,
            }
            for entry in log_rows
        ],
    }

    def _render(d: dict[str, Any]) -> None:
        archived_marker = ""
        if d["archived_at"]:
            archived_marker = f"  ARCHIVED ({d['archived_at'][:10]})"
        print(f"{d['slug']}  — {d['name']}{archived_marker}")
        print(f"  ID:        {d['id']}")
        print(f"  Prefix:    {d['prefix'] or '—'}")
        if d["type"]:
            print(f"  Type:      {d['type']} ({d['type_name']})")
        if d["status"]:
            print(f"  Status:    {d['status']} ({d['status_name']})")
        print(f"  Priority:  {d['priority']}")
        if d["description"]:
            print(f"  Description: {d['description']}")
        if d["one_line_summary"]:
            print(f"  One-line:  {d['one_line_summary']}")
        if d["estimated_deadline"]:
            print(f"  Deadline:  {d['estimated_deadline']}")
        if d["git_repo_url"]:
            print(f"  Git:       {d['git_repo_url']}")
        if d["local_path"]:
            print(f"  Path:      {d['local_path']}")
        if d["parent"] is not None:
            print(f"  Parent:    {d['parent']['slug']} ({d['parent']['name']})")
        print(f"  Created:   {d['created_at']}")
        print(f"  Updated:   {d['updated_at']}")
        if d["last_touched_at"]:
            print(f"  Touched:   {d['last_touched_at']}")

        if d["modules"]:
            print(f"\nModules ({len(d['modules'])}):")
            for m in d["modules"]:
                print(f"  • {m['slug']} — {m['name']} {m['type']}")

        if d["participants"]:
            print("\nParticipants:")
            for p in d["participants"]:
                hours = (
                    f", {p['allocated_weekly_hours']}h/нед"
                    if p["allocated_weekly_hours"] else ""
                )
                print(f"  • {p['name']} — {p['role']}{hours}")
        else:
            print("\nParticipants: —")

        if d["tags"]:
            print("\nTags:")
            for tag in d["tags"]:
                print(
                    f"  • {tag['category']} / {tag['slug']} — "
                    f"{tag['name']} ({tag['color'] or '—'})"
                )
        else:
            print("\nTags: —")

        if d["recent_activity"]:
            print("\nRecent activity:")
            for entry in d["recent_activity"]:
                ts = entry["timestamp"][:16].replace("T", " ") if entry["timestamp"] else "—"
                print(f"  • {ts} — {entry['action']}")

    emit_data(payload, text_renderer=_render)


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@projects_app.command("update")
def update_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    name: Optional[str] = typer.Option(None, "--name"),
    priority: Optional[str] = typer.Option(None, "--priority", help="P0 | P1 | P2 | P3"),
    status_slug: Optional[str] = typer.Option(None, "--status"),
    description: Optional[str] = typer.Option(None, "--description"),
    one_line: Optional[str] = typer.Option(None, "--one-line"),
    deadline: Optional[str] = typer.Option(None, "--deadline", help="YYYY-MM-DD"),
    git_repo_url: Optional[str] = typer.Option(
        None, "--git-repo-url",
        help="Legacy alias для --git-remote-url (W45-32k: синхронизирует оба поля).",
    ),
    git_remote_url: Optional[str] = typer.Option(
        None, "--git-remote-url",
        help="URL git remote (новое поле git_remote_url). Синхронизирует и legacy git_repo_url.",
    ),
    local_path: Optional[str] = typer.Option(None, "--local-path"),
    prefix: Optional[str] = typer.Option(None, "--prefix"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="ЗАПРЕЩЕНО менять slug — это часть task IDs. Используй delete + add.",
    ),
    parent: Optional[str] = typer.Option(
        None, "--parent",
        help="Сделать проект модулем контейнера (slug | UUID). Защита от цикла.",
    ),
    no_parent: bool = typer.Option(
        False, "--no-parent",
        help="Отвязать проект от родителя (parent IS NULL). Взаимоисключает --parent.",
    ),
) -> None:
    """Обновить поля проекта (любые, кроме slug)."""
    if parent is not None and no_parent:
        console.print(
            "[red]--parent и --no-parent взаимоисключающи.[/red]"
        )
        raise typer.Exit(code=1)

    if slug is not None:
        console.print(
            "[red]Изменение slug запрещено: slug участвует в task IDs. "
            "Если действительно нужно — `delete` + `add`.[/red]"
        )
        raise typer.Exit(code=1)

    if priority is not None:
        _validate_priority(priority)
    if prefix is not None:
        _validate_prefix(prefix)

    deadline_dt: Optional[datetime] = None
    if deadline is not None:
        try:
            deadline_dt = datetime.fromisoformat(deadline)
        except ValueError:
            console.print(f"[red]Невалидный deadline '{deadline}'.[/red]")
            raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(project, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(project, field, new_value)

        _maybe_update("name", name)
        _maybe_update("priority", priority)
        _maybe_update("description", description)
        _maybe_update("one_line_summary", one_line)
        _maybe_update("estimated_deadline", deadline_dt)

        # W45-32c+k: --git-remote-url и legacy --git-repo-url
        # синхронизируются. Любой из них обновляет ОБА поля БД.
        unified_git_url = git_remote_url or git_repo_url
        if unified_git_url is not None:
            _maybe_update("git_remote_url", unified_git_url)
            _maybe_update("git_repo_url", unified_git_url)

        # W45-32m: --local-path принимает относительный → resolve через
        # ATLAS_PROJECTS_ROOT (или ~/Documents/PROJECT).
        if local_path is not None:
            lp = Path(local_path)
            if not lp.is_absolute():
                lp = (get_projects_root() / lp).resolve()
            _maybe_update("local_path", str(lp))

        if status_slug is not None:
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_slug)
            ).scalar_one_or_none()
            if ps is None:
                console.print(
                    f"[red]Статус '{status_slug}' не найден.[/red]"
                )
                raise typer.Exit(code=1)
            if project.status_id != ps.id:
                # сохраним slug в diff (читабельнее, чем UUID)
                old_status = session.get(ProjectStatus, project.status_id)
                diffs["status"] = {
                    "old": old_status.slug if old_status else None,
                    "new": status_slug,
                }
                project.status_id = ps.id

        if prefix is not None and project.prefix != prefix:
            if _prefix_exists_fn(session)(prefix):
                console.print(
                    f"[red]Prefix '{prefix}' занят. Выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            diffs["prefix"] = {"old": project.prefix, "new": prefix}
            project.prefix = prefix

        # ----- parent / no-parent -----
        # #2/#8/#14: фиксируем смену parent_id, чтобы после commit перенести
        # физический junction в modules/ нового контейнера (или в type-группу
        # при --no-parent) и снять старый junction. local_path тоже обновляем.
        reparent: Optional[dict[str, Any]] = None
        if no_parent:
            if project.parent_id is not None:
                old_parent = session.get(Project, project.parent_id)
                diffs["parent"] = {
                    "old": old_parent.slug if old_parent else project.parent_id,
                    "new": None,
                }
                reparent = {
                    "old_parent_id": project.parent_id,
                    "new_parent_id": None,
                }
                project.parent_id = None
        elif parent is not None:
            new_parent = _resolve_parent_or_die(session, parent)
            if new_parent.id != project.parent_id:
                _check_no_cycle_or_die(
                    session, project_id=project.id, new_parent_id=new_parent.id
                )
                old_parent = (
                    session.get(Project, project.parent_id)
                    if project.parent_id is not None else None
                )
                diffs["parent"] = {
                    "old": old_parent.slug if old_parent else None,
                    "new": new_parent.slug,
                }
                reparent = {
                    "old_parent_id": project.parent_id,
                    "new_parent_id": new_parent.id,
                }
                project.parent_id = new_parent.id

        if not diffs:
            emit_data(
                {"slug": project.slug, "updated": False, "diffs": {}},
                text_renderer=lambda d: console.print("[yellow]Нечего обновлять.[/yellow]"),
            )
            return

        project.last_touched_at = local_now()
        _log_action(
            session,
            action="project_updated",
            entity_id=project.id,
            details=diffs,
        )
        session.commit()

        # #2/#8/#14: re-parent изменил иерархию в БД → синхронизируем физику
        # (junction-aware: переносим junction модуля в modules/ нового контейнера
        # или в type-группу при --no-parent; снимаем старый junction; обновляем
        # local_path). Best-effort: ошибки физики не валят DB-апдейт.
        reparent_result: Optional[dict[str, Any]] = None
        if reparent is not None:
            reparent_result = _reparent_relocate_junction(
                session,
                project,
                old_parent_id=reparent["old_parent_id"],
                new_parent_id=reparent["new_parent_id"],
            )
            session.commit()

        # diffs может содержать не-JSON значения (datetime) → str-нормализуем для вывода.
        diffs_out = {
            field: {"old": str(diff["old"]), "new": str(diff["new"])}
            for field, diff in diffs.items()
        }

        def _render(d: dict[str, Any]) -> None:
            console.print(
                f"[green]✓ Project '{d['slug']}' updated[/green] "
                f"({len(d['diffs'])} field(s))"
            )
            for field, diff in d["diffs"].items():
                console.print(
                    f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
                )

        emit_data(
            {
                "slug": project.slug,
                "updated": True,
                "diffs": diffs_out,
                "reparent": reparent_result,
            },
            text_renderer=_render,
        )


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


OLD_BACKUPS_DIR_NAME = "_old_git_backups"


def _gitlab_full_path_from_remote_url(remote_url: str) -> Optional[str]:
    """Извлечь `namespace/project` из git_remote_url GitLab-репозитория.

    `https://gitlab.com/<full_path>.git` → `<full_path>`.
    Возвращает None, если URL не похож на GitLab.
    """
    if not remote_url:
        return None
    m = re.match(r"^https?://[^/]+/(?P<full>.+?)(?:\.git)?$", remote_url.strip())
    if not m:
        return None
    return m.group("full")


def _hard_delete_physical(
    *,
    slug: str,
    logical: Path,
    storage: Path,
    root: Path,
) -> dict[str, Any]:
    """Удалить junction и переместить `_storage/<slug>/` в `_old_git_backups/`.

    Возвращает отчёт: ``{"junction_removed": bool, "storage_backup": Optional[Path]}``.

    SAFETY:
      - junction удаляется только если это действительно junction (через
        ``remove_junction``, который сам проверяет).
      - storage НЕ удаляется немедленно — переносится в `_old_git_backups/<slug>-deleted-YYYY-MM-DD/`.
        Очистку этой папки пользователь делает вручную (или периодически).
    """
    report: dict[str, Any] = {
        "junction_removed": False,
        "storage_backup": None,
    }

    if logical.exists():
        try:
            if is_junction(logical):
                remove_junction(logical)
                report["junction_removed"] = True
            else:
                console.print(
                    f"  [yellow]⚠ {logical} существует, но это не junction "
                    f"(реальная папка). Пропуск — снимите вручную.[/yellow]"
                )
        except Exception as exc:  # pragma: no cover — defensive
            console.print(f"  [yellow]⚠ junction snimka failed: {exc}[/yellow]")

    if storage.exists():
        backups_root = root / OLD_BACKUPS_DIR_NAME
        backups_root.mkdir(parents=True, exist_ok=True)
        date_tag = datetime.now().strftime("%Y-%m-%d")
        backup = backups_root / f"{slug}-deleted-{date_tag}"
        suffix = 1
        while backup.exists():
            suffix += 1
            backup = backups_root / f"{slug}-deleted-{date_tag}-{suffix}"
        try:
            _perform_storage_move(storage, backup, copy_first=False)
            report["storage_backup"] = backup
        except Exception as exc:
            console.print(
                f"  [red]✗ Не удалось перенести {storage} → {backup}: {exc}[/red]"
            )
            console.print(
                "  [dim]Storage оставлен на месте. Уберите вручную "
                "если нужно.[/dim]"
            )

    return report


def _hard_delete_gitlab(full_path: str) -> bool:
    """Удалить GitLab-репозиторий через `glab repo delete`.

    Возвращает True если удалось (или GitLab вернул 202 Accepted).
    """
    try:
        result = subprocess.run(
            ["glab", "repo", "delete", full_path, "--yes"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        console.print(f"  [red]✗ glab call failed: {exc}[/red]")
        return False
    if result.returncode != 0:
        console.print(
            f"  [red]✗ glab repo delete вернул {result.returncode}: "
            f"{result.stderr.strip()!r}[/red]"
        )
        return False
    out = (result.stdout or "").strip()
    if out:
        console.print(f"  [dim]glab: {out}[/dim]")
    return True


@projects_app.command("delete")
def delete_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    hard: bool = typer.Option(
        False, "--hard",
        help=(
            "Удалить полностью: запись из БД + junction + перенос "
            "_storage/<slug>/ в _old_git_backups/. По умолчанию — soft archive."
        ),
    ),
    keep_files: bool = typer.Option(
        False, "--keep-files",
        help=(
            "(только с --hard) Удалить только запись из БД, оставить "
            "_storage/<slug>/ и junction нетронутыми. Legacy-поведение."
        ),
    ),
    with_gitlab: bool = typer.Option(
        False, "--with-gitlab",
        help=(
            "(только с --hard) Дополнительно удалить GitLab-репозиторий "
            "через `glab repo delete`. Требует второго подтверждения."
        ),
    ),
) -> None:
    """Удалить проект (soft по умолчанию: archived_at, статус не меняется).

    Режимы --hard:
      • без флагов            — БД + junction + физика → _old_git_backups/.
      • --hard --keep-files   — только БД (legacy, junction и физика остаются).
      • --hard --with-gitlab  — БД + физика + GitLab repo (с подтверждением).
    """
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        slug_for_msg = project.slug
        project_id = project.id

        if hard:
            confirmed = typer.confirm(
                f"Физически удалить '{slug_for_msg}'? Это сломает FK у tasks."
            )
            if not confirmed:
                console.print("[yellow]Отменено.[/yellow]")
                raise typer.Exit(code=1)

            # Snapshot нужных полей до session.delete: после удаления
            # detached object потеряет доступ к атрибутам. type_slug/archived
            # больше не нужны явно — _project_logical_for считает их сам, пока
            # project ещё в сессии.
            parent_id = getattr(project, "parent_id", None)
            git_remote_url = getattr(project, "git_remote_url", None) or getattr(
                project, "git_repo_url", None
            )

            root = get_projects_root()
            storage = get_storage_path(slug_for_msg, root=root)
            # #3: module-aware. Для модуля реальный junction живёт в
            # <container>/modules/<slug> — считаем logical с учётом parent,
            # ПОКА project ещё в сессии (нужен type_id/parent_id для резолва).
            try:
                logical = _project_logical_for(
                    session, project, parent_id=parent_id, root=root
                )
            except Exception:
                logical = None

            _log_action(
                session,
                action="project_hard_deleted",
                entity_id=project_id,
                details={
                    "slug": slug_for_msg,
                    "keep_files": keep_files,
                    "with_gitlab": with_gitlab,
                },
            )
            session.delete(project)
            session.commit()
            emit_message(f"✗ Project '{slug_for_msg}' удалён из БД.", level="warn")

            result_payload: dict[str, Any] = {
                "slug": slug_for_msg,
                "mode": "hard",
                "deleted": True,
                "keep_files": keep_files,
                "junction_removed": False,
                "storage_backup": None,
                "gitlab_deleted": None,
            }

            if not keep_files:
                if logical is not None:
                    report = _hard_delete_physical(
                        slug=slug_for_msg,
                        logical=logical,
                        storage=storage,
                        root=root,
                    )
                    result_payload["junction_removed"] = report["junction_removed"]
                    result_payload["storage_backup"] = (
                        str(report["storage_backup"])
                        if report["storage_backup"] else None
                    )
                    if report["junction_removed"]:
                        emit_message(f"✓ junction snят: {logical}")
                    if report["storage_backup"]:
                        emit_message(
                            f"✓ storage перенесён: {report['storage_backup']}"
                        )
                    if (
                        not report["junction_removed"]
                        and report["storage_backup"] is None
                    ):
                        emit_message(
                            "ни junction, ни _storage/ не найдены — "
                            "ничего физически не было"
                        )
                else:
                    emit_message(
                        "⚠ logical_path не вычислился — физика не тронута",
                        level="warn",
                    )
            else:
                emit_message("--keep-files: junction и _storage/ оставлены")

            if with_gitlab:
                full_path = _gitlab_full_path_from_remote_url(git_remote_url or "")
                if not full_path:
                    emit_message(
                        "⚠ git_remote_url отсутствует — GitLab repo не удаляется",
                        level="warn",
                    )
                else:
                    confirmed_gl = typer.confirm(
                        f"Удалить GitLab-репозиторий '{full_path}'? "
                        "Это destructive (~7 дней grace period)."
                    )
                    if confirmed_gl:
                        if _hard_delete_gitlab(full_path):
                            result_payload["gitlab_deleted"] = True
                            emit_message(
                                f"✓ GitLab repo '{full_path}' queued for deletion"
                            )
                    else:
                        result_payload["gitlab_deleted"] = False
                        emit_message(
                            "GitLab repo оставлен (отменено)", level="warn"
                        )

            emit_data(
                result_payload,
                text_renderer=lambda d: console.print(
                    f"[red]✗ Project '{d['slug']}' удалён из БД.[/red]"
                ),
            )
            return

        if project.archived_at is not None:
            emit_data(
                {
                    "slug": slug_for_msg,
                    "mode": "soft",
                    "archived": True,
                    "already_archived": True,
                },
                text_renderer=lambda d: console.print(
                    f"[yellow]Project '{d['slug']}' уже archived.[/yellow]"
                ),
            )
            return

        project.archived_at = local_now()
        _log_action(
            session,
            action="project_archived",
            entity_id=project_id,
            details={"slug": slug_for_msg, "at": project.archived_at.isoformat()},
        )
        session.commit()
        emit_data(
            {
                "slug": slug_for_msg,
                "mode": "soft",
                "archived": True,
                "already_archived": False,
            },
            text_renderer=lambda d: console.print(
                f"[green]✓ Project '{d['slug']}' archived[/green]"
            ),
        )


# --------------------------------------------------------------------------- #
# project tag: add / rm (attach/detach тега к проекту)                        #
# --------------------------------------------------------------------------- #


@project_tag_app.command("add")
def add_tags_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    tags: list[str] = typer.Option(
        ..., "--tag", "-t",
        help="Тег (можно несколько --tag).",
    ),
) -> None:
    """Прикрепить теги к проекту (идемпотентно)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        resolved = _resolve_tags_or_die(session, tags)
        slugs = [t.slug for t in resolved]
        added = attach_tags(session, project.id, [t.id for t in resolved])

        _log_action(
            session,
            action="project_tags_added",
            entity_id=project.id,
            details={"tag_slugs": slugs, "added": added},
        )
        session.commit()

        emit_data(
            {"slug": project.slug, "attached": added, "tags": slugs},
            text_renderer=lambda d: console.print(
                f"[green]✓ Project '{d['slug']}': attached {d['attached']} "
                f"tag(s) ({', '.join(d['tags'])})[/green]"
            ),
        )


@project_tag_app.command("rm")
def remove_tags_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    tags: list[str] = typer.Option(
        ..., "--tag", "-t",
        help="Тег (можно несколько --tag).",
    ),
) -> None:
    """Открепить теги от проекта (graceful — отсутствующая связь игнорируется)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        resolved = _resolve_tags_or_die(session, tags)
        slugs = [t.slug for t in resolved]
        removed = detach_tags(session, project.id, [t.id for t in resolved])

        _log_action(
            session,
            action="project_tags_removed",
            entity_id=project.id,
            details={"tag_slugs": slugs, "removed": removed},
        )
        session.commit()

        emit_data(
            {"slug": project.slug, "detached": removed, "tags": slugs},
            text_renderer=lambda d: console.print(
                f"[green]✓ Project '{d['slug']}': detached {d['detached']} "
                f"tag(s) ({', '.join(d['tags'])})[/green]"
            ),
        )


# --------------------------------------------------------------------------- #
# project member: add / list / rm (F4f: роли в проекте)                       #
# --------------------------------------------------------------------------- #


def _validate_project_member_role(role: str) -> None:
    """Роль участия в проекте — строго из VALID_PROJECT_MEMBER_ROLES (ноль хардкода
    зашитых имён: множество — единый источник правды, совпадает с ядром)."""
    if role not in VALID_PROJECT_MEMBER_ROLES:
        console.print(
            f"[red]Невалидная роль '{role}': допустимы "
            f"{sorted(VALID_PROJECT_MEMBER_ROLES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _resolve_member_or_die(session: Session, ref: str) -> Participant:
    """Резолв участника (slug / UUID / short-prefix) с выводом ошибки и Exit(1).
    Переиспользует _resolve_participant_ref из participants.py."""
    from atlas.commands.participants import _resolve_participant_ref

    try:
        participant = _resolve_participant_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if participant is None:
        console.print(f"[red]Participant '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return participant


@project_member_app.command("add")
def member_add_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    member: str = typer.Option(
        ..., "--member", "-m", "--participant",
        help="slug | UUID участника.",
    ),
    role: str = typer.Option(
        DEFAULT_PROJECT_MEMBER_ROLE, "--role",
        help="Роль в проекте: lead | member (default: member).",
    ),
) -> None:
    """Добавить участника в проект с ролью (lead/member).

    Идемпотентно: PK project_participants = (project_id, participant_id), роль не
    в ключе → повторный `member add` того же участника ОБНОВЛЯЕТ его role_in_project
    (одна роль на участника в проекте), без дубля."""
    _validate_project_member_role(role)
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)
        participant = _resolve_member_or_die(session, member)

        link = session.get(ProjectParticipant, (project.id, participant.id))
        if link is None:
            link = ProjectParticipant(
                project_id=project.id,
                participant_id=participant.id,
                role_in_project=role,
            )
            session.add(link)
        else:
            link.role_in_project = role

        _log_action(
            session,
            action="project_member_added",
            entity_id=project.id,
            details={"participant": participant.slug, "role": role},
        )
        session.commit()

        emit_data(
            {"slug": project.slug, "participant": participant.slug, "role": role},
            text_renderer=lambda d: console.print(
                f"[green]✓ Project '{d['slug']}': участник "
                f"'{d['participant']}' с ролью '{d['role']}'.[/green]"
            ),
        )


@project_member_app.command("list")
def member_list_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
) -> None:
    """Показать участников проекта с их ролями."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)
        slug_for_msg = project.slug
        link_rows = session.execute(
            select(ProjectParticipant, Participant)
            .join(Participant, ProjectParticipant.participant_id == Participant.id)
            .where(ProjectParticipant.project_id == project.id)
        ).all()

    data = [
        {
            "slug": participant.slug,
            "name": participant.name,
            "role": link.role_in_project,
        }
        for link, participant in link_rows
    ]
    emit_table(
        data,
        columns=[
            {"key": "slug", "header": "Slug", "style": "cyan"},
            {"key": "name", "header": "Name"},
            {"key": "role", "header": "Role", "style": "magenta"},
        ],
        empty_message=f"Project '{slug_for_msg}': участников нет.",
    )


@project_member_app.command("rm")
def member_remove_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    member: str = typer.Option(
        ..., "--member", "-m", "--participant",
        help="slug | UUID участника.",
    ),
) -> None:
    """Снять участника с проекта (graceful — нет связи → warning, не ошибка)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)
        participant = _resolve_member_or_die(session, member)

        link = session.get(ProjectParticipant, (project.id, participant.id))
        if link is None:
            emit_data(
                {
                    "slug": project.slug,
                    "participant": participant.slug,
                    "removed": False,
                },
                text_renderer=lambda d: console.print(
                    f"[yellow]Участник '{d['participant']}' не состоит в проекте "
                    f"'{d['slug']}' — нечего снимать.[/yellow]"
                ),
            )
            return

        session.delete(link)
        _log_action(
            session,
            action="project_member_removed",
            entity_id=project.id,
            details={"participant": participant.slug},
        )
        session.commit()

        emit_data(
            {"slug": project.slug, "participant": participant.slug, "removed": True},
            text_renderer=lambda d: console.print(
                f"[green]✓ Project '{d['slug']}': участник "
                f"'{d['participant']}' снят.[/green]"
            ),
        )


# --------------------------------------------------------------------------- #
# Archive engine helpers                                                      #
# --------------------------------------------------------------------------- #


def _resolve_project_or_die(session: Session, ref: str) -> Project:
    """Resolve project ref с выводом ошибок и typer.Exit."""
    try:
        project = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if project is None:
        console.print(f"[red]Project '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return project


def _resolve_parent_or_die(session: Session, ref: str) -> Project:
    """Резолв parent-ref в Project. Ошибки → typer.Exit(1).

    Сообщение об ошибке упоминает 'parent', чтобы причина была понятна.
    """
    try:
        parent = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]Parent: {exc}[/red]")
        raise typer.Exit(code=1)
    if parent is None:
        console.print(f"[red]Parent '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return parent


def _check_no_cycle_or_die(
    session: Session, *, project_id: str, new_parent_id: str
) -> None:
    """Проверить, что назначение new_parent проекту project_id не создаёт цикл.

    Поднимаемся по цепочке parent_id от нового родителя вверх; если по пути
    встретили сам project_id — цикл (A→…→A). Self-parent (new_parent==project)
    ловится здесь же на первом шаге.
    """
    if new_parent_id == project_id:
        console.print(
            "[red]Проект не может быть родителем самому себе.[/red]"
        )
        raise typer.Exit(code=1)

    seen: set[str] = set()
    cursor: Optional[str] = new_parent_id
    while cursor is not None:
        if cursor == project_id:
            console.print(
                "[red]Смена parent создаст цикл в иерархии проектов "
                "(cycle detected). Отменено.[/red]"
            )
            raise typer.Exit(code=1)
        if cursor in seen:
            # защита от уже существующего цикла в данных (не наш случай).
            break
        seen.add(cursor)
        cursor = session.execute(
            select(Project.parent_id).where(Project.id == cursor)
        ).scalar_one_or_none()


def _status_by_slug_or_die(session: Session, status_slug: str) -> ProjectStatus:
    ps = session.execute(
        select(ProjectStatus).where(ProjectStatus.slug == status_slug)
    ).scalar_one_or_none()
    if ps is None:
        console.print(
            f"[red]Статус '{status_slug}' не найден. См. `atlas statuses list`.[/red]"
        )
        raise typer.Exit(code=1)
    return ps


def _move_folder(src: Path, dst: Path) -> bool:
    """Физически переместить src → dst.

    - Возвращает True если перемещение выполнено; False если src не существует
      (тогда вызывающий продолжит с warning).
    - Создаёт dst.parent через mkdir(parents=True, exist_ok=True).
    - На Windows ``shutil.move`` умеет cross-drive (fallback на copy+delete).
    - Если dst уже существует → ValueError (консистентность — не перезаписываем).
    """
    if not src.exists():
        return False
    if dst.exists():
        raise FileExistsError(
            f"Target уже существует: {dst}. "
            f"Руками проверьте и уберите конфликт."
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


# --------------------------------------------------------------------------- #
# archive                                                                     #
# --------------------------------------------------------------------------- #


@projects_app.command("archive")
def archive_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    status: str = typer.Option(
        ..., "--status",
        help=f"Статус в архиве: {' | '.join(sorted(VALID_ARCHIVE_STATUSES))}",
    ),
    keep_path: bool = typer.Option(
        False, "--keep-path",
        help="Не выполнять физический mv, только БД update.",
    ),
) -> None:
    """Архивировать проект: mv в _Archive/<group>/ + обновить БД.

    Маппинг group: client-project → clients, business-product → products, test → tests,
    personal-utility/personal-project/shared-infrastructure → products.
    """
    if status not in VALID_ARCHIVE_STATUSES:
        console.print(
            f"[red]Невалидный --status '{status}': допустимы "
            f"{sorted(VALID_ARCHIVE_STATUSES)}.[/red]"
        )
        raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if project.archived_at is not None:
            console.print(
                f"[red]Project '{project.slug}' уже archived "
                f"({project.archived_at}). Используйте `unarchive`.[/red]"
            )
            raise typer.Exit(code=1)

        pt = session.get(ProjectType, project.type_id)
        if pt is None:
            console.print("[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        try:
            group = type_slug_to_group(pt.slug)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        target_status = _status_by_slug_or_die(session, status)

        old_local_path = project.local_path
        moved_from: Optional[str] = None
        moved_to: Optional[str] = None
        warning: Optional[str] = None

        # #4: модуль остаётся физически под контейнером (modules/<slug>) даже в
        # архиве — get_logical_path для archived-модуля держит его под
        # container_logical. Поэтому НЕ двигаем junction в _Archive/<group>,
        # меняем только статус в БД.
        is_module = project.parent_id is not None

        if not keep_path and not is_module and project.local_path:
            src = Path(project.local_path)

            # W45-32e: junction-aware archive. Если src — junction (на
            # `_storage/<slug>/` или другой target), не двигаем физику
            # (storage остаётся на месте), а пересоздаём junction в
            # `_Archive/<group>/<slug>/`. Это безопаснее `shutil.move` для
            # symlink/junction на Windows.
            if src.exists() and is_junction(src):
                from atlas.junctions import create_junction, junction_target as _jt

                target = None
                try:
                    target = _jt(src)
                except Exception:
                    pass
                dst = archive_path(root, group, project.slug)
                if dst.exists():
                    console.print(
                        f"[red]Target уже существует: {dst}.[/red]"
                    )
                    raise typer.Exit(code=1)
                try:
                    remove_junction(src)
                except Exception as exc:
                    console.print(
                        f"[red]Не удалось снять junction {src}: {exc}[/red]"
                    )
                    raise typer.Exit(code=1)
                if target is not None:
                    try:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        create_junction(dst, target)
                    except Exception as exc:
                        console.print(
                            f"[red]Не удалось создать junction "
                            f"{dst} → {target}: {exc}[/red]"
                        )
                        raise typer.Exit(code=1)
                moved_from = str(src)
                moved_to = str(dst)
                project.local_path = str(dst)
            else:
                dst = archive_path(root, group, project.slug)
                try:
                    moved = _move_folder(src, dst)
                except FileExistsError as exc:
                    console.print(f"[red]{exc}[/red]")
                    raise typer.Exit(code=1)
                if moved:
                    moved_from = str(src)
                    moved_to = str(dst)
                    project.local_path = str(dst)
                else:
                    warning = (
                        f"Source path '{src}' не существует — продолжаю с БД update."
                    )
                    console.print(f"[yellow]⚠ {warning}[/yellow]")

        # БД-обновления.
        now = local_now()
        project.archived_at = now
        project.archived_group = group
        project.status_id = target_status.id
        project.last_touched_at = now

        details = {
            "status": status,
            "archived_group": group,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "keep_path": keep_path,
        }
        if warning:
            details["warning"] = warning

        _log_action(
            session,
            action="project_archived",
            entity_id=project.id,
            details=details,
        )
        archived_slug = project.slug
        session.commit()

    def _render(d: dict[str, Any]) -> None:
        console.print(
            f"[green]✓ Project '{d['slug']}' archived with status "
            f"'{d['status']}'[/green]"
        )
        if d["moved_from"] and d["moved_to"]:
            console.print(
                f"  Moved: [dim]{d['moved_from']}[/dim] → [bold]{d['moved_to']}[/bold]"
            )
        elif d["keep_path"]:
            console.print("  [dim](--keep-path: физический mv пропущен)[/dim]")
        elif d["old_local_path"]:
            console.print(f"  [dim](src не существовал: {d['old_local_path']})[/dim]")
        else:
            console.print("  [dim](local_path не задан — только БД update)[/dim]")

    emit_data(
        {
            "slug": archived_slug,
            "status": status,
            "archived_group": group,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "keep_path": keep_path,
            "old_local_path": old_local_path,
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# unarchive                                                                   #
# --------------------------------------------------------------------------- #


@projects_app.command("unarchive")
def unarchive_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    status: str = typer.Option(
        "active", "--status",
        help="Статус после unarchive (default: active).",
    ),
    keep_path: bool = typer.Option(
        False, "--keep-path",
        help="Не выполнять физический mv, только БД update.",
    ),
) -> None:
    """Вернуть проект из архива: mv из _Archive/ обратно + status=active."""
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if project.archived_at is None:
            console.print(
                f"[red]Project '{project.slug}' не архивирован.[/red]"
            )
            raise typer.Exit(code=1)

        pt = session.get(ProjectType, project.type_id)
        if pt is None:
            console.print("[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        # Группа для возврата берётся из актуального type (если type_id изменился
        # между archive и unarchive — возвращаемся в новую группу).
        try:
            target_group = type_slug_to_group(pt.slug)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        type_changed_warning = None
        if project.archived_group and project.archived_group != target_group:
            type_changed_warning = (
                f"project_type изменился после archive: "
                f"archived_group='{project.archived_group}', "
                f"новая group='{target_group}'. Возвращаю в новую."
            )
            console.print(f"[yellow]⚠ {type_changed_warning}[/yellow]")

        target_status = _status_by_slug_or_die(session, status)
        old_status = session.get(ProjectStatus, project.status_id)
        old_status_slug = old_status.slug if old_status else None

        moved_from: Optional[str] = None
        moved_to: Optional[str] = None
        warning: Optional[str] = None

        # #4: модуль остаётся под контейнером (modules/<slug>) и при unarchive —
        # junction никуда не двигался при archive, двигать обратно нечего.
        is_module = project.parent_id is not None

        if not keep_path and not is_module and project.local_path:
            src = Path(project.local_path)
            dst = group_path(root, pt.slug, project.slug)
            try:
                moved = _move_folder(src, dst)
            except FileExistsError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if moved:
                moved_from = str(src)
                moved_to = str(dst)
                project.local_path = str(dst)
            else:
                warning = (
                    f"Source path '{src}' не существует — продолжаю с БД update."
                )
                console.print(f"[yellow]⚠ {warning}[/yellow]")

        # БД-обновления.
        now = local_now()
        project.archived_at = None
        project.archived_group = None
        project.status_id = target_status.id
        project.last_touched_at = now

        details = {
            "old_status": old_status_slug,
            "new_status": status,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "keep_path": keep_path,
        }
        if warning:
            details["warning"] = warning
        if type_changed_warning:
            details["type_changed_warning"] = type_changed_warning

        _log_action(
            session,
            action="project_unarchived",
            entity_id=project.id,
            details=details,
        )
        unarchived_slug = project.slug
        session.commit()

    def _render(d: dict[str, Any]) -> None:
        console.print(
            f"[green]✓ Project '{d['slug']}' unarchived to '{d['status']}'[/green]"
        )
        if d["moved_from"] and d["moved_to"]:
            console.print(
                f"  Moved: [dim]{d['moved_from']}[/dim] → [bold]{d['moved_to']}[/bold]"
            )

    emit_data(
        {
            "slug": unarchived_slug,
            "status": status,
            "moved_from": moved_from,
            "moved_to": moved_to,
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# renew                                                                       #
# --------------------------------------------------------------------------- #


@projects_app.command("renew")
def renew_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
) -> None:
    """Инкремент renewal_count для client-project.

    Если проект в архиве — unarchive + status=active + renewal_count++.
    Если активен — только status=active + renewal_count++.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        pt = session.get(ProjectType, project.type_id)
        if pt is None:
            console.print("[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        if pt.slug != "client-project":
            console.print(
                f"[red]renew имеет смысл только для client-project "
                f"(у проекта тип '{pt.slug}'). "
                f"Для остальных используйте `unarchive`.[/red]"
            )
            raise typer.Exit(code=1)

        was_archived = project.archived_at is not None
        count_before = project.renewal_count
        old_status = session.get(ProjectStatus, project.status_id)
        old_status_slug = old_status.slug if old_status else None

        active_status = _status_by_slug_or_die(session, "active")

        moved_from: Optional[str] = None
        moved_to: Optional[str] = None

        if was_archived:
            # Физический mv из _Archive/<group>/ обратно.
            try:
                type_slug_to_group(pt.slug)  # валидация типа (raises ValueError)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)

            if project.local_path:
                src = Path(project.local_path)
                dst = group_path(root, pt.slug, project.slug)
                try:
                    moved = _move_folder(src, dst)
                except FileExistsError as exc:
                    console.print(f"[red]{exc}[/red]")
                    raise typer.Exit(code=1)
                if moved:
                    moved_from = str(src)
                    moved_to = str(dst)
                    project.local_path = str(dst)

            project.archived_at = None
            project.archived_group = None

        project.renewal_count = count_before + 1
        project.status_id = active_status.id
        project.last_touched_at = local_now()

        details = {
            "renewal_count_before": count_before,
            "renewal_count_after": project.renewal_count,
            "was_archived": was_archived,
            "previous_status": old_status_slug,
            "new_status": "active",
            "moved_from": moved_from,
            "moved_to": moved_to,
        }

        _log_action(
            session,
            action="project_renewed",
            entity_id=project.id,
            details=details,
        )
        renewed_slug = project.slug
        renewal_count = project.renewal_count
        session.commit()

    def _render(d: dict[str, Any]) -> None:
        console.print(
            f"[green]✓ Project '{d['slug']}' renewed "
            f"(renewal #{d['renewal_count']})[/green]"
        )
        if d["moved_from"] and d["moved_to"]:
            console.print(
                f"  Moved: [dim]{d['moved_from']}[/dim] → [bold]{d['moved_to']}[/bold]"
            )
        if d["previous_status"] and d["previous_status"] != "active":
            console.print(
                f"  Status: [dim]{d['previous_status']}[/dim] → [bold]active[/bold]"
            )

    emit_data(
        {
            "slug": renewed_slug,
            "renewal_count": renewal_count,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "previous_status": old_status_slug,
            "new_status": "active",
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# move                                                                        #
# --------------------------------------------------------------------------- #


@projects_app.command("move")
def move_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    to_type: str = typer.Option(..., "--to-type", help="Новый project_type.slug"),
) -> None:
    """Сменить project_type проекта + физический mv между группами (если нужно).

    Если старая и новая группы совпадают (e.g. personal-utility → business-product,
    обе → products) — физика не меняется, только БД.
    Для архивного проекта операция запрещена — сначала unarchive.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if project.archived_at is not None:
            console.print(
                f"[red]Project '{project.slug}' archived — сначала `unarchive`, "
                f"потом `move`.[/red]"
            )
            raise typer.Exit(code=1)

        old_type = session.get(ProjectType, project.type_id)
        if old_type is None:
            console.print("[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        new_type = session.execute(
            select(ProjectType).where(ProjectType.slug == to_type)
        ).scalar_one_or_none()
        if new_type is None:
            console.print(
                f"[red]Тип '{to_type}' не найден. См. `atlas types list`.[/red]"
            )
            raise typer.Exit(code=1)

        if old_type.id == new_type.id:
            console.print(
                f"[yellow]Тип уже '{to_type}' — нечего менять.[/yellow]"
            )
            return

        try:
            old_group = type_slug_to_group(old_type.slug)
            new_group = type_slug_to_group(new_type.slug)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        # #7: модуль живёт в <container>/modules/<slug> НЕЗАВИСИМО от type.
        # Смена типа не должна выносить junction в type-группу — иначе
        # рассинхрон БД (parent_id остаётся) ↔ ФС. Для модуля физику не трогаем.
        is_module = project.parent_id is not None

        physical_move = (old_group != new_group) and not is_module
        moved_from: Optional[str] = None
        moved_to: Optional[str] = None

        if physical_move and project.local_path:
            src = Path(project.local_path)
            dst = group_path(root, new_type.slug, project.slug)
            try:
                moved = _move_folder(src, dst)
            except FileExistsError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if moved:
                moved_from = str(src)
                moved_to = str(dst)
                project.local_path = str(dst)

        project.type_id = new_type.id
        project.last_touched_at = local_now()

        details = {
            "old_type": old_type.slug,
            "new_type": new_type.slug,
            "old_group": old_group,
            "new_group": new_group,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "physical_move": physical_move,
        }

        _log_action(
            session,
            action="project_type_changed",
            entity_id=project.id,
            details=details,
        )
        moved_slug = project.slug
        old_type_slug = old_type.slug
        new_type_slug = new_type.slug
        session.commit()

    def _render(d: dict[str, Any]) -> None:
        console.print(
            f"[green]✓ Project '{d['slug']}' type changed: "
            f"[dim]{d['old_type']}[/dim] → [bold]{d['new_type']}[/bold][/green]"
        )
        if d["moved_from"] and d["moved_to"]:
            console.print(
                f"  Moved: [dim]{d['moved_from']}[/dim] → [bold]{d['moved_to']}[/bold]"
            )
        elif not d["physical_move"]:
            console.print(
                f"  [dim](обе группы = '{d['new_group']}' — физика не меняется)[/dim]"
            )

    emit_data(
        {
            "slug": moved_slug,
            "old_type": old_type_slug,
            "new_type": new_type_slug,
            "old_group": old_group,
            "new_group": new_group,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "physical_move": physical_move,
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# reorganize                                                                  #
# --------------------------------------------------------------------------- #


@projects_app.command("reorganize")
def reorganize_cmd(
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply",
        help="По умолчанию --dry-run. --apply выполнит фактические изменения.",
    ),
) -> None:
    """Синхронизировать БД ↔ файловая система.

    Действия:
    - **В sync**: expected существует, local_path == expected → OK.
    - **DB drift**: expected существует, local_path ≠ expected → update local_path.
    - **Physical drift**: local_path существует, expected не существует → mv.
    - **Без local_path**: skip (проект без физики — OK).
    - **Broken**: local_path задан, но ни одно место не существует — warning.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    actions: list[dict[str, Any]] = []

    with make_session(engine) as session:
        projects = session.execute(select(Project)).scalars().all()

        for project in projects:
            pt = session.get(ProjectType, project.type_id)
            if pt is None:
                actions.append({
                    "project_id": project.id,
                    "slug": project.slug,
                    "action": "warn",
                    "reason": "broken type_id",
                })
                continue

            if not project.local_path:
                actions.append({
                    "project_id": project.id,
                    "slug": project.slug,
                    "current": None,
                    "expected": None,
                    "action": "skip",
                })
                continue

            try:
                expected = expected_project_path(
                    root, pt.slug, project.slug,
                    archived=project.archived_at is not None,
                    archived_group=project.archived_group,
                )
            except ValueError:
                actions.append({
                    "project_id": project.id,
                    "slug": project.slug,
                    "action": "warn",
                    "reason": f"unknown type_slug '{pt.slug}'",
                })
                continue

            current = Path(project.local_path)
            current_exists = current.exists()
            expected_exists = expected.exists()
            same_path = (
                current.resolve() == expected.resolve()
                if (current_exists or expected_exists)
                else str(current) == str(expected)
            )

            row: dict[str, Any] = {
                "project_id": project.id,
                "slug": project.slug,
                "current": str(current),
                "expected": str(expected),
            }

            if same_path and expected_exists:
                row["action"] = "ok"
            elif same_path and not expected_exists and not current_exists:
                row["action"] = "warn"
                row["reason"] = "ни current, ни expected не существуют"
            elif not same_path and expected_exists:
                # DB drift: в БД записан не тот путь, но expected есть физически.
                row["action"] = "db-fix"
            elif current_exists and not expected_exists:
                # Physical drift: нужно сделать mv.
                row["action"] = "move"
            else:
                row["action"] = "warn"
                row["reason"] = "неясно состояние"
            actions.append(row)

        # Сводка
        counts = {
            "ok": 0, "db-fix": 0, "move": 0, "skip": 0, "warn": 0,
        }
        for a in actions:
            counts[a.get("action", "warn")] = counts.get(a.get("action", "warn"), 0) + 1

        # Вывод плана (результат) — json-консистентно через emit_table.
        plan_rows = [
            {
                "slug": a["slug"],
                "current_path": (
                    None if a.get("action") == "skip" else a.get("current")
                ),
                "expected_path": (
                    None if a.get("action") == "skip" else a.get("expected")
                ),
                "action": a.get("action", "?"),
                "reason": a.get("reason"),
            }
            for a in actions
        ]
        emit_table(
            plan_rows,
            columns=[
                {"key": "slug", "header": "slug", "style": "cyan"},
                {"key": "current_path", "header": "current_path", "style": "dim"},
                {"key": "expected_path", "header": "expected_path", "style": "bold"},
                {
                    "key": "action",
                    "header": "action",
                    "style": "magenta",
                    "format": lambda v: v or "?",
                },
                {"key": "reason", "header": "reason", "style": "dim"},
            ],
            title=f"Reorganize plan ({len(actions)} projects)",
            empty_message="Нет проектов для реорганизации.",
        )

        emit_message(
            f"Scanned {len(actions)} projects: "
            f"in_sync={counts['ok']}, db_drift={counts['db-fix']}, "
            f"physical={counts['move']}, skipped={counts['skip']}, "
            f"broken={counts['warn']}",
            ok=counts["ok"], db_fix=counts["db-fix"], move=counts["move"],
            skip=counts["skip"], warn=counts["warn"],
        )

        if dry_run:
            emit_message("Dry run. Use --apply to execute.", level="warn")
            return

        # --apply: выполняем изменения.
        any_changed = False
        for a in actions:
            action = a.get("action")
            project_id = a.get("project_id")
            if action == "db-fix":
                proj = session.get(Project, project_id)
                if proj is None:
                    continue
                old = proj.local_path
                proj.local_path = a["expected"]
                _log_action(
                    session,
                    action="project_reorganized",
                    entity_id=proj.id,
                    details={
                        "kind": "db-fix",
                        "old_path": old,
                        "new_path": a["expected"],
                    },
                )
                any_changed = True
            elif action == "move":
                proj = session.get(Project, project_id)
                if proj is None:
                    continue
                src = Path(a["current"])
                dst = Path(a["expected"])
                try:
                    moved = _move_folder(src, dst)
                except FileExistsError as exc:
                    console.print(f"[red]{exc}[/red]")
                    continue
                if moved:
                    proj.local_path = str(dst)
                    _log_action(
                        session,
                        action="project_reorganized",
                        entity_id=proj.id,
                        details={
                            "kind": "move",
                            "old_path": str(src),
                            "new_path": str(dst),
                        },
                    )
                    any_changed = True

        if any_changed:
            session.commit()
            emit_data(
                {"applied": True, "counts": counts},
                text_renderer=lambda d: console.print("[green]✓ Applied.[/green]"),
            )
        else:
            emit_data(
                {"applied": False, "counts": counts},
                text_renderer=lambda d: console.print("[dim]Нечего применять.[/dim]"),
            )


# --------------------------------------------------------------------------- #
# layout sub-app: `atlas projects layout ...`                                 #
# --------------------------------------------------------------------------- #
from atlas.commands.projects_layout import layout_app as _layout_app  # noqa: E402

projects_app.add_typer(
    _layout_app,
    name="layout",
    help="Junction-based layout: `_storage/` + junction-ссылки.",
)


# --------------------------------------------------------------------------- #
# Note: справочники types/statuses вынесены в отдельные top-level subapp:    #
# `atlas types ...` (src/atlas/pm/commands/types.py)                         #
# `atlas statuses ...` (src/atlas/pm/commands/statuses.py)                   #
# --------------------------------------------------------------------------- #
