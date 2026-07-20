"""Seed data для PM-БД: project_types, project_statuses, participants.

Запускается один раз при `atlas portfolio init` после первой миграции Alembic.
Безопасен к повторному вызову — использует UPSERT-паттерн по уникальным slug.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import Participant, ProjectStatus, ProjectType, Tag

# --------------------------------------------------------------------------- #
# Seed data                                                                   #
# --------------------------------------------------------------------------- #

# Единый источник правды по типам проектов (канон).
# Раньше тип был размазан по трём хардкод-спискам: PROJECT_TYPES (name/desc/color),
# DEFAULT_SYNC_POLICY_BY_TYPE (policy) и paths.TYPE_TO_GROUP (storage_group).
# Теперь — одна ПОЛНАЯ запись на тип: {slug,name,description,color,
# default_sync_policy,storage_group}. seed_project_types читает merged(base+user).
#
# Поля storage_group: clients|products|tests|inbox (физическая раскладка).
# default_sync_policy: slug из sync_policies (local|epics|media|full).
BASE_PROJECT_TYPES: list[dict[str, str]] = [
    # --- исходные 5 (name/description/color сохранены ДОСЛОВНО) -------------
    {
        "slug": "client-project",
        "name": "Клиентские проекты",
        "description": "Проекты для внешних клиентов",
        "color": "#F97316",
        "storage_group": "clients",
        "default_sync_policy": "full",
    },
    {
        "slug": "business-product",
        "name": "Новые бизнес-продукты",
        "description": "Собственные SaaS / бизнес-продукты",
        "color": "#10B981",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    {
        "slug": "personal-utility",
        "name": "Личные утилиты",
        "description": "Личные dev-утилиты и эксперименты",
        "color": "#8B5CF6",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    {
        "slug": "personal-project",
        "name": "Личные проекты",
        "description": "Собственные инициативы",
        "color": "#EC4899",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    {
        "slug": "shared-infrastructure",
        "name": "Общая инфраструктура",
        "description": "Инструменты/тулкиты, переиспользуемые многими проектами",
        "color": "#6B7280",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    # --- роли-типы (выражают реальные классы портфеля) ---------------------
    {
        "slug": "kit",
        "name": "Kit / SDK-тулкит",
        "description": "Переиспользуемый SDK (BaseX+registry+contract-tests): adapterkit/clikit/librarykit",
        "color": "#0EA5E9",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    {
        "slug": "service",
        "name": "Сервис",
        "description": "Деплоится, состояние, потребляет киты: gateway/bublictr/workerkit",
        "color": "#14B8A6",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    {
        "slug": "superskill",
        "name": "Супернавык",
        "description": "skill+CLI+lib под сервис, dual (шарится+адаптер): notebooklm/yt-uploader",
        "color": "#A855F7",
        "storage_group": "products",
        "default_sync_policy": "epics",
    },
    # --- спец-типы (раньше «фантомы» в маппингах без записи в PROJECT_TYPES) -
    {
        "slug": "test",
        "name": "Тесты / спайки",
        "description": "Короткоживущие тестовые проекты и спайки (Tests/*)",
        "color": "#9CA3AF",
        "storage_group": "tests",
        "default_sync_policy": "local",
    },
    {
        "slug": "inbox",
        "name": "Inbox",
        "description": "Сырой материал на разбор AI (_Inbox/*)",
        "color": "#64748B",
        "storage_group": "inbox",
        "default_sync_policy": "local",
    },
]

# Поля, которые upsert переносит в ProjectType (остальные ключи из toml игнор).
_TYPE_FIELDS = ("slug", "name", "description", "color", "storage_group", "default_sync_policy")
_VALID_GROUPS = ("clients", "products", "tests", "inbox")

PROJECT_STATUSES: list[dict[str, str | int]] = [
    # Канонический набор (W45-39, 2026-04-29). Раньше было 6+ статусов
    # (experiment, active, maintained, dormant, graduating, archived,
    # plus paused/frozen/idea/research/planned добавленные в разных миграциях).
    # Большинство не использовалось — сжали до 5 канонических.
    # Legacy-статусы остаются в таблице (если уже есть в БД), но
    # рекомендованный набор для новых проектов — этот.
    {"slug": "experiment", "name": "Эксперимент", "order_idx": 1,
     "description": "Короткоживущий эксперимент / спайк, 1-30 дней"},
    {"slug": "active", "name": "Активный", "order_idx": 2,
     "description": "В работе, есть цель и критерий завершения"},
    {"slug": "paused", "name": "На паузе", "order_idx": 3,
     "description": "Временно остановлен; есть причина возврата"},
    {"slug": "archived", "name": "Архив", "order_idx": 4,
     "description": "Закрыто, оставлено как history (read-only)"},
    {"slug": "cancelled", "name": "Отменено", "order_idx": 5,
     "description": "Решено не делать; идея/проект закрыт без архивирования истории"},
]

BASE_TAGS: list[dict[str, str]] = [
    # owner — НЕ хардкодим личные значения; owner-тег владельца стора сидится
    # из конфига (AtlasConfig.owner) в seed_base_tags().
    # stack (14)
    {"slug": "b24", "name": "Bitrix24", "category": "stack"},
    {"slug": "notion", "name": "Notion", "category": "stack"},
    {"slug": "telegram", "name": "Telegram", "category": "stack"},
    {"slug": "anthropic-api", "name": "Anthropic API (Claude)", "category": "stack"},
    {"slug": "openai", "name": "OpenAI API", "category": "stack"},
    {"slug": "python", "name": "Python", "category": "stack"},
    {"slug": "typescript", "name": "TypeScript", "category": "stack"},
    {"slug": "notebooklm", "name": "NotebookLM", "category": "stack"},
    {"slug": "playwright", "name": "Playwright", "category": "stack"},
    {"slug": "sqlalchemy", "name": "SQLAlchemy", "category": "stack"},
    {"slug": "fastapi", "name": "FastAPI", "category": "stack"},
    {"slug": "sqlite", "name": "SQLite", "category": "stack"},
    {"slug": "alembic", "name": "Alembic", "category": "stack"},
    {"slug": "typer", "name": "Typer CLI", "category": "stack"},
    # domain (12)
    {"slug": "marketing", "name": "Маркетинг", "category": "domain"},
    {"slug": "sales", "name": "Продажи", "category": "domain"},
    {"slug": "ai-agents", "name": "ИИ-агенты", "category": "domain"},
    {"slug": "knowledge-management", "name": "Управление знаниями", "category": "domain"},
    {"slug": "dev-tools", "name": "Dev Tools", "category": "domain"},
    {"slug": "analytics", "name": "Аналитика", "category": "domain"},
    {"slug": "pm-tools", "name": "PM Tools", "category": "domain"},
    {"slug": "crm", "name": "CRM / внедрения", "category": "domain"},
    {"slug": "content", "name": "Контент / SEO", "category": "domain"},
    {"slug": "finance", "name": "Финансы", "category": "domain"},
    {"slug": "research", "name": "Ресёрч", "category": "domain"},
    {"slug": "integrations", "name": "Интеграции", "category": "domain"},
]


# Базовые участники. Личного владельца НЕ хардкодим — он сидится из конфига
# (AtlasConfig.owner) в seed_participants(). Здесь — только generic AI-агент.
PARTICIPANTS_SEED: list[dict[str, str]] = [
    {
        "kind": "ai_agent",
        "slug": "claude-code",
        "name": "Claude Code",
        "role_default": "Developer/PM",
        "metadata_json": '{"model_family":"claude-opus-4-8","platform":"anthropic"}',
    },
]


def _owner_seed_slug() -> str:
    """member-slug владельца стора из конфига (AtlasConfig.owner); пусто, если не задан."""
    from atlas.appconfig import default_actor

    return default_actor()


SYNC_POLICIES_SEED = [
    {"slug": "local", "name": "Локально (ничего наружу)", "sync_epic": 0, "sync_task": 0, "sync_checklist": 0},
    {"slug": "epics", "name": "Только эпики (вехи)", "sync_epic": 1, "sync_task": 0, "sync_checklist": 0},
    {"slug": "media", "name": "Эпики + задачи", "sync_epic": 1, "sync_task": 1, "sync_checklist": 0},
    {"slug": "full", "name": "Полностью", "sync_epic": 1, "sync_task": 1, "sync_checklist": 1},
]

# Контрагенты — НЕ хардкодим реальных. Пусто по умолчанию; пользователь
# заводит своих через CLI. (Раньше тут были личные company/person.)
COUNTERPARTIES_SEED: list[dict[str, str]] = []

# --------------------------------------------------------------------------- #
# User-override типов (types.toml) + merge                                    #
# --------------------------------------------------------------------------- #


def _user_types_path() -> Path:
    """Путь к пользовательскому types.toml.

    Приоритет: env ``ATLAS_TYPES_FILE`` → ``~/.atlas/types.toml``.
    Тип — свойство пользователя (глобально), не стора/профиля.
    """
    override = os.environ.get("ATLAS_TYPES_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".atlas" / "types.toml"


def load_user_types() -> list[dict[str, str]]:
    """Загрузить пользовательские типы из types.toml (если файл есть).

    Формат::

        [[types]]
        slug = "worker-kit"
        name = "Worker Kit"
        default_sync_policy = "epics"
        storage_group = "products"

    Нет файла → ``[]``. Битый slug/group/policy → ValueError (не молча).
    """
    path = _user_types_path()
    if not path.exists():
        return []

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    raw = data.get("types", [])
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: ключ 'types' должен быть массивом таблиц [[types]]."
        )

    result: list[dict[str, str]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not entry.get("slug"):
            raise ValueError(f"{path}: types[{i}] без обязательного 'slug'.")
        slug = entry["slug"]
        group = entry.get("storage_group")
        if group is not None and group not in _VALID_GROUPS:
            raise ValueError(
                f"{path}: тип '{slug}' — невалидный storage_group '{group}'. "
                f"Допустимо: {', '.join(_VALID_GROUPS)}."
            )
        result.append({k: v for k, v in entry.items() if k in _TYPE_FIELDS})
    return result


def merged_project_types() -> list[dict[str, str]]:
    """Базовые типы, поверх которых наложены пользовательские (merge by slug).

    User переопределяет существующий тип (частично — только переданные поля)
    и/или добавляет новые. Порядок: базовые сохраняют позицию, новые user-типы
    добавляются в конец.
    """
    by_slug: dict[str, dict[str, str]] = {t["slug"]: dict(t) for t in BASE_PROJECT_TYPES}
    order: list[str] = [t["slug"] for t in BASE_PROJECT_TYPES]

    for ut in load_user_types():
        slug = ut["slug"]
        if slug in by_slug:
            by_slug[slug].update(ut)  # частичный override
        else:
            by_slug[slug] = dict(ut)
            order.append(slug)

    return [by_slug[s] for s in order]


# --------------------------------------------------------------------------- #
# Upsert helpers                                                              #
# --------------------------------------------------------------------------- #


def _upsert(session: Session, model, unique_field: str, data: dict) -> object:
    """Найти запись по уникальному полю или создать новую. Возвращает объект."""
    existing = session.execute(
        select(model).where(getattr(model, unique_field) == data[unique_field])
    ).scalar_one_or_none()

    if existing is not None:
        # Обновляем поля, которые пришли
        for key, value in data.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        return existing

    instance = model(**data)
    session.add(instance)
    return instance


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def seed_project_types(session: Session) -> list[ProjectType]:
    """Заселить справочник project_types из merged(base + types.toml).

    Идемпотентно по slug: существующий тип обновляется (name/description/color/
    storage_group/default_sync_policy), новый — создаётся. Политика и группа
    идут в самой записи типа (отдельный seed_type_default_policies больше не
    нужен).

    У типа есть FK ``default_sync_policy → sync_policies.slug`` (в SQLite
    FK-enforcement включён), поэтому сначала гарантируем наличие политик —
    идемпотентно. Так ``seed_project_types`` безопасен и при автономном вызове.
    """
    seed_sync_policies(session)
    session.flush()
    return [_upsert(session, ProjectType, "slug", pt) for pt in merged_project_types()]


def seed_project_statuses(session: Session) -> list[ProjectStatus]:
    """Заселить справочник project_statuses."""
    return [_upsert(session, ProjectStatus, "slug", ps) for ps in PROJECT_STATUSES]


def seed_participants(session: Session) -> list[Participant]:
    """Заселить базовых участников: generic AI-агент + владелец стора из конфига.

    Владелец не хардкодится — берётся из ``AtlasConfig.owner``, у которого есть
    дефолт ``admin`` (#899): на чистой установке участник-владелец появляется сам,
    и atlas работает без единой настройки. Сменить владельца —
    ``atlas config set owner <slug>`` (+ ``atlas person add`` для нового участника).
    """
    seeds = list(PARTICIPANTS_SEED)
    owner = _owner_seed_slug()
    if owner:
        seeds.append(
            {"kind": "human", "slug": owner, "name": owner, "role_default": "Orchestrator"}
        )
    return [_upsert(session, Participant, "slug", p) for p in seeds]


def seed_base_tags(session: Session) -> dict[str, int]:
    """Заселить базовый набор owner/stack/domain тегов (идемпотентно).

    Проверяет `SELECT ... WHERE slug = ?` перед INSERT. Если тег уже есть —
    skip (существующий не перезаписывается). Возвращает `{'created': N,
    'skipped': M}`.
    """
    created = 0
    skipped = 0
    tags = list(BASE_TAGS)
    # owner-теги — из конфига (не хардкод личных значений): личный владелец
    # (AtlasConfig.owner) + организационный владелец (AtlasConfig.team_owner).
    from atlas.appconfig import load_config

    cfg = load_config()
    seen_owner: set[str] = set()
    for slug in (cfg.owner, cfg.team_owner):
        if slug and slug not in seen_owner:
            seen_owner.add(slug)
            tags.append({"slug": slug, "name": slug, "category": "owner"})
    for data in tags:
        existing = session.execute(
            select(Tag).where(Tag.slug == data["slug"])
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        session.add(Tag(**data))
        created += 1
    return {"created": created, "skipped": skipped}


def seed_sync_policies(session: Session) -> list:
    from atlas.models import SyncPolicy
    return [_upsert(session, SyncPolicy, "slug", sp) for sp in SYNC_POLICIES_SEED]


def seed_counterparties(session: Session) -> list:
    """Заселить контрагентов: личный владелец (person) + орг-владелец (company).

    Личные/реальные значения не хардкодим — берём из конфига
    (``AtlasConfig.owner`` / ``team_owner`` / ``org_namespace``).
    """
    from atlas.appconfig import load_config
    from atlas.models import Counterparty

    cfg = load_config()
    seeds = list(COUNTERPARTIES_SEED)
    if cfg.owner:
        seeds.append({"slug": cfg.owner, "kind": "person", "name": cfg.owner})
    if cfg.team_owner:
        seeds.append(
            {
                "slug": cfg.team_owner,
                "kind": "company",
                "name": cfg.team_owner,
                "git_namespace": cfg.org_namespace or None,
            }
        )
    return [_upsert(session, Counterparty, "slug", cp) for cp in seeds]


def seed_all(session: Session) -> dict[str, int | dict[str, int]]:
    """Запустить все seeds. Возвращает counts.

    Порядок: sync_policies ДО project_types — у типа есть FK
    default_sync_policy → sync_policies.slug (SQLite FK enforcement включён).
    """
    policies = seed_sync_policies(session)
    types = seed_project_types(session)
    statuses = seed_project_statuses(session)
    participants = seed_participants(session)
    tags = seed_base_tags(session)
    counterparties = seed_counterparties(session)
    session.commit()
    return {
        "project_types": len(types),
        "project_statuses": len(statuses),
        "participants": len(participants),
        "tags": tags,
        "sync_policies": len(policies),
        "counterparties": len(counterparties),
    }
