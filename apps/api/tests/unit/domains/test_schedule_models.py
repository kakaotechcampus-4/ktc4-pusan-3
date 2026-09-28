from app.domains.schedule.models import Event


def test_event_has_no_status_or_expiry():
    """draft 를 DB 에 쓰지 않기로 했다 (#118) — 이 컬럼이 되살아나면 결정이 조용히 뒤집힌 것이다."""
    columns = set(Event.__table__.c.keys())
    assert not columns & {"status", "expires_at"}
