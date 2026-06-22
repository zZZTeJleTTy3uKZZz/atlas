"""Тесты для seed-функций PM-слоя."""
from __future__ import annotations

from sqlalchemy import func, select


def test_seed_project_types_creates_all_canon_types():
    """Канон: 5 исходных + роли (kit/service/superskill) + test + inbox = 10."""
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base, ProjectType
    from atlas.pm.seeds import seed_project_types, seed_sync_policies

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with make_session(engine) as session:
        seed_sync_policies(session)
        seed_project_types(session)
        session.commit()

        count = session.execute(select(func.count()).select_from(ProjectType)).scalar()
        assert count == 10

        slugs = session.execute(select(ProjectType.slug).order_by(ProjectType.slug)).scalars().all()
        assert slugs == [
            "business-product",
            "client-project",
            "inbox",
            "kit",
            "personal-project",
            "personal-utility",
            "service",
            "shared-infrastructure",
            "superskill",
            "test",
        ]


def test_seed_all_is_idempotent():
    """Повторный вызов seed_all не должен создавать дубликаты."""
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base, Participant, ProjectStatus, ProjectType
    from atlas.pm.seeds import seed_all

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with make_session(engine) as session:
        seed_all(session)
        seed_all(session)  # второй вызов

        # W45-39: канонический набор статусов сжат до 5
        # (experiment/active/paused/archived/cancelled).
        for model, expected in [(ProjectType, 10), (ProjectStatus, 5), (Participant, 2)]:
            count = session.execute(select(func.count()).select_from(model)).scalar()
            assert count == expected, f"{model.__tablename__} count {count} != {expected}"


def test_seed_participants_includes_dmitry_and_claude():
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base, Participant
    from atlas.pm.seeds import seed_all

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with make_session(engine) as session:
        seed_all(session)

        dmitry = session.execute(
            select(Participant).where(Participant.slug == "dmitry")
        ).scalar_one()
        assert dmitry.kind == "human"
        assert dmitry.role_default == "Orchestrator"

        claude = session.execute(
            select(Participant).where(Participant.slug == "claude-code")
        ).scalar_one()
        assert claude.kind == "ai_agent"
        assert "anthropic" in (claude.metadata_json or "").lower()
