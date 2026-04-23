from datetime import date, datetime, timedelta

import pytest

from notion_task_cli.dates import bucketize, parse_human_date


TZ = "Europe/Moscow"


def test_parse_today_tomorrow():
    today = parse_human_date("сегодня", tz_name=TZ)
    tomorrow = parse_human_date("завтра", tz_name=TZ)
    assert isinstance(today, date)
    assert tomorrow == today + timedelta(days=1)


def test_parse_plus_n_days():
    t = parse_human_date("+3д", tz_name=TZ)
    assert t == parse_human_date("сегодня", tz_name=TZ) + timedelta(days=3)


def test_parse_iso_date_only():
    d = parse_human_date("2026-05-10", tz_name=TZ)
    assert d == date(2026, 5, 10)


def test_parse_iso_with_time():
    d = parse_human_date("2026-05-10 15:30", tz_name=TZ)
    assert isinstance(d, datetime)
    assert d.tzinfo is not None
    assert d.hour == 15 and d.minute == 30


def test_parse_weekday_future():
    # ближайший вторник — не сегодня (>= 1 день)
    d = parse_human_date("вторник", tz_name=TZ)
    assert isinstance(d, date)
    assert d.weekday() == 1
    assert d > parse_human_date("сегодня", tz_name=TZ)


def test_parse_invalid():
    with pytest.raises(ValueError):
        parse_human_date("непонятно", tz_name=TZ)


def test_bucketize():
    today = parse_human_date("сегодня", tz_name=TZ)
    assert bucketize(None) == "no_date"
    assert bucketize(today) == "today"
    assert bucketize(today + timedelta(days=3)) == "upcoming"
    assert bucketize(today + timedelta(days=100)) == "someday"
    assert bucketize(today - timedelta(days=1)) == "overdue"
