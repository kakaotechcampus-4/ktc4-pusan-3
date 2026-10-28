"""목록 검색어(`q`) → `ILIKE` 패턴 — 기록 · 기억 목록이 같이 쓴다 (#584)."""


def contains_pattern(q: str | None) -> str | None:
    """부분 일치 패턴. SQL 은 `ILIKE <패턴> ESCAPE '\\'` 로 쓴다.

    🚨 사용자가 친 `%` · `_` · `\\` 는 와일드카드가 아니라 글자 그대로다. 막지 않으면 `q=%` 가
       전체 매칭이 된다. `\\` 를 먼저 바꿔야 뒤에 붙는 이스케이프가 다시 이스케이프되지 않는다.
    """
    if q is None:
        return None
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
