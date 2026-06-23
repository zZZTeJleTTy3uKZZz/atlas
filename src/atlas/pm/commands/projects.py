"""CLI-команды `atlas projects ...`.

CRUD по проектам портфеля + init + archive engine.

Команды:
- ``init``       — создать БД, применить миграции, seed справочников.
- ``add``        — создать проект (slug/prefix авто или явно).
- ``list``       — список проектов (фильтры по type / status / archived).
- ``get``        — карточка проекта (по slug, full UUID или short UUID prefix).
- ``update``     — изменить поля проекта (любые, кроме slug).
- ``delete``     — soft archive (по умолчанию) или ``--hard`` для физ. удаления.

Archive engine (см. NP-005 ARCHITECTURE.md §2.7, ADR-001):
- ``archive``    — физический mv в ``_Archive/<group>/`` + обновление БД.
- ``unarchive``  — обратный mv + установка статуса (default: active).
- ``renew``      — инкремент renewal_count + опц. unarchive (только client-project).
- ``move``       — сменить project_type, физ. mv если группа другая.
- ``reorganize`` — проверить + починить расхождения БД ↔ файловая система.

Справочники types/statuses вынесены в отдельные top-level subapp
(`atlas types ...`, `atlas statuses ...`) — см. types.py и statuses.py.
"""
from __future__ import annotations

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
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import local_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectParticipant,
    ProjectStatus,
    ProjectTag,
    ProjectType,
    Tag,
)
from atlas.pm.junctions import is_junction, remove_junction
from atlas.pm.layout import (
    _perform_storage_move,
    get_logical_path,
    get_storage_path,
)
from atlas.pm.paths import (
    archive_path,
    expected_project_path,
    get_projects_root,
    group_path,
    type_slug_to_group,
)
from atlas.appconfig import load_config, owner_member_slug, resolve_api_key
from atlas.pm.seeds import seed_all
from atlas.pm.sync.hub_service import HubService
from atlas.pm.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    generate_prefix_from_slug,
    generate_unique_slug,
    resolve_project_ref,
    slugify_text,
)
from atlas.pm.tags import (
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
from atlas.pm.commands.projects_git import git_app as _git_app  # noqa: E402

projects_app.add_typer(_git_app, name="git")

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
DEFAULT_ACTOR_SLUG = "dmitry"

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
    """Получить id участника-актора (Дмитрий) из seed для action_log."""
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

Карточка проекта в Atlas-БД (NP-005):

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

Проект зарегистрирован в Atlas-БД (NP-005). Карточка:

```sh
atlas projects get {slug}
```

Любые изменения метаданных (приоритет, статус, теги) — через atlas CLI:

- `atlas projects update {slug} --priority P0` — поменять приоритет
- `atlas add-tags {slug} -t domain:<slug>` — добавить тег
- `atlas projects move {slug} --to-type <type>` — конвертировать тип

## Тип / Статус (на момент создания)

- type=`{type_slug}`, status=`{status_slug}`, priority=`{priority}`

## Правила работы

- Все исходные тексты, документы, код проекта — в этом репо.
- Чувствительные данные (`.env`, токены, ключи) — игнорируются `.gitignore`.
- AI-ассистенту разрешено: читать, генерировать, редактировать в этом репо.

## Канонические команды

- `atlas projects get {slug}` — карточка проекта
- `atlas pm-tasks list --project {slug}` — задачи проекта (когда W7
  волна будет реализована)
"""

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
) -> tuple[Path, Path, bool]:
    """Создать `_storage/<slug>/` и junction в logical, если нужно.

    Возвращает ``(logical_path, storage_path, junction_created)``.

    NOTE: Если logical уже существует и НЕ junction — оставляем как есть
    (логика migrate-to-storage обработает позднее, через `atlas projects
    layout init`).
    """
    from atlas.pm.layout import (
        get_logical_path,
        get_storage_path,
    )

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
        },
    )()
    logical = get_logical_path(fake_proj, root=root)

    junction_created = False
    if logical.resolve() != storage.resolve():
        if not logical.exists():
            from atlas.pm.junctions import create_junction

            logical.parent.mkdir(parents=True, exist_ok=True)
            create_junction(logical, storage)
            junction_created = True
        elif is_junction(logical):
            current = None
            try:
                from atlas.pm.junctions import junction_target

                current = junction_target(logical)
            except Exception:
                pass
            if current is None or current.resolve() != storage.resolve():
                remove_junction(logical)
                from atlas.pm.junctions import create_junction

                create_junction(logical, storage)
                junction_created = True

    return logical, storage, junction_created


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
    console.print(f"[bold]Database:[/bold] {url}")

    console.print("[cyan]1. Применяю миграции Alembic...[/cyan]")
    env = os.environ.copy()
    env["ATLAS_DB_URL"] = url
    project_root = _find_project_root()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print("[red]Ошибка миграций:[/red]")
        console.print(result.stderr)
        raise typer.Exit(code=1)
    console.print("[green]   ✓ миграции применены[/green]")

    console.print(
        "[cyan]2. Заселяю справочники (project_types, project_statuses, participants, tags)...[/cyan]"
    )
    engine = make_engine(url)
    with make_session(engine) as session:
        counts = seed_all(session)
    tags_counts = counts.get("tags", {"created": 0, "skipped": 0})
    console.print(
        f"[green]   ✓ project_types={counts['project_types']}, "
        f"project_statuses={counts['project_statuses']}, "
        f"participants={counts['participants']}[/green]"
    )
    console.print(
        f"[green]   ✓ Tags: created {tags_counts['created']}, "
        f"skipped {tags_counts['skipped']}[/green]"
    )

    console.print("[bold green]Готово.[/bold green] PM-БД инициализирована.")


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
        help="Командный проект (владелец Цифро.ПРО). По умолчанию — личный (твой).",
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
    from atlas.appconfig import load_config, owner_member_slug, resolve_api_key
    from atlas.pm.commands._provision import resolve_project_mode
    mode = resolve_project_mode(
        type_flag=type_slug, team=team, owner=owner,
        default_owner=owner_member_slug(load_config().portal_id),
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
        from atlas.pm.layout import get_logical_path
        root = get_projects_root()
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
                },
            )()
            try:
                logical = get_logical_path(fake_proj, root=root)
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
        from atlas.pm.models import SyncPolicy
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

        if slug_auto:
            console.print(f"[dim]slug auto-generated: {final_slug}[/dim]")
        if prefix_auto:
            console.print(f"[dim]prefix auto-generated: {final_prefix}[/dim]")

        console.print(f"[green]✓ Project '{final_slug}' created[/green]")
        console.print(f"  Name:     {name}")
        console.print(f"  Type:     {mode.type_slug}")
        console.print(f"  Prefix:   {final_prefix}")
        console.print(f"  Priority: {priority}")
        console.print(f"  Status:   {status_slug}")
        console.print(f"  Владелец: {mode.owner_slug}  ·  lead: {mode.lead_slug}  ·  {mode.visibility}")
        if resolved_local_path:
            console.print(f"  Path:     {resolved_local_path}")

        # ----- setup_layout: _storage + junction -----
        if setup_layout:
            try:
                _, storage_path, junction_created = _setup_storage_and_junction(
                    final_slug, mode.type_slug,
                )
                console.print(f"  Storage:  {storage_path}")
                if junction_created:
                    console.print(
                        f"  Junction: {resolved_local_path} → {storage_path}"
                    )
            except Exception as exc:
                console.print(
                    f"  [yellow]⚠ setup_layout failed: {exc}[/yellow]"
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
                if created_files:
                    console.print(
                        f"  Files:    {', '.join(created_files)}"
                    )
            except Exception as exc:
                console.print(
                    f"  [yellow]⚠ canonical files failed: {exc}[/yellow]"
                )

        # ----- init_git -----
        if init_git:
            from atlas.pm.commands.projects_git import (
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
                console.print(
                    f"  [green]✓ Git initialized[/green]"
                )
                console.print(f"    URL:    {result['url']}")
                console.print(f"    Branch: {result['branch']}")
                console.print(f"    Group:  {result['group_path']}")
            except RuntimeError as exc:
                console.print(f"  [red]✗ Git init failed: {exc}[/red]")
                console.print(
                    f"  [dim]Проект создан в БД и канонизирован, но без git. "
                    f"Можно повторить позже: `atlas projects git init {final_slug}`.[/dim]"
                )

        # ----- авто-раскладка в ядро+Notion (этап 2): если синк включён -----
        # owner-counterparty: личный → 'me' (Дмитрий-person), иначе владелец-компания.
        cfg = load_config()
        if not no_sync and resolve_api_key(cfg):
            notion_kind = "личный" if mode.visibility == "personal" else "клиентский"
            targets = (
                ["notion-pragmat", "atlas-dmitry"] if mode.visibility == "personal"
                else ["b24-exs", "notion-pragmat"]
            )
            core_owner = "me" if mode.owner_slug == "dmitry" else mode.owner_slug
            hub = HubService(cfg.base_url, resolve_api_key(cfg))
            try:
                res = asyncio.run(hub.provision_project(
                    slug=final_slug, name=name, kind="direction",
                    owner_slug=core_owner, lead_slug=mode.lead_slug,
                    visibility=mode.visibility, notion_kind=notion_kind,
                    sync_target_slugs=targets,
                ))
                with make_session(engine) as s2:
                    p2 = s2.get(Project, project.id)
                    if res.get("backend_id"):
                        p2.backend_id = res["backend_id"]
                    if res.get("notion_page_id"):
                        p2.notion_project_id = res["notion_page_id"]
                    s2.commit()
                console.print(
                    f"  [green]✓ Разложен в ядро+Notion[/green] "
                    f"(backend={res.get('backend_id')})"
                )
            except Exception as exc:  # noqa: BLE001 — best-effort, не валим create
                console.print(
                    f"  [yellow]⚠ Локально создан; раскладка в ядро не удалась: {exc}.[/yellow]"
                )


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
        from atlas.pm.models import SyncPolicy
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
        console.print("[yellow]⚠ Atlas обновлён; api_key не задан — ядро не тронуто.[/yellow]")
        return
    hub = HubService(cfg.base_url, resolve_api_key(cfg))
    try:
        asyncio.run(hub.patch_project(
            ident, visibility="personal",
            owner_slug=("me" if owner == "dmitry" else owner), lead_slug=owner,
        ))
        console.print(f"[green]✓ '{ref}' переведён в личный (ядро+Atlas).[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ Atlas обновлён; ядро PATCH не удалось: {exc}.[/yellow]")


@projects_app.command("link")
def link_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    portal: str = typer.Option(..., "--portal", help="slug портала: notion-pragmat | b24-exs | atlas-dmitry"),
    external: str = typer.Option(..., "--external", help="id сущности в портале"),
) -> None:
    """Привязать проект к сущности портала в ядре (entity_link) — без docker exec."""
    cfg = load_config()
    if not resolve_api_key(cfg):
        console.print("[red]Нужен api_key (ATLAS_API_KEY).[/red]")
        raise typer.Exit(code=1)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ident = _backend_ident(_resolve_project_or_die(session, ref))
    hub = HubService(cfg.base_url, resolve_api_key(cfg))
    try:
        asyncio.run(hub.link_project(ident, portal_slug=portal, external_id=external))
        console.print(f"[green]✓ '{ref}' ↔ {portal} ({external}).[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ link не удался: {exc}[/red]")


@projects_app.command("import-b24")
def import_b24_cmd(
    group_id: int = typer.Argument(..., help="ID группы (проекта) в Б24"),
    notion_kind: str = typer.Option(
        "клиентский", "--notion-kind", help="личный | клиентский | компанейский",
    ),
) -> None:
    """Втянуть существующую группу Б24 в ядро+Notion+Atlas (автономность Б24→всё).

    Ядро создаёт core_project + связь↔Б24 + Notion-страницу; локально заводит
    Atlas-проект с backend_id (идемпотентно по backend_id)."""
    cfg = load_config()
    if not resolve_api_key(cfg):
        console.print("[red]Нужен api_key (ATLAS_API_KEY).[/red]")
        raise typer.Exit(code=1)
    owner = owner_member_slug(cfg.portal_id)
    hub = HubService(cfg.base_url, resolve_api_key(cfg))
    try:
        res = asyncio.run(hub.import_from_b24(
            group_id=group_id, notion_kind=notion_kind, lead_slug=owner,
            sync_target_slugs=["b24-exs", "notion-pragmat", "atlas-dmitry"],
        ))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ import из Б24 не удался: {exc}[/red]")
        raise typer.Exit(code=1)

    name = res.get("name") or f"Б24 группа {group_id}"
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        from atlas.pm.models import SyncPolicy
        existing = session.execute(
            select(Project).where(Project.backend_id == res["backend_id"])
        ).scalar_one_or_none()
        if existing is not None:
            console.print(f"[yellow]Уже в Atlas: '{existing.slug}'.[/yellow]")
            return
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == "client-project")
        ).scalar_one()
        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == "active")
        ).scalar_one()
        base = slugify_text(name) or f"b24-{group_id}"
        fslug = generate_unique_slug(base, _slug_exists_fn(session))
        fprefix = _generate_unique_prefix(
            session, generate_prefix_from_slug(fslug) or "imp"
        )
        proj = Project(
            slug=fslug, prefix=fprefix, name=name, type_id=pt.id, status_id=ps.id,
            priority="P2", one_line_summary=f"Импортирован из Б24 (группа #{group_id})",
            backend_id=res["backend_id"], notion_project_id=res.get("notion_page_id"),
        )
        if session.get(SyncPolicy, "media") is not None:
            proj.sync_policy = "media"
        session.add(proj)
        session.flush()
        lead_p = session.execute(
            select(Participant).where(Participant.slug == owner)
        ).scalar_one_or_none()
        if lead_p is not None:
            session.add(ProjectParticipant(
                project_id=proj.id, participant_id=lead_p.id, role_in_project="lead",
            ))
        _log_action(session, action="project_imported_b24", entity_id=proj.id,
                    details={"group_id": group_id, "backend_id": res["backend_id"]})
        result_slug = proj.slug
        session.commit()
    console.print(
        f"[green]✓ Б24-группа #{group_id} импортирована → Atlas '{result_slug}' "
        f"(backend={res['backend_id']}).[/green]"
    )


@projects_app.command("unlink")
def unlink_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    portal: str = typer.Option(..., "--portal", help="slug портала для отвязки"),
) -> None:
    """Снять связь проекта с порталом в ядре (entity_link)."""
    cfg = load_config()
    if not resolve_api_key(cfg):
        console.print("[red]Нужен api_key (ATLAS_API_KEY).[/red]")
        raise typer.Exit(code=1)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ident = _backend_ident(_resolve_project_or_die(session, ref))
    hub = HubService(cfg.base_url, resolve_api_key(cfg))
    try:
        asyncio.run(hub.unlink_project(ident, portal_slug=portal))
        console.print(f"[green]✓ '{ref}' отвязан от {portal}.[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ unlink не удался: {exc}[/red]")


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
    """Список проектов (табличный вывод)."""
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
            console.print("[yellow]Проектов не найдено.[/yellow]")
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

    if not rows:
        console.print("[yellow]Проектов не найдено.[/yellow]")
        return

    table = Table(title=f"Projects ({len(rows)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("prefix", style="dim")
    table.add_column("name")
    table.add_column("type", style="magenta")
    table.add_column("status", style="green")
    table.add_column("P", justify="center", style="bold")
    table.add_column("last touched", style="dim")

    for row in rows:
        last_touched = (
            row.last_touched_at.strftime("%Y-%m-%d") if row.last_touched_at else "—"
        )
        name_display = row.name
        if row.archived_at is not None:
            name_display = f"[strike]{row.name}[/strike] [dim](archived)[/dim]"
        table.add_row(
            row.slug,
            row.prefix or "—",
            name_display,
            row.type_slug,
            row.status_slug,
            row.priority,
            last_touched,
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@projects_app.command("get")
def get_cmd(
    ref: str = typer.Argument(..., help="slug | full UUID | short UUID prefix (≥ 7 chars)"),
    as_json: bool = typer.Option(
        False, "--json",
        help="Вывести карточку как JSON (включая parent и modules).",
    ),
) -> None:
    """Показать карточку проекта."""
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

    # ----- JSON-режим: чистый дамп без rich-разметки -----
    if as_json:
        payload = {
            "id": project.id,
            "slug": project.slug,
            "prefix": project.prefix,
            "name": project.name,
            "type": pt.slug if pt else None,
            "status": ps.slug if ps else None,
            "priority": project.priority,
            "description": project.description,
            "one_line_summary": project.one_line_summary,
            "local_path": project.local_path,
            "archived_at": (
                project.archived_at.isoformat() if project.archived_at else None
            ),
            "parent": parent_info,
            "modules": modules_info,
            "participants": [
                {"name": p.name, "slug": p.slug, "role": link.role_in_project}
                for link, p in link_rows
            ],
            "tags": [
                {"slug": t.slug, "category": t.category, "name": t.name}
                for t in project_tags
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # вывод
    archived_marker = ""
    if project.archived_at is not None:
        archived_marker = (
            f"  [bold red]ARCHIVED[/bold red] "
            f"({project.archived_at.strftime('%Y-%m-%d')})"
        )
    console.print(
        f"[bold cyan]{project.slug}[/bold cyan]  — {project.name}{archived_marker}"
    )
    console.print(f"  ID:        {project.id}")
    console.print(f"  Prefix:    {project.prefix or '—'}")
    if pt:
        console.print(f"  Type:      {pt.slug} ({pt.name})")
    if ps:
        console.print(f"  Status:    {ps.slug} ({ps.name})")
    console.print(f"  Priority:  {project.priority}")
    if project.description:
        console.print(f"  Description: {project.description}")
    if project.one_line_summary:
        console.print(f"  One-line:  {project.one_line_summary}")
    if project.estimated_deadline:
        console.print(f"  Deadline:  {project.estimated_deadline.strftime('%Y-%m-%d')}")
    if project.git_repo_url:
        console.print(f"  Git:       {project.git_repo_url}")
    if project.local_path:
        console.print(f"  Path:      {project.local_path}")
    if parent_info is not None:
        console.print(
            f"  Parent:    {parent_info['slug']} ({parent_info['name']})"
        )
    console.print(f"  Created:   {project.created_at}")
    console.print(f"  Updated:   {project.updated_at}")
    if project.last_touched_at:
        console.print(f"  Touched:   {project.last_touched_at}")

    if modules_info:
        console.print(f"\n[bold]Modules ({len(modules_info)}):[/bold]")
        for m in modules_info:
            console.print(f"  • {m['slug']} — {m['name']} [magenta]{m['type']}[/magenta]")

    if link_rows:
        console.print("\n[bold]Participants:[/bold]")
        for link, participant in link_rows:
            hours = (
                f", {link.allocated_weekly_hours}h/нед"
                if link.allocated_weekly_hours else ""
            )
            console.print(
                f"  • {participant.name} — {link.role_in_project}{hours}"
            )
    else:
        console.print("\n[dim]Participants: —[/dim]")

    if project_tags:
        console.print("\n[bold]Tags:[/bold]")
        tags_table = Table(show_header=True, header_style="bold")
        tags_table.add_column("Category", style="magenta")
        tags_table.add_column("Slug", style="cyan")
        tags_table.add_column("Name")
        tags_table.add_column("Color", style="dim")
        for tag in project_tags:
            tags_table.add_row(
                tag.category,
                tag.slug,
                tag.name,
                tag.color or "—",
            )
        console.print(tags_table)
    else:
        console.print("\n[dim]Tags: —[/dim]")

    if log_rows:
        console.print("\n[bold]Recent activity:[/bold]")
        for entry in log_rows:
            ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
            console.print(f"  • {ts} — {entry.action}")


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
        if no_parent:
            if project.parent_id is not None:
                old_parent = session.get(Project, project.parent_id)
                diffs["parent"] = {
                    "old": old_parent.slug if old_parent else project.parent_id,
                    "new": None,
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
                project.parent_id = new_parent.id

        if not diffs:
            console.print("[yellow]Нечего обновлять.[/yellow]")
            return

        project.last_touched_at = local_now()
        _log_action(
            session,
            action="project_updated",
            entity_id=project.id,
            details=diffs,
        )
        session.commit()

        console.print(
            f"[green]✓ Project '{project.slug}' updated[/green] "
            f"({len(diffs)} field(s))"
        )
        for field, diff in diffs.items():
            console.print(
                f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
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
                f"  [dim]Storage оставлен на месте. Уберите вручную "
                f"если нужно.[/dim]"
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
            # detached object потеряет доступ к атрибутам.
            pt = session.get(ProjectType, project.type_id)
            type_slug = pt.slug if pt is not None else None
            archived_flag = project.archived_at is not None
            archived_group = getattr(project, "archived_group", None)
            git_remote_url = getattr(project, "git_remote_url", None) or getattr(
                project, "git_repo_url", None
            )

            root = get_projects_root()
            storage = get_storage_path(slug_for_msg, root=root)
            try:
                logical = get_logical_path(
                    type(
                        "P",
                        (),
                        {
                            "slug": slug_for_msg,
                            "type_slug": type_slug,
                            "archived": archived_flag,
                            "archived_group": archived_group,
                        },
                    )(),
                    root=root,
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
            console.print(
                f"[red]✗ Project '{slug_for_msg}' удалён из БД.[/red]"
            )

            if not keep_files:
                if logical is not None:
                    report = _hard_delete_physical(
                        slug=slug_for_msg,
                        logical=logical,
                        storage=storage,
                        root=root,
                    )
                    if report["junction_removed"]:
                        console.print(f"  [green]✓ junction snят: {logical}[/green]")
                    if report["storage_backup"]:
                        console.print(
                            f"  [green]✓ storage перенесён: "
                            f"{report['storage_backup']}[/green]"
                        )
                    if (
                        not report["junction_removed"]
                        and report["storage_backup"] is None
                    ):
                        console.print(
                            "  [dim](ни junction, ни _storage/ не найдены — "
                            "ничего физически не было)[/dim]"
                        )
                else:
                    console.print(
                        "  [yellow]⚠ logical_path не вычислился — "
                        "физика не тронута[/yellow]"
                    )
            else:
                console.print(
                    "  [dim](--keep-files: junction и _storage/ оставлены)[/dim]"
                )

            if with_gitlab:
                full_path = _gitlab_full_path_from_remote_url(git_remote_url or "")
                if not full_path:
                    console.print(
                        "  [yellow]⚠ git_remote_url отсутствует — "
                        "GitLab repo не удаляется[/yellow]"
                    )
                else:
                    confirmed_gl = typer.confirm(
                        f"Удалить GitLab-репозиторий '{full_path}'? "
                        "Это destructive (~7 дней grace period)."
                    )
                    if confirmed_gl:
                        if _hard_delete_gitlab(full_path):
                            console.print(
                                f"  [green]✓ GitLab repo '{full_path}' "
                                f"queued for deletion[/green]"
                            )
                    else:
                        console.print(
                            "  [yellow]GitLab repo оставлен (отменено)[/yellow]"
                        )
            return

        if project.archived_at is not None:
            console.print(
                f"[yellow]Project '{slug_for_msg}' уже archived ({project.archived_at}).[/yellow]"
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
        console.print(f"[green]✓ Project '{slug_for_msg}' archived[/green]")


# --------------------------------------------------------------------------- #
# add-tags / remove-tags                                                      #
# --------------------------------------------------------------------------- #


@projects_app.command("add-tags")
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

        console.print(
            f"[green]✓ Project '{project.slug}': attached {added} "
            f"tag(s) ({', '.join(slugs)})[/green]"
        )


@projects_app.command("remove-tags")
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

        console.print(
            f"[green]✓ Project '{project.slug}': detached {removed} "
            f"tag(s) ({', '.join(slugs)})[/green]"
        )


# --------------------------------------------------------------------------- #
# member-add / member-list / member-remove (F4f: роли в проекте)              #
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
    from atlas.pm.commands.participants import _resolve_participant_ref

    try:
        participant = _resolve_participant_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if participant is None:
        console.print(f"[red]Participant '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return participant


@projects_app.command("member-add")
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
    в ключе → повторный member-add того же участника ОБНОВЛЯЕТ его role_in_project
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

        console.print(
            f"[green]✓ Project '{project.slug}': участник "
            f"'{participant.slug}' с ролью '{role}'.[/green]"
        )


@projects_app.command("member-list")
def member_list_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
) -> None:
    """Показать участников проекта с их ролями."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)
        link_rows = session.execute(
            select(ProjectParticipant, Participant)
            .join(Participant, ProjectParticipant.participant_id == Participant.id)
            .where(ProjectParticipant.project_id == project.id)
        ).all()

    if not link_rows:
        console.print(f"[dim]Project '{project.slug}': участников нет.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="magenta")
    for link, participant in link_rows:
        table.add_row(participant.slug, participant.name, link.role_in_project)
    console.print(table)


@projects_app.command("member-remove")
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
            console.print(
                f"[yellow]Участник '{participant.slug}' не состоит в проекте "
                f"'{project.slug}' — нечего снимать.[/yellow]"
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

        console.print(
            f"[green]✓ Project '{project.slug}': участник "
            f"'{participant.slug}' снят.[/green]"
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
            console.print(f"[red]Broken data: project.type_id не найден.[/red]")
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

        if not keep_path and project.local_path:
            src = Path(project.local_path)

            # W45-32e: junction-aware archive. Если src — junction (на
            # `_storage/<slug>/` или другой target), не двигаем физику
            # (storage остаётся на месте), а пересоздаём junction в
            # `_Archive/<group>/<slug>/`. Это безопаснее `shutil.move` для
            # symlink/junction на Windows.
            if src.exists() and is_junction(src):
                from atlas.pm.junctions import create_junction, junction_target as _jt

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
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' archived with status '{status}'[/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")
    elif keep_path:
        console.print("  [dim](--keep-path: физический mv пропущен)[/dim]")
    elif old_local_path:
        console.print(f"  [dim](src не существовал: {old_local_path})[/dim]")
    else:
        console.print("  [dim](local_path не задан — только БД update)[/dim]")


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
            console.print(f"[red]Broken data: project.type_id не найден.[/red]")
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

        if not keep_path and project.local_path:
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
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' unarchived to '{status}'[/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")


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
                target_group = type_slug_to_group(pt.slug)
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
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' renewed "
        f"(renewal #{project.renewal_count})[/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")
    if old_status_slug and old_status_slug != "active":
        console.print(
            f"  Status: [dim]{old_status_slug}[/dim] → [bold]active[/bold]"
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

        physical_move = old_group != new_group
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
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' type changed: "
        f"[dim]{old_type.slug}[/dim] → [bold]{new_type.slug}[/bold][/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")
    elif not physical_move:
        console.print(
            f"  [dim](обе группы = '{new_group}' — физика не меняется)[/dim]"
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

        # Вывод таблицы
        if actions:
            table = Table(title=f"Reorganize plan ({len(actions)} projects)")
            table.add_column("slug", style="cyan")
            table.add_column("current_path", style="dim")
            table.add_column("expected_path", style="bold")
            table.add_column("action", style="magenta")
            for a in actions:
                if a.get("action") == "skip":
                    cur = "—"
                    exp = "—"
                else:
                    cur = a.get("current") or "—"
                    exp = a.get("expected") or "—"
                act = a.get("action", "?")
                if a.get("reason"):
                    act = f"{act} ({a['reason']})"
                table.add_row(a["slug"], cur, exp, act)
            console.print(table)

        console.print(
            f"\nScanned {len(actions)} projects:\n"
            f"  ✓ In sync:      {counts['ok']}\n"
            f"  ⚠ DB drift:     {counts['db-fix']} (will update path in DB)\n"
            f"  🔀 Physical:    {counts['move']} (will move folder)\n"
            f"  • Skipped:      {counts['skip']} (no local_path)\n"
            f"  ⚠ Broken:       {counts['warn']}"
        )

        if dry_run:
            console.print(
                "\n[yellow]Dry run. Use --apply to execute.[/yellow]"
            )
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
            console.print("[green]✓ Applied.[/green]")
        else:
            console.print("[dim]Нечего применять.[/dim]")


# --------------------------------------------------------------------------- #
# layout sub-app: `atlas projects layout ...`                                 #
# --------------------------------------------------------------------------- #
from atlas.pm.commands.projects_layout import layout_app as _layout_app  # noqa: E402

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
