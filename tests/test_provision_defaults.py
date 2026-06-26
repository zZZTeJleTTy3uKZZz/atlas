"""Авто-раскладка нового проекта: дефолты владельца/режима (этап 1)."""
from atlas.appconfig import owner_member_slug
from atlas.commands._provision import resolve_project_mode


def test_owner_member_slug_from_atlas_owner():
    assert owner_member_slug("atlas-personal") == "owner"


def test_owner_member_slug_admin_is_also_owner():
    assert owner_member_slug("atlas-admin") == "owner"


def test_owner_member_slug_unknown_portal_falls_back_to_owner():
    assert owner_member_slug("atlas-something") == "owner"


def test_default_is_personal_owned_by_me():
    m = resolve_project_mode(type_flag=None, team=False, owner=None, default_owner="owner")
    assert m.type_slug == "personal-project"
    assert m.sync_policy == "full"
    assert m.visibility == "personal"
    assert m.owner_slug == "owner"
    assert m.lead_slug == "owner"


def test_team_flag_makes_company_project_but_keeps_me_lead():
    m = resolve_project_mode(
        type_flag=None, team=True, owner=None, default_owner="owner",
        company_owner="example-org",
    )
    assert m.visibility == "team"
    assert m.owner_slug == "example-org"
    assert m.lead_slug == "owner"      # Owner всё равно lead (видит задачи)
    assert m.sync_policy == "media"


def test_explicit_owner_overrides_and_becomes_lead():
    m = resolve_project_mode(type_flag=None, team=False, owner="artem", default_owner="owner")
    assert m.owner_slug == "artem"
    assert m.lead_slug == "artem"
    assert m.visibility == "team"        # чужой владелец → не личный


def test_explicit_type_preserved():
    m = resolve_project_mode(type_flag="business-product", team=False, owner=None, default_owner="owner")
    assert m.type_slug == "business-product"
    assert m.visibility == "personal"    # без --team/--owner всё ещё личный режим
