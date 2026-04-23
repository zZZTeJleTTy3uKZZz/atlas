from notion_task_cli.index import Index


def test_index_pairs_and_name():
    idx = Index({"a": "Персона", "b": "Каша"})
    assert idx.name("a") == "Персона"
    assert idx.name("z") is None
    assert idx.pairs(["a", "z"]) == [
        {"id": "a", "title": "Персона"},
        {"id": "z", "title": ""},
    ]
