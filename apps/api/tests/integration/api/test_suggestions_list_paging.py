"""추천 목록 `GET /children/{cid}/suggestions` 의 `rated` 필터 · 쪽(page · size) 스펙 — #592.

기록 · 기억 목록(#584 · `test_observations.py`)과 같은 쪽 나누기를 쓴다.
응답은 `{items, total, page, size, total_pages}` 이고 `next_cursor` · `cursor` · `limit` 은 없다.

스펙 (이 파일이 구현보다 먼저 있다)

  rated 필터 — `rated: bool | None = None`
    R1  안 보내면 지금과 같다 — draft 를 뺀 전부. 평가한 것은 feedback 값이 실린다
    R2  rated=false → feedback 이 비어 있는 것만
    R3  rated=true  → feedback 이 있는 것만 (세 값 모두 "평가함")
    R4  true 와 false 는 겹치지 않고, 합치면 안 보낸 것과 같다
    R5  평가하면 false → true 로 옮겨 간다. 다시 고르면 true 에 남은 채 값만 바뀐다
    R6  status 와 AND 로 걸린다 — draft 는 평가를 못 받아서 rated=true 면 비고, false 면 나온다
    R7  rated 를 걸어도 draft 는 기본 목록에 나오지 않는다 (draft 는 feedback 이 NULL 이다)
    R8  다른 아이의 평가 · 미평가는 섞이지 않는다
    R9  일반 추천(kind=general)도 같은 길이다
    R10 boolean 이 아닌 값 → 400 validation_failed (422 아님)
    R11 expired 행은 평가를 받을 수 없지만(PATCH 는 approved · rejected 만) 기본 목록에는 들어 있다.
        그래서 rated=false 에는 나오고 rated=true 에는 안 나온다. 지금 동작을 못박는 테스트다
        (앱이 DB 에 expired 를 쓰는 곳은 아직 없다 — 쓰게 되면 이 테스트로 의도를 다시 정한다)

  쪽 나누기 — `page` (1~10,000, 기본 1) · `size` (1~100, 기본 20)
    P1  응답 키는 정확히 items · total · page · size · total_pages (next_cursor 없음)
    P2  최신순(created_at DESC, id DESC)으로 빠짐 · 겹침 없이 이어진다. 범위 밖 쪽은 빈 목록
    P3  created_at 이 같은 행끼리도 쪽을 넘나들며 빠지거나 겹치지 않는다
    P4  total 은 필터(status · rated)를 건 뒤의 전체 건수다. 쪽과 무관하다
    P5  total_pages = ceil(total / size), 0건이면 0
    P6  page · size 를 안 보내면 1 · 20 이고 응답에 그대로 실린다
    P7  범위 밖(page 0 · 10,001 · 10**20, size 0 · 101)은 400. page 10,000 · size 100 은 200
    P8  남의 아이는 어떤 쿼리여도 403
    P9  Swagger 에 page · size · rated · status 가 있고 cursor · limit 은 없다 (page 최대 10,000)
    P10 근거(evidence)는 쪽을 나눠도 그대로 붙는다
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionFeedback,
    SuggestionKind,
    SuggestionStatus,
)
from app.main import app
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

_APPROVED = SuggestionStatus.APPROVED
_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


async def _make(
    session: AsyncSession,
    child_id: uuid.UUID,
    *,
    status: SuggestionStatus = _APPROVED,
    feedback: SuggestionFeedback | None = None,
    kind: SuggestionKind = SuggestionKind.PERSONALIZED,
    minutes_ago: int | None = None,
) -> Suggestion:
    """`minutes_ago` 를 주면 그 시각으로, 안 주면 트랜잭션 시각(행끼리 같은 시각)으로 만든다."""
    row = Suggestion(
        child_id=child_id,
        agent=SuggestionAgent.ACTIVITY,
        kind=kind,
        content="공원 산책",
        reason="야외 활동 선호",
        status=status,
        feedback=feedback,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        **({} if minutes_ago is None else {"created_at": _NOW - timedelta(minutes=minutes_ago)}),
    )
    session.add(row)
    await session.flush()
    return row


async def _list(db_client: AsyncClient, headers: dict, cid: uuid.UUID, **params) -> dict:
    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ids(body: dict) -> set[str]:
    return {i["id"] for i in body["items"]}


# ── rated 필터 ───────────────────────────────────────────────────────────


async def test_r1_without_rated_returns_everything_except_draft(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    pending = await _make(session, cid)
    liked = await _make(session, cid, feedback=SuggestionFeedback.CHILD_LIKED)
    draft = await _make(session, cid, status=SuggestionStatus.DRAFT)

    body = await _list(db_client, headers, cid)

    assert _ids(body) == {str(pending.id), str(liked.id)}
    assert str(draft.id) not in _ids(body)
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[str(liked.id)]["feedback"] == "child_liked"
    assert by_id[str(pending.id)]["feedback"] is None


async def test_r2_rated_false_returns_only_unrated(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    pending = await _make(session, cid)
    await _make(session, cid, feedback=SuggestionFeedback.CHILD_LIKED)

    body = await _list(db_client, headers, cid, rated="false")

    assert _ids(body) == {str(pending.id)}
    assert [i["feedback"] for i in body["items"]] == [None]
    assert body["total"] == 1


@pytest.mark.parametrize("value", list(SuggestionFeedback))
async def test_r3_rated_true_returns_only_rated_whatever_the_value(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    value: SuggestionFeedback,
):
    """`not_acted`("반응이 딱히 없음") 도 평가한 것이다 — 값이 아니라 NULL 여부로 가른다."""
    headers, _ = bearer
    await _make(session, cid)
    rated = await _make(session, cid, feedback=value)

    body = await _list(db_client, headers, cid, rated="true")

    assert _ids(body) == {str(rated.id)}
    assert [i["feedback"] for i in body["items"]] == [value.value]
    assert body["total"] == 1


async def test_r4_rated_true_and_false_partition_the_default_list(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    await _make(session, cid)
    await _make(session, cid, status=SuggestionStatus.REJECTED)
    await _make(session, cid, feedback=SuggestionFeedback.CHILD_LIKED)
    await _make(session, cid, feedback=SuggestionFeedback.NOT_ACTED)

    everything = await _list(db_client, headers, cid)
    rated = await _list(db_client, headers, cid, rated="true")
    unrated = await _list(db_client, headers, cid, rated="false")

    assert _ids(rated).isdisjoint(_ids(unrated))
    assert _ids(rated) | _ids(unrated) == _ids(everything)
    assert (everything["total"], rated["total"], unrated["total"]) == (4, 2, 2)


async def test_r5_feedback_moves_a_suggestion_and_reselecting_overwrites(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    s = await _make(session, cid)
    url = f"/api/v1/children/{cid}/suggestions/{s.id}/feedback"

    assert _ids(await _list(db_client, headers, cid, rated="false")) == {str(s.id)}

    resp = await db_client.patch(url, json={"feedback": "child_liked"}, headers=headers)
    assert resp.status_code == 200
    assert await _list(db_client, headers, cid, rated="false") == {
        "items": [],
        "total": 0,
        "page": 1,
        "size": 20,
        "total_pages": 0,
    }
    liked = await _list(db_client, headers, cid, rated="true")
    assert [(i["id"], i["feedback"]) for i in liked["items"]] == [(str(s.id), "child_liked")]

    # 다시 고르면 막히지 않고 값만 바뀐다 — 평가한 제안 보기에서 고칠 수 있어야 한다
    resp = await db_client.patch(url, json={"feedback": "child_disliked"}, headers=headers)
    assert resp.status_code == 200
    again = await _list(db_client, headers, cid, rated="true")
    assert [(i["id"], i["feedback"]) for i in again["items"]] == [(str(s.id), "child_disliked")]
    assert (await _list(db_client, headers, cid, rated="false"))["total"] == 0


async def test_r6_rated_is_anded_with_status(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    approved = await _make(session, cid)
    rejected = await _make(session, cid, status=SuggestionStatus.REJECTED)
    liked_rejected = await _make(
        session, cid, status=SuggestionStatus.REJECTED, feedback=SuggestionFeedback.CHILD_LIKED
    )
    draft = await _make(session, cid, status=SuggestionStatus.DRAFT)

    assert _ids(await _list(db_client, headers, cid, status="approved", rated="false")) == {
        str(approved.id)
    }
    assert _ids(await _list(db_client, headers, cid, status="rejected", rated="false")) == {
        str(rejected.id)
    }
    assert _ids(await _list(db_client, headers, cid, status="rejected", rated="true")) == {
        str(liked_rejected.id)
    }
    # draft 는 평가를 받을 수 없다(PATCH 는 approved · rejected 만) — rated=true 로는 안 나온다
    assert (await _list(db_client, headers, cid, status="draft", rated="true"))["items"] == []
    assert _ids(await _list(db_client, headers, cid, status="draft", rated="false")) == {
        str(draft.id)
    }


async def test_r7_rated_filters_never_let_a_draft_into_the_default_list(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    draft = await _make(session, cid, status=SuggestionStatus.DRAFT)

    for rated in ("true", "false"):
        body = await _list(db_client, headers, cid, rated=rated)
        assert str(draft.id) not in _ids(body)
        assert body["total"] == 0


async def test_r8_other_childs_suggestions_never_mix_in(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    mine = await _make(session, cid, feedback=SuggestionFeedback.CHILD_LIKED)
    await _make(session, other_cid, feedback=SuggestionFeedback.CHILD_LIKED)
    await _make(session, other_cid)

    assert _ids(await _list(db_client, headers, cid, rated="true")) == {str(mine.id)}
    assert (await _list(db_client, headers, cid, rated="false"))["total"] == 0
    assert (await _list(db_client, headers, cid))["total"] == 1


async def test_r9_general_suggestions_follow_the_same_filter(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    general_rated = await _make(
        session, cid, kind=SuggestionKind.GENERAL, feedback=SuggestionFeedback.CHILD_DISLIKED
    )
    general_pending = await _make(session, cid, kind=SuggestionKind.GENERAL)

    rated = await _list(db_client, headers, cid, rated="true")
    unrated = await _list(db_client, headers, cid, rated="false")

    assert [(i["id"], i["kind"]) for i in rated["items"]] == [(str(general_rated.id), "general")]
    assert [(i["id"], i["kind"]) for i in unrated["items"]] == [
        (str(general_pending.id), "general")
    ]


async def test_r11_expired_row_shows_in_rated_false_and_never_in_rated_true(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    expired = await _make(session, cid, status=SuggestionStatus.EXPIRED)
    pending = await _make(session, cid)

    unrated = await _list(db_client, headers, cid, rated="false")
    rated = await _list(db_client, headers, cid, rated="true")
    only_expired = await _list(db_client, headers, cid, status="expired", rated="false")

    assert _ids(unrated) == {str(expired.id), str(pending.id)}
    assert rated["items"] == []
    assert _ids(only_expired) == {str(expired.id)}
    # 평가는 저장되지 않는다 — 목록에 남아 있어도 expired 는 평가할 수 없다
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/suggestions/{expired.id}/feedback",
        json={"feedback": "child_liked"},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("value", ["abc", "2", "maybe"])
async def test_r10_non_boolean_rated_is_bad_request_not_unprocessable(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID, value: str
):
    headers, _ = bearer

    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions", params={"rated": value}, headers=headers
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_failed"


# ── 쪽 나누기 ────────────────────────────────────────────────────────────


async def test_p1_response_has_exactly_the_paging_fields(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    await _make(session, cid)

    body = await _list(db_client, headers, cid)

    assert set(body) == {"items", "total", "page", "size", "total_pages"}


async def test_p2_pages_through_newest_first_without_gaps(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    created = [str((await _make(session, cid, minutes_ago=m)).id) for m in range(5)]
    # minutes_ago 0 이 가장 최근 — 응답은 이 순서(최신순)여야 한다

    pages = [await _list(db_client, headers, cid, page=p, size=2) for p in (1, 2, 3, 4)]

    assert [[i["id"] for i in page["items"]] for page in pages] == [
        created[0:2],
        created[2:4],
        created[4:5],
        [],  # 범위를 넘은 쪽은 오류가 아니라 빈 목록
    ]
    assert {(page["total"], page["size"], page["total_pages"]) for page in pages} == {(5, 2, 3)}
    assert [page["page"] for page in pages] == [1, 2, 3, 4]


async def test_p3_rows_sharing_a_created_at_are_not_skipped_or_repeated_across_pages(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    rows = [await _make(session, cid) for _ in range(7)]  # 한 트랜잭션 — created_at 이 모두 같다
    assert len({r.created_at for r in rows}) == 1
    expected = [str(r.id) for r in sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)]

    walked: list[str] = []
    for page in (1, 2, 3, 4):
        walked += [
            i["id"] for i in (await _list(db_client, headers, cid, page=page, size=2))["items"]
        ]

    assert walked == expected  # 순서까지 id DESC 로 고정이고, 빠지거나 겹치지 않는다


async def test_p4_total_is_counted_after_every_filter_and_ignores_the_page(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    for m in range(5):
        await _make(session, cid, minutes_ago=m)
    for m in range(3):
        await _make(session, cid, feedback=SuggestionFeedback.CHILD_LIKED, minutes_ago=10 + m)
    await _make(session, cid, status=SuggestionStatus.DRAFT)  # 기본 목록에서 빠진다

    unrated = await _list(db_client, headers, cid, rated="false", page=2, size=2)
    rated = await _list(db_client, headers, cid, rated="true", page=1, size=2)
    everything = await _list(db_client, headers, cid, page=3, size=3)

    assert (unrated["total"], unrated["total_pages"], len(unrated["items"])) == (5, 3, 2)
    assert (rated["total"], rated["total_pages"], len(rated["items"])) == (3, 2, 2)
    assert (everything["total"], everything["total_pages"], len(everything["items"])) == (8, 3, 2)


@pytest.mark.parametrize(
    ("created", "params", "expected_total_pages"),
    [
        (0, {}, 0),
        (0, {"size": 5}, 0),
        (1, {"size": 5}, 1),
        (5, {"size": 5}, 1),
        (6, {"size": 5}, 2),
        (5, {"size": 2}, 3),
    ],
)
async def test_p5_total_pages_is_ceil_of_total_over_size_and_zero_when_empty(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    created: int,
    params: dict[str, int],
    expected_total_pages: int,
):
    headers, _ = bearer
    for m in range(created):
        await _make(session, cid, minutes_ago=m)

    body = await _list(db_client, headers, cid, **params)

    assert (body["total"], body["total_pages"]) == (created, expected_total_pages)


async def test_p6_defaults_are_page_1_size_20_and_are_echoed(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    for m in range(21):
        await _make(session, cid, minutes_ago=m)

    body = await _list(db_client, headers, cid)

    assert (body["page"], body["size"], body["total"], body["total_pages"]) == (1, 20, 21, 2)
    assert len(body["items"]) == 20


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"page": 1, "size": 1}, 200),
        ({"page": 10_000, "size": 100}, 200),  # 상한 그대로는 허용
        ({"page": 0}, 400),
        ({"page": -1}, 400),
        ({"page": 10_001}, 400),
        ({"page": 10**20}, 400),  # DB 정수 범위를 넘는 값이 500 으로 새면 안 된다
        ({"size": 0}, 400),
        ({"size": 101}, 400),
        ({"page": "x"}, 400),
    ],
)
async def test_p7_out_of_range_page_or_size_is_a_bad_request_not_unprocessable(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID, params: dict, expected: int
):
    headers, _ = bearer

    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions", params=params, headers=headers
    )

    assert resp.status_code == expected
    if expected == 400:
        assert resp.json()["error"]["code"] == "validation_failed"


@pytest.mark.parametrize(
    "params", [{}, {"rated": "true", "page": 2, "size": 5}, {"status": "draft"}]
)
async def test_p8_other_childs_list_is_forbidden_whatever_the_query(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, params: dict
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)

    resp = await db_client.get(
        f"/api/v1/children/{other_cid}/suggestions", params=params, headers=headers
    )

    assert resp.status_code == 403


def test_p9_swagger_lists_page_size_rated_status_and_no_cursor():
    """과도기 코드를 남기지 않는다 — cursor · limit 이 문서에 남아 있으면 계약이 둘이 된다."""
    params = app.openapi()["paths"]["/api/v1/children/{cid}/suggestions"]["get"]["parameters"]
    names = {p["name"] for p in params}
    page = next(p for p in params if p["name"] == "page")

    assert {"page", "size", "rated", "status"} <= names
    assert names.isdisjoint({"cursor", "limit"})
    assert page["schema"]["maximum"] == 10_000


def test_p9_swagger_response_has_total_fields_and_no_next_cursor():
    schemas = app.openapi()["components"]["schemas"]
    props = schemas["SuggestionListResponse"]["properties"]

    assert set(props) == {"items", "total", "page", "size", "total_pages"}


async def test_p10_evidence_is_attached_on_every_page(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    affinity = ProfileAffinity(
        child_id=cid,
        merge_key="공원 산책",
        domain=MemoryDomain.ACTIVITY,
        last_observed_on=datetime.now(UTC).date(),
    )
    session.add(affinity)
    await session.flush()
    rows = [await _make(session, cid, minutes_ago=m) for m in range(3)]
    for row in rows:
        session.add(
            SuggestionEvidence(
                suggestion_id=row.id,
                source_kind="profile_affinity",
                source_id=affinity.id,
                note="반복 3회",
            )
        )
    await session.flush()

    second = await _list(db_client, headers, cid, page=2, size=2)

    assert [i["id"] for i in second["items"]] == [str(rows[2].id)]
    assert [len(i["evidence"]) for i in second["items"]] == [1]
