from datetime import date, datetime

from atlas.props import (
    read_checkbox,
    read_date,
    read_multi_select,
    read_number,
    read_relation_ids,
    read_rich_text,
    read_status,
    read_title,
    w_checkbox,
    w_date,
    w_relation,
    w_status,
    w_title,
)


def _page(props: dict) -> dict:
    return {"id": "x", "properties": props}


def test_read_title_and_status():
    p = _page({
        "Задача": {"type": "title", "title": [{"plain_text": "Привет"}]},
        "Готово?": {"type": "status", "status": {"name": "В работе"}},
    })
    assert read_title(p, "Задача") == "Привет"
    assert read_status(p, "Готово?") == "В работе"


def test_read_number_and_checkbox_and_relations():
    p = _page({
        "b24_task_id": {"type": "number", "number": 1846},
        "Сделано?": {"type": "checkbox", "checkbox": True},
        "👾 Проекты": {"type": "relation", "relation": [{"id": "a"}, {"id": "b"}]},
        "Тип": {"type": "multi_select", "multi_select": [{"name": "встреча"}]},
        "Блок": {"type": "rich_text", "rich_text": [{"plain_text": "срочно"}]},
    })
    assert read_number(p, "b24_task_id") == 1846
    assert read_checkbox(p, "Сделано?") is True
    assert read_relation_ids(p, "👾 Проекты") == ["a", "b"]
    assert read_multi_select(p, "Тип") == ["встреча"]
    assert read_rich_text(p, "Блок") == "срочно"


def test_read_date_only():
    p = _page({"Дата": {"type": "date", "date": {"start": "2026-04-22"}}})
    dv = read_date(p, "Дата")
    assert dv and dv.as_date == date(2026, 4, 22)
    assert dv.has_time is False


def test_read_date_with_time():
    p = _page({"Дата": {"type": "date", "date": {"start": "2026-04-22T15:30:00.000+03:00"}}})
    dv = read_date(p, "Дата")
    assert dv and dv.has_time
    assert isinstance(dv.start, datetime)


def test_writers():
    assert w_title("x")["title"][0]["text"]["content"] == "x"
    assert w_status("В планах") == {"status": {"name": "В планах"}}
    assert w_checkbox(False) == {"checkbox": False}
    assert w_relation(["p"]) == {"relation": [{"id": "p"}]}
    d = w_date(date(2026, 4, 22))
    assert d["date"]["start"] == "2026-04-22"
    d2 = w_date(datetime(2026, 4, 22, 15, 30))
    # содержит таймзону
    assert "T15:30" in d2["date"]["start"]
