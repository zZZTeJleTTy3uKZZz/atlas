"""SQLAlchemy ORM модели для PM-системы.

MVP-схема (Spike v0.4): project_types, project_statuses, projects, participants,
project_participants, tasks, action_log.

Расширения Sprint 1: sprints, expenses, prd_snapshots, stacks, project_stacks.
Расширения v0.7 (multi-agent): agent_runs, research_findings.

Полная схема и обоснования — в `MODEL.md` NP-005.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Декларативная база SQLAlchemy 2.x."""


def _gen_uuid() -> str:
    """Сгенерировать UUID4 как строку (для SQLite)."""
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Справочники                                                                 #
# --------------------------------------------------------------------------- #


class ProjectType(Base):
    """Тип проекта (client-project, business-product, personal-utility, ...).

    Связь, а не enum — можно добавлять новые типы без миграции структуры.
    """

    __tablename__ = "project_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]] = mapped_column(String(20))
    is_archived: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class ProjectStatus(Base):
    """Lifecycle-статус (experiment, active, maintained, dormant, archived, graduating)."""

    __tablename__ = "project_statuses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    order_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# Projects                                                                    #
# --------------------------------------------------------------------------- #


class Project(Base):
    """Проект — центральная сущность портфеля."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    prefix: Mapped[Optional[str]] = mapped_column(String(5), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    type_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_types.id"), nullable=False
    )
    status_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_statuses.id"), nullable=False
    )
    priority: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    one_line_summary: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime)
    git_repo_url: Mapped[Optional[str]] = mapped_column(String(500))
    local_path: Mapped[Optional[str]] = mapped_column(String(500))
    notion_project_id: Mapped[Optional[str]] = mapped_column(String(100))
    notebooklm_id: Mapped[Optional[str]] = mapped_column(String(100))
    b24_company_id: Mapped[Optional[str]] = mapped_column(String(100))
    renewal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    archived_group: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_touched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "priority IN ('P0','P1','P2','P3')", name="ck_projects_priority"
        ),
        CheckConstraint(
            "archived_group IS NULL OR archived_group IN ('clients','products','tests')",
            name="ck_projects_archived_group",
        ),
        Index("idx_projects_type", "type_id"),
        Index("idx_projects_status", "status_id"),
        Index("idx_projects_priority", "priority"),
        Index("idx_projects_last_touched", "last_touched_at"),
    )


# --------------------------------------------------------------------------- #
# Participants                                                                #
# --------------------------------------------------------------------------- #


class Participant(Base):
    """Участник: человек, AI-агент или контрактник."""

    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_default: Mapped[Optional[str]] = mapped_column(String(100))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('human','ai_agent','contractor')", name="ck_participants_kind"
        ),
    )


class ProjectParticipant(Base):
    """M:N между projects и participants с ролью в проекте."""

    __tablename__ = "project_participants"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id", ondelete="RESTRICT"), primary_key=True
    )
    role_in_project: Mapped[str] = mapped_column(String(100), nullable=False)
    allocated_weekly_hours: Mapped[Optional[float]] = mapped_column()
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


# --------------------------------------------------------------------------- #
# Tasks                                                                       #
# --------------------------------------------------------------------------- #


class Task(Base):
    """Задача. Содержит обязательное поле ЦКП (cpp_description)."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    number: Mapped[Optional[int]] = mapped_column(Integer, unique=True, nullable=True)
    slug: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    sprint_id: Mapped[Optional[str]] = mapped_column(String(36))  # FK добавим в Sprint 1
    assignee_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id")
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    cpp_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="backlog")
    priority: Mapped[str] = mapped_column(String(3), nullable=False)
    story_points: Mapped[Optional[int]] = mapped_column(Integer)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notion_page_id: Mapped[Optional[str]] = mapped_column(String(100))
    git_branch: Mapped[Optional[str]] = mapped_column(String(200))
    git_pr_url: Mapped[Optional[str]] = mapped_column(String(500))
    superpowers_spec_path: Mapped[Optional[str]] = mapped_column(String(500))
    superpowers_plan_path: Mapped[Optional[str]] = mapped_column(String(500))
    quality_tier: Mapped[Optional[str]] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "status IN ('backlog','todo','in_progress','review','done','blocked','cancelled')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('P0','P1','P2','P3')", name="ck_tasks_priority"
        ),
        CheckConstraint(
            "quality_tier IS NULL OR quality_tier IN ('T1','T2','T3')",
            name="ck_tasks_quality_tier",
        ),
        Index("idx_tasks_project", "project_id"),
        Index("idx_tasks_sprint", "sprint_id"),
        Index("idx_tasks_assignee", "assignee_id"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_due", "due_date"),
    )


# --------------------------------------------------------------------------- #
# Audit log (append-only)                                                     #
# --------------------------------------------------------------------------- #


class ActionLog(Base):
    """Append-only аудит. Никогда не UPDATE/DELETE записи."""

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    actor_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id")
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details_json: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_action_log_entity", "entity_type", "entity_id"),
        Index("idx_action_log_actor", "actor_id"),
        Index("idx_action_log_timestamp", "timestamp"),
    )


# --------------------------------------------------------------------------- #
# Tags                                                                        #
# --------------------------------------------------------------------------- #


class Tag(Base):
    """Тег — произвольный ярлык на проект (owner/stack/domain/other).

    slug глобально уникален; category — отдельное поле (не вплетается в slug).
    """

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('owner','stack','domain','other')",
            name="ck_tags_category",
        ),
        Index("idx_tags_category", "category"),
    )


class ProjectTag(Base):
    """M:N между projects и tags."""

    __tablename__ = "project_tags"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_project_tags_tag", "tag_id"),
    )
