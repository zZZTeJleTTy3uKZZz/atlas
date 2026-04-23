"""Seed data для PM-БД: project_types, project_statuses, participants.

Запускается один раз при `notionctl portfolio init` после первой миграции Alembic.
Безопасен к повторному вызову — использует UPSERT-паттерн по уникальным slug.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from notion_task_cli.pm.models import Participant, ProjectStatus, ProjectType

# --------------------------------------------------------------------------- #
# Seed data                                                                   #
# --------------------------------------------------------------------------- #

PROJECT_TYPES: list[dict[str, str]] = [
    {
        "slug": "client-project",
        "name": "Клиентские проекты",
        "description": "Внедрения Bitrix24 + AI-агенты для клиентов Cifro.pro",
        "color": "#F97316",
    },
    {
        "slug": "business-product",
        "name": "Новые бизнес-продукты",
        "description": "SaaS-продукты Cifro.pro (NP-001..005+)",
        "color": "#10B981",
    },
    {
        "slug": "personal-utility",
        "name": "Личные утилиты",
        "description": "Dev-утилиты Дмитрия (Tests/* и пр.)",
        "color": "#8B5CF6",
    },
    {
        "slug": "personal-project",
        "name": "Личные проекты",
        "description": "Собственные инициативы (Дима/*)",
        "color": "#EC4899",
    },
    {
        "slug": "shared-infrastructure",
        "name": "Общая инфраструктура",
        "description": "Инструменты, используемые многими проектами (notion-task-cli и пр.)",
        "color": "#6B7280",
    },
]

PROJECT_STATUSES: list[dict[str, str | int]] = [
    {"slug": "experiment", "name": "Эксперимент", "order_idx": 1,
     "description": "Проба гипотезы, 1-30 дней"},
    {"slug": "active", "name": "Активный", "order_idx": 2,
     "description": "В работе, есть цель и критерий завершения"},
    {"slug": "maintained", "name": "Поддержка", "order_idx": 3,
     "description": "Готово, поддерживаем, не развиваем активно"},
    {"slug": "dormant", "name": "Пауза", "order_idx": 4,
     "description": "Осознанная пауза, ждём внешнего события"},
    {"slug": "graduating", "name": "Graduating", "order_idx": 5,
     "description": "Утилита/эксперимент готовится стать business-product"},
    {"slug": "archived", "name": "Архив", "order_idx": 6,
     "description": "Закрыто, код/доки оставлены как history"},
]

PARTICIPANTS_SEED: list[dict[str, str]] = [
    {
        "kind": "human",
        "slug": "dmitry",
        "name": "Дмитрий Семёнов",
        "role_default": "Orchestrator",
    },
    {
        "kind": "ai_agent",
        "slug": "claude-code",
        "name": "Claude Code",
        "role_default": "Developer/PM",
        "metadata_json": '{"model_family":"claude-opus-4-7","platform":"anthropic"}',
    },
]


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
    """Заселить справочник project_types."""
    return [_upsert(session, ProjectType, "slug", pt) for pt in PROJECT_TYPES]


def seed_project_statuses(session: Session) -> list[ProjectStatus]:
    """Заселить справочник project_statuses."""
    return [_upsert(session, ProjectStatus, "slug", ps) for ps in PROJECT_STATUSES]


def seed_participants(session: Session) -> list[Participant]:
    """Заселить базовых участников (Дмитрий + Claude Code)."""
    return [_upsert(session, Participant, "slug", p) for p in PARTICIPANTS_SEED]


def seed_all(session: Session) -> dict[str, int]:
    """Запустить все seeds. Возвращает counts."""
    types = seed_project_types(session)
    statuses = seed_project_statuses(session)
    participants = seed_participants(session)
    session.commit()
    return {
        "project_types": len(types),
        "project_statuses": len(statuses),
        "participants": len(participants),
    }
