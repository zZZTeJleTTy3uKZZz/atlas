"""Авто-раскладка нового проекта: дефолты владельца/режима (этап 1)."""
from atlas.appconfig import owner_member_slug
from atlas.pm.commands._provision import resolve_project_mode


def test_owner_member_slug_from_atlas_dmitry():
    assert owner_member_slug("atlas-dmitry") == "dmitry"


def test_owner_member_slug_admin_is_also_dmitry():
    assert owner_member_slug("atlas-admin") == "dmitry"


def test_owner_member_slug_unknown_portal_falls_back_to_dmitry():
    assert owner_member_slug("atlas-something") == "dmitry"


def test_default_is_personal_owned_by_me():
    m = resolve_project_mode(type_flag=None, team=False, owner=None, default_owner="dmitry")
    assert m.type_slug == "personal-project"
    assert m.sync_policy == "full"
    assert m.visibility == "personal"
    assert m.owner_slug == "dmitry"
    assert m.lead_slug == "dmitry"
    assert m.status_slug == "active"


def test_team_flag_makes_company_project_but_keeps_me_lead():
    m = resolve_project_mode(type_flag=None, team=True, owner=None, default_owner="dmitry")
    assert m.visibility == "team"
    assert m.owner_slug == "cifro-pro"
    assert m.lead_slug == "dmitry"      # Дмитрий всё равно lead (видит задачи)
    assert m.sync_policy == "media"


def test_explicit_owner_overrides_and_becomes_lead():
    m = resolve_project_mode(type_flag=None, team=False, owner="artem", default_owner="dmitry")
    assert m.owner_slug == "artem"
    assert m.lead_slug == "artem"
    assert m.visibility == "team"        # чужой владелец → не личный


def test_explicit_type_preserved():
    m = resolve_project_mode(type_flag="business-product", team=False, owner=None, default_owner="dmitry")
    assert m.type_slug == "business-product"
    assert m.visibility == "personal"    # без --team/--owner всё ещё личный режим
