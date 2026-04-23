from notion_task_cli.tasks import ACTIVE_STATUSES, STATUS_ALIAS, task_from_page


def test_status_alias_canonical():
    assert STATUS_ALIAS["done"] == "Выполнена"
    assert STATUS_ALIAS["planned"] == "В планах"
    assert STATUS_ALIAS["в работе"] == "В работе"


def test_task_from_page_min():
    page = {
        "id": "p1",
        "url": "https://pragmat.notion.site/p1",
        "properties": {
            "Задача": {"type": "title", "title": [{"plain_text": "T"}]},
            "Готово?": {"type": "status", "status": {"name": "В работе"}},
            "Дата": {"type": "date", "date": None},
            "Тип": {"type": "multi_select", "multi_select": []},
            "👾 Проекты": {"type": "relation", "relation": []},
            "👾 Под-Проекты": {"type": "relation", "relation": [{"id": "sp"}]},
            "Ответственный": {"type": "relation", "relation": []},
            "Исполнители": {"type": "relation", "relation": []},
            "b24_task_id": {"type": "number", "number": None},
            "b24_checklist_item_id": {"type": "number", "number": 2640},
        },
    }
    t = task_from_page(page)
    assert t.id == "p1" and t.title == "T"
    assert t.status == "В работе" and t.is_active
    assert t.b24_item_id == 2640
    assert t.subprojects == ["sp"]
    assert t.status in ACTIVE_STATUSES
