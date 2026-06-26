"""SQLAlchemy ORM модели для PM-системы.

MVP-схема (Spike v0.4): project_types, project_statuses, projects, participants,
project_participants, tasks, action_log.

Расширения Sprint 1: expenses, prd_snapshots, stacks, project_stacks.
Расширения v0.7 (multi-agent): agent_runs, research_findings.

Полная схема и обоснования — в `MODEL.md` Atlas.
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

from atlas._time import local_now


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
    # --- Канон типов (миграция canon) -------------------------------------
    # storage_group — физическая группа размещения проекта этого типа на диске:
    # clients|products|tests|inbox. Свод хардкода paths.TYPE_TO_GROUP в БД.
    # NULL допустим (старые/кастомные типы без явной группы).
    storage_group: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # ----------------------------------------------------------------------
    default_sync_policy: Mapped[Optional[str]] = mapped_column(
        String(50), ForeignKey("sync_policies.slug")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
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
        DateTime, default=local_now, nullable=False
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
    # --- Git integration (миграция 006) ------------------------------------
    git_remote_url: Mapped[Optional[str]] = mapped_column(String(500))
    git_default_branch: Mapped[str] = mapped_column(
        String(100), default="main", server_default="main", nullable=False
    )
    git_provider: Mapped[Optional[str]] = mapped_column(String(20))
    git_initialized_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    git_last_pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # ----------------------------------------------------------------------
    # --- Entity model refactor (миграция 007) ----------------------------
    # entity_kind определяет роль записи в портфеле:
    #   - 'project' (default) — полноценный проект с _storage/<slug>/ + junction.
    #   - 'idea'    — стадия 0: 1 MD-файл в _Ideas/<slug>.md, без storage.
    #   - 'inbox'   — сырой материал для разбора AI: _Inbox/<slug>/.
    # Routing физики: см. atlas.paths.entity_kind_to_root().
    entity_kind: Mapped[str] = mapped_column(
        String(20), default="project", server_default="project", nullable=False
    )
    # ----------------------------------------------------------------------
    # --- F3b: контрагенты + политика синка (миграция f3b-1) ---------------
    owner_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("counterparties.id")
    )
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("counterparties.id")
    )
    sync_policy: Mapped[Optional[str]] = mapped_column(
        String(50), ForeignKey("sync_policies.slug")
    )
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    # ----------------------------------------------------------------------
    # --- Канон типов: иерархия проектов (миграция canon) ------------------
    # parent_id — родительский проект (под-проект / направление внутри
    # портфеля контрагента). FK на ORM-уровне для метаданных; в миграции
    # как DB-constraint НЕ создаётся (безымянный FK в batch падает —
    # как в provenance-миграции c1d2e3f4a5b6). Индексируется именованным
    # idx_projects_parent ниже (в __table_args__) — детерминированное имя
    # для миграции; index=True на колонке не ставим (иначе дубль с auto ix_*).
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )
    # ----------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, onupdate=local_now, nullable=False
    )
    last_touched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "priority IN ('P0','P1','P2','P3')", name="ck_projects_priority"
        ),
        CheckConstraint(
            "archived_group IS NULL OR archived_group IN ('clients','products','tests','inbox')",
            name="ck_projects_archived_group",
        ),
        CheckConstraint(
            "entity_kind IN ('project','idea','inbox')",
            name="ck_projects_entity_kind",
        ),
        CheckConstraint(
            "git_provider IS NULL OR git_provider IN ('gitlab','github')",
            name="ck_projects_git_provider",
        ),
        Index("idx_projects_type", "type_id"),
        Index("idx_projects_status", "status_id"),
        Index("idx_projects_priority", "priority"),
        Index("idx_projects_last_touched", "last_touched_at"),
        Index("idx_projects_parent", "parent_id"),
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
        DateTime, default=local_now, nullable=False
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
        DateTime, default=local_now, nullable=False
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
    epic_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("epics.id"))
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
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    git_branch: Mapped[Optional[str]] = mapped_column(String(200))
    git_pr_url: Mapped[Optional[str]] = mapped_column(String(500))
    superpowers_spec_path: Mapped[Optional[str]] = mapped_column(String(500))
    superpowers_plan_path: Mapped[Optional[str]] = mapped_column(String(500))
    quality_tier: Mapped[Optional[str]] = mapped_column(String(3))
    # --- Provenance: происхождение задачи (миграция provenance) -----------
    # source_project_id — проект-источник (NULL = нативная задача).
    # origin — как заведена: native|injected|imported|split.
    # rationale — почему/по какому принципу заведена.
    # injected_by / injected_at — кто и когда инжектировал из источника.
    # source_project_id индексируется именованным idx_tasks_source_project
    # ниже (в __table_args__) — детерминированное имя для миграции, поэтому
    # index=True на колонке не ставим (иначе дубль с auto ix_*).
    source_project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )
    origin: Mapped[str] = mapped_column(
        String(20), default="native", server_default="native", nullable=False
    )
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    injected_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id"), nullable=True
    )
    injected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # ----------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, onupdate=local_now, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # --- Lease/Claim: блокировка задачи для мультиагентности (Волна 8) -----
    # lease_* — ЛОКАЛЬНАЯ координация агентов: кто/откуда/когда взял задачу.
    # НЕ синкается в ядро (см. sync/mapper._task_payload). lock_version —
    # optimistic-lock через version_id_col (__mapper_args__ ниже): бампается и
    # проверяется на каждом ORM-flush задачи.
    lease_owner: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id"), nullable=True
    )
    lease_session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    lease_origin: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )

    __mapper_args__ = {"version_id_col": lock_version}

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
        CheckConstraint(
            "origin IN ('native','injected','imported','split')",
            name="ck_tasks_origin",
        ),
        Index("idx_tasks_project", "project_id"),
        Index("idx_tasks_epic", "epic_id"),
        Index("idx_tasks_assignee", "assignee_id"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_due", "due_date"),
        Index("idx_tasks_source_project", "source_project_id"),
        Index("idx_tasks_lease", "lease_owner", "lease_expires_at"),
        Index("idx_tasks_lease_expires", "lease_expires_at"),
    )


# --------------------------------------------------------------------------- #
# Hypotheses (Atlas Hypothesis Ledger)                                        #
# --------------------------------------------------------------------------- #


class Hypothesis(Base):
    """Гипотеза — единица цикла «опыт → гипотеза → эксперимент → замер → решение».

    Реестр гипотез + эффективность (success-rate ledger). Следует паттерну Task:
    id/number/slug/FK/timestamps/CheckConstraint/Index. Привязка к проекту
    обязательна; опц. связь с задачей (task_id, ondelete SET NULL).
    """

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    number: Mapped[Optional[int]] = mapped_column(Integer, unique=True, nullable=True)
    slug: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    statement: Mapped[Optional[str]] = mapped_column(Text)
    metric: Mapped[Optional[str]] = mapped_column(String(200))
    baseline: Mapped[Optional[str]] = mapped_column(String(100))
    target: Mapped[Optional[str]] = mapped_column(String(100))
    method: Mapped[Optional[str]] = mapped_column(Text)
    result_value: Mapped[Optional[str]] = mapped_column(String(100))
    delta: Mapped[Optional[str]] = mapped_column(String(100))
    verdict: Mapped[Optional[str]] = mapped_column(String(20))
    lesson: Mapped[Optional[str]] = mapped_column(Text)
    consolidated_into: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(3), default="M", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, onupdate=local_now, nullable=False
    )
    tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','testing','measured','closed')",
            name="ck_hypotheses_status",
        ),
        CheckConstraint(
            "confidence IN ('H','M','L')", name="ck_hypotheses_confidence"
        ),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('accept','reject','iterate','inconclusive')",
            name="ck_hypotheses_verdict",
        ),
        Index("idx_hypotheses_project", "project_id"),
        Index("idx_hypotheses_task", "task_id"),
        Index("idx_hypotheses_status", "status"),
        Index("idx_hypotheses_verdict", "verdict"),
        Index("idx_hypotheses_created", "created_at"),
    )


# --------------------------------------------------------------------------- #
# Audit log (append-only)                                                     #
# --------------------------------------------------------------------------- #


class ActionLog(Base):
    """Append-only аудит. Никогда не UPDATE/DELETE записи."""

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
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
        DateTime, default=local_now, nullable=False
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
        DateTime, default=local_now, nullable=False
    )

    __table_args__ = (
        Index("idx_project_tags_tag", "tag_id"),
    )


# --------------------------------------------------------------------------- #
# F3b: справочники синка + контрагенты                                        #
# --------------------------------------------------------------------------- #


class SyncPolicy(Base):
    """Политика-потолок синка: до какого уровня иерархии выгружать наружу.

    v1 — три булевых уровня (bool как Integer 0/1). Сиды: local(0,0,0),
    epics(1,0,0), media(1,1,0), full(1,1,1).
    """

    __tablename__ = "sync_policies"

    slug: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sync_epic: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sync_task: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sync_checklist: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
    )


class Counterparty(Base):
    """Контрагент — владелец/заказчик проекта (бизнес-связь, НЕ адрес синка).

    От owner вытекает git-namespace; пространство синка определяет команда
    проекта (участники), не контрагент. Зеркало core-Counterparty.
    """

    __tablename__ = "counterparties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    git_namespace: Mapped[Optional[str]] = mapped_column(String(200))
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('person','company')", name="ck_counterparties_kind"
        ),
    )


# --------------------------------------------------------------------------- #
# F3b: иерархия Epic → Task → ChecklistItem + TaskMember                       #
# --------------------------------------------------------------------------- #


class Epic(Base):
    """Эпик = спринт (крупная веха, опц. даты). Уровень, синкаемый наружу."""

    __tablename__ = "epics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    goal: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    # --- Provenance: происхождение эпика (миграция provenance) ------------
    source_project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )
    origin: Mapped[str] = mapped_column(
        String(20), default="native", server_default="native", nullable=False
    )
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    injected_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id"), nullable=True
    )
    injected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # ----------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, onupdate=local_now, nullable=False
    )
    # --- Lease/Claim: групповой lease эпика (эпик «Групповой lease») -------
    # Симметрично Task: epic claim ставит lease на эпик И каскадно лочит его
    # незавершённые задачи под одного держателя. lease_* — ЛОКАЛЬНАЯ коорди-
    # нация, НЕ синкается в ядро (см. sync/mapper._epic_payload). lock_version
    # — optimistic-lock через version_id_col (__mapper_args__ ниже).
    lease_owner: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id"), nullable=True
    )
    lease_session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    lease_origin: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )

    __mapper_args__ = {"version_id_col": lock_version}

    __table_args__ = (
        CheckConstraint(
            "origin IN ('native','injected','imported','split')",
            name="ck_epics_origin",
        ),
        Index("idx_epics_project", "project_id"),
        Index("idx_epics_source_project", "source_project_id"),
        Index("idx_epics_lease", "lease_owner", "lease_expires_at"),
        Index("idx_epics_lease_expires", "lease_expires_at"),
    )


class ChecklistItem(Base):
    """Чек-лист задачи (шаги ИИ-агента). По умолчанию локален."""

    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    __table_args__ = (Index("idx_checklist_task", "task_id"),)


class TaskMember(Base):
    """Участник задачи с ролью (расширение одиночного assignee_id)."""

    __tablename__ = "task_members"

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('responsible','executor','watcher')", name="ck_task_members_role"
        ),
    )


# --------------------------------------------------------------------------- #
# F3b: sync-инфра — outbox + курсор pull                                       #
# --------------------------------------------------------------------------- #


class Outbox(Base):
    """Очередь исходящих операций (локальное изменение → событие на хаб)."""

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    op: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("op IN ('create','update','delete')", name="ck_outbox_op"),
        CheckConstraint(
            "status IN ('pending','sent','failed')", name="ck_outbox_status"
        ),
        Index("idx_outbox_status", "status"),
    )


class SyncCursor(Base):
    """Курсор pull-канала (ISO occurred_at последнего применённого события)."""

    __tablename__ = "sync_cursors"

    channel: Mapped[str] = mapped_column(String(50), primary_key=True)
    cursor: Mapped[Optional[str]] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, onupdate=local_now, nullable=False
    )
