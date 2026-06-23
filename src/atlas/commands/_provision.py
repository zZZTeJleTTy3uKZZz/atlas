"""Чистый резолв режима нового проекта (без БД/CLI) — тестируется изолированно.

`project add` сводит флаги (--team / --owner / --type) и дефолтного владельца к
:class:`ProjectMode`: тип, статус, политику синка, видимость, владельца и lead'а.
Личный проект (без --team и без чужого --owner) синкается полностью (full) и
маршрутизируется в личный контур владельца (visibility=personal).
"""
from __future__ import annotations

from dataclasses import dataclass

# Тип по умолчанию для личного проекта. У personal-project default_sync_policy=epics
# (задачи НЕ синкаются наружу), поэтому личному ЯВНО ставим full.
_PERSONAL_TYPE = "personal-project"
_COMPANY_OWNER = "cifro-pro"


@dataclass(frozen=True)
class ProjectMode:
    type_slug: str
    sync_policy: str
    visibility: str
    owner_slug: str
    lead_slug: str


def resolve_project_mode(
    *, type_flag: str | None, team: bool, owner: str | None, default_owner: str
) -> ProjectMode:
    """Свести флаги к режиму нового проекта.

    Владелец (counterparty): ``--owner`` > (``--team`` → cifro-pro) > ``default_owner``.
    lead (member-человек, видит все задачи): явный ``--owner`` (он же ведёт), иначе
    ``default_owner`` — даже у командного проекта (--team) lead остаётся человеком,
    компания lead'ом быть не может. Личный режим = НЕ team И (owner не задан ИЛИ
    owner == default_owner) → full + personal; иначе team + media.
    """
    explicit_owner = owner or (_COMPANY_OWNER if team else None)
    owner_slug = explicit_owner or default_owner
    is_personal = not team and (owner is None or owner == default_owner)
    return ProjectMode(
        type_slug=type_flag or (_PERSONAL_TYPE if is_personal else "client-project"),
        sync_policy="full" if is_personal else "media",
        visibility="personal" if is_personal else "team",
        owner_slug=owner_slug,
        lead_slug=owner or default_owner,
    )


__all__ = ["ProjectMode", "resolve_project_mode"]
