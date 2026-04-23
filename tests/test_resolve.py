import pytest

from notion_task_cli.resolve import normalize_page_id


def test_normalize_plain_hex():
    assert (
        normalize_page_id("349bfce2404c81b593cad9116e88b471")
        == "349bfce2-404c-81b5-93ca-d9116e88b471"
    )


def test_normalize_with_dashes():
    x = "349bfce2-404c-81b5-93ca-d9116e88b471"
    assert normalize_page_id(x) == x


def test_normalize_url():
    url = "https://pragmat.notion.site/349bfce2404c81b593cad9116e88b471?source=copy"
    assert normalize_page_id(url) == "349bfce2-404c-81b5-93ca-d9116e88b471"


def test_normalize_bad():
    with pytest.raises(ValueError):
        normalize_page_id("not-a-uuid")
