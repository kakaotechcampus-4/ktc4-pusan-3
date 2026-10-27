"""관찰 목록 · 상세 API — #259.

저장 함수(필터 · 커서 · 삭제 표시)는 `tests/integration/test_observation_repository.py` 가 건다.
여기는 API 가 더하는 것만 본다: 분류 → 표 매핑, 건강 제외, 응답 조립, 쪽(page · size) · 날짜 · 검색.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import today_kst
from app.domains.correction.models import CorrectionTargetKind, CorrectionVerdict
from app.domains.correction.repository import append_correction
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import create_observation
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
)
from app.main import app
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

_FIELDS = {
    "food": {"subject": "사과", "action": "먹음"},
    "education": {"subject": "그림책", "topic": "그림책"},
    "activity": {"subject": "블록", "activity": "블록 쌓기"},
    "routine": {"subject": "양치", "routine_category": RoutineCategory.SELF_CARE},
    "health": {"symptom": ["기침"]},
}


async def _observe(
    session: AsyncSession,
    child_id: uuid.UUID,
    writer: uuid.UUID,
    domain: str,
    days_ago: int = 0,
    raw_text: str | None = None,
):
    day = today_kst() - timedelta(days=days_ago)
    return await create_observation(
        session,
        domain=domain,
        child_id=child_id,
        source_writer=writer,
        raw_text=raw_text or f"{domain} 원문",
        observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, **_FIELDS[domain]},
    )


async def test_other_childs_observations_are_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    record = await _observe(session, other_cid, stranger, "food")

    listed = await db_client.get(f"/api/v1/children/{other_cid}/observations", headers=headers)
    detail = await db_client.get(
        f"/api/v1/children/{other_cid}/observations/observation_food/{record.id}", headers=headers
    )
    assert (listed.status_code, detail.status_code) == (403, 403)


async def test_list_maps_agent_to_tables_and_hides_health(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """growth 는 education · routine 두 표, health 는 빈 목록, 전체에도 건강은 없다."""
    headers, parent_id = bearer
    for domain in _FIELDS:
        await _observe(session, cid, parent_id, domain)
    url = f"/api/v1/children/{cid}/observations"

    growth = (await db_client.get(url, params={"domain": "growth"}, headers=headers)).json()
    health = (await db_client.get(url, params={"domain": "health"}, headers=headers)).json()
    everything = (await db_client.get(url, headers=headers)).json()

    assert {i["kind"] for i in growth["items"]} == {
        "observation_education",
        "observation_routine",
    }
    assert health == {"items": [], "total": 0, "page": 1, "size": 20, "total_pages": 0}
    assert everything["total"] == 4
    assert "observation_health" not in {i["kind"] for i in everything["items"]}


async def test_list_keeps_corrected_observations_but_not_deleted_ones(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """고친 기록(이번만 · 잘못된 기록)은 목록에 남고 status 로 구분된다. 지운 것만 빠진다."""
    headers, parent_id = bearer
    kept = {}
    for status in ObservationStatus:
        record = await _observe(session, cid, parent_id, "food")
        await session.execute(
            update(ObservationFood).where(ObservationFood.id == record.id).values(status=status)
        )
        kept[str(record.id)] = status.value

    listed = (await db_client.get(f"/api/v1/children/{cid}/observations", headers=headers)).json()

    assert {i["id"]: i["status"] for i in listed["items"]} == {
        id_: status for id_, status in kept.items() if status != "deleted"
    }
    assert listed["total"] == 3
    only = (
        await db_client.get(
            f"/api/v1/children/{cid}/observations",
            params={"status": "stand_alone"},
            headers=headers,
        )
    ).json()
    assert [i["status"] for i in only["items"]] == ["stand_alone"]
    assert only["total"] == 1


async def test_pages_through_newest_first_without_gaps_and_reports_total_pages(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    created = [
        str((await _observe(session, cid, parent_id, "food", days_ago=d)).id) for d in range(5)
    ]  # days_ago 0 이 가장 최근 — 응답은 이 순서(최신순)여야 한다
    url = f"/api/v1/children/{cid}/observations"

    pages = [
        (await db_client.get(url, params={"page": p, "size": 2}, headers=headers)).json()
        for p in (1, 2, 3, 4)
    ]

    assert [[i["id"] for i in page["items"]] for page in pages] == [
        created[0:2],
        created[2:4],
        created[4:5],
        [],  # 범위를 넘은 쪽은 오류가 아니라 빈 목록
    ]
    assert {(page["total"], page["size"], page["total_pages"]) for page in pages} == {(5, 2, 3)}
    assert [page["page"] for page in pages] == [1, 2, 3, 4]


async def test_list_has_no_cursor_anymore(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    await _observe(session, cid, parent_id, "food")

    body = (await db_client.get(f"/api/v1/children/{cid}/observations", headers=headers)).json()

    assert set(body) == {"items", "total", "page", "size", "total_pages"}


async def test_date_range_keeps_overlapping_records_and_includes_both_boundaries(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    for d in range(5):
        await _observe(session, cid, parent_id, "food", days_ago=d)
    today = today_kst()
    url = f"/api/v1/children/{cid}/observations"

    window = (
        await db_client.get(
            url,
            params={
                "date_from": (today - timedelta(days=3)).isoformat(),
                "date_to": (today - timedelta(days=1)).isoformat(),
            },
            headers=headers,
        )
    ).json()
    one_day = (
        await db_client.get(
            url,
            params={"date_from": today.isoformat(), "date_to": today.isoformat()},
            headers=headers,
        )
    ).json()

    assert window["total"] == 3 and len(window["items"]) == 3
    assert one_day["total"] == 1


async def test_q_filters_by_raw_text_and_total_counts_only_matches(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    hit = await _observe(session, cid, parent_id, "food", 0, raw_text="점심에 브로콜리를 먹음")
    await _observe(session, cid, parent_id, "food", 1, raw_text="사과를 먹음")
    await _observe(session, cid, parent_id, "activity", 2, raw_text="브로콜리 그림 그리기")
    url = f"/api/v1/children/{cid}/observations"

    found = (await db_client.get(url, params={"q": "브로콜리"}, headers=headers)).json()
    food_only = (
        await db_client.get(url, params={"q": "브로콜리", "domain": "food"}, headers=headers)
    ).json()
    paged = (
        await db_client.get(url, params={"q": "브로콜리", "size": 1, "page": 2}, headers=headers)
    ).json()

    assert found["total"] == 2
    assert [i["id"] for i in food_only["items"]] == [str(hit.id)] and food_only["total"] == 1
    assert (paged["total"], paged["total_pages"], len(paged["items"])) == (2, 2, 1)


async def test_blank_q_means_no_search_and_wildcards_are_plain_characters(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    await _observe(session, cid, parent_id, "food", 0, raw_text="주스 100% 먹음")
    await _observe(session, cid, parent_id, "food", 1, raw_text="사과를 먹음")
    url = f"/api/v1/children/{cid}/observations"

    blank = (await db_client.get(url, params={"q": "   "}, headers=headers)).json()
    percent = (await db_client.get(url, params={"q": "%"}, headers=headers)).json()

    assert blank["total"] == 2
    assert percent["total"] == 1


async def test_filters_combine_with_paging_and_total_is_after_every_filter(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    for d in range(4):
        await _observe(session, cid, parent_id, "food", d, raw_text="브로콜리")
    await _observe(session, cid, parent_id, "activity", 0, raw_text="브로콜리")
    await _observe(session, cid, parent_id, "food", 9, raw_text="브로콜리")  # 날짜 밖
    today = today_kst()

    body = (
        await db_client.get(
            f"/api/v1/children/{cid}/observations",
            params={
                "domain": "food",
                "q": "브로콜리",
                "date_from": (today - timedelta(days=5)).isoformat(),
                "date_to": today.isoformat(),
                "status": "active",
                "page": 2,
                "size": 3,
            },
            headers=headers,
        )
    ).json()

    assert (body["total"], body["total_pages"], len(body["items"])) == (4, 2, 1)


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"size": 0},
        {"size": 101},
        {"q": "가" * 101},
        {"date_from": "2026-13-01"},
        {"date_to": "어제"},
        {"date_from": "2026-10-31", "date_to": "2026-10-01"},
    ],
)
async def test_invalid_query_is_bad_request_not_unprocessable(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID, params: dict[str, str | int]
):
    """쿼리 검증 실패는 422 가 아니라 400 validation_failed 다 (errors.py — 경로 값만 422)."""
    headers, _ = bearer

    resp = await db_client.get(
        f"/api/v1/children/{cid}/observations", params=params, headers=headers
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_failed"


async def test_detail_carries_affinity_writer_usage_and_corrections(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    parent = await session.get(Parent, parent_id)
    parent.nickname = "엄마"
    affinity = ProfileAffinity(
        child_id=cid, merge_key="사과", domain=MemoryDomain.FOOD, last_observed_on=date.today()
    )
    session.add(affinity)
    await session.flush()
    record = await _observe(session, cid, parent_id, "food", days_ago=1)
    row = await session.get(ObservationFood, record.id)
    row.affinity_id = affinity.id
    suggestion = Suggestion(
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        kind=SuggestionKind.PERSONALIZED,
        content="사과 조각을 곁들여 보세요",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(suggestion)
    await session.flush()
    session.add(
        SuggestionEvidence(
            suggestion_id=suggestion.id,
            source_kind="observation_food",
            source_id=record.id,
            note="사과",
        )
    )
    await append_correction(
        session,
        child_id=cid,
        target_kind=CorrectionTargetKind.OBSERVATION_FOOD,
        target_id=record.id,
        verdict=CorrectionVerdict.ONCE_ONLY,
        parent_id=parent_id,
    )
    await session.flush()

    body = (
        await db_client.get(
            f"/api/v1/children/{cid}/observations/observation_food/{record.id}", headers=headers
        )
    ).json()

    observation = body["observation"]
    assert observation["observed_label"] == "어제"
    assert observation["affinity"]["merge_key"] == "사과"
    assert observation["source_writer"]["nickname"] == "엄마"
    assert observation["domain_fields"] == {"action": "먹음", "amount": None, "reaction": None}
    assert [u["suggestion_id"] for u in body["used_in"]] == [str(suggestion.id)]
    assert [c["verdict"] for c in body["corrections"]] == ["once_only"]


async def test_health_detail_is_not_found(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    record = await _observe(session, cid, parent_id, "health")
    resp = await db_client.get(
        f"/api/v1/children/{cid}/observations/observation_health/{record.id}", headers=headers
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("created", "params", "expected_total_pages"),
    [
        (0, {}, 0),
        (4, {"size": 2}, 2),  # 딱 나누어떨어진다
        (4, {"size": 4}, 1),
        (4, {"size": 100}, 1),
        (5, {"size": 2}, 3),
    ],
)
async def test_total_pages_is_ceil_of_total_over_size_and_zero_when_empty(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    created: int,
    params: dict[str, int],
    expected_total_pages: int,
):
    headers, parent_id = bearer
    for d in range(created):
        await _observe(session, cid, parent_id, "food", days_ago=d)

    body = (
        await db_client.get(f"/api/v1/children/{cid}/observations", params=params, headers=headers)
    ).json()

    assert (body["total"], body["total_pages"]) == (created, expected_total_pages)


async def test_q_without_a_match_has_zero_total_pages(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    await _observe(session, cid, parent_id, "food", raw_text="사과")

    body = (
        await db_client.get(
            f"/api/v1/children/{cid}/observations", params={"q": "없는말"}, headers=headers
        )
    ).json()

    assert (body["items"], body["total"], body["total_pages"]) == ([], 0, 0)


async def test_q_allows_exactly_100_characters_and_trims_the_edges(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    await _observe(session, cid, parent_id, "food", raw_text="점심에 브로콜리를 먹음")
    await _observe(session, cid, parent_id, "food", days_ago=1, raw_text="사과를 먹음")
    url = f"/api/v1/children/{cid}/observations"

    longest = await db_client.get(url, params={"q": "가" * 100}, headers=headers)
    padded = (await db_client.get(url, params={"q": " 브로콜리 "}, headers=headers)).json()

    assert longest.status_code == 200
    assert padded["total"] == 1  # 가장자리 공백이 검색어에 남으면 "% 브로콜리 %" 라 안 걸린다


async def test_health_domain_echoes_page_and_size_and_still_validates(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """건강은 빈 목록이지만 고정 응답이 아니다 — 요청한 page · size 를 되돌리고 검증도 한다."""
    headers, _ = bearer
    url = f"/api/v1/children/{cid}/observations"

    empty = (
        await db_client.get(url, params={"domain": "health", "page": 3, "size": 5}, headers=headers)
    ).json()
    invalid = await db_client.get(
        url,
        params={"domain": "health", "date_from": "2026-10-31", "date_to": "2026-10-01"},
        headers=headers,
    )

    assert empty == {"items": [], "total": 0, "page": 3, "size": 5, "total_pages": 0}
    assert invalid.status_code == 400


async def test_other_childs_list_is_forbidden_whatever_the_query(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)

    resp = await db_client.get(
        f"/api/v1/children/{other_cid}/observations",
        params={"page": 1, "size": 5, "q": "사과", "date_from": "2026-01-01"},
        headers=headers,
    )

    assert resp.status_code == 403


def test_swagger_lists_the_new_params_and_no_cursor():
    """과도기 코드를 남기지 않는다 — cursor · limit 이 문서에 남아 있으면 계약이 둘이 된다."""
    params = app.openapi()["paths"]["/api/v1/children/{cid}/observations"]["get"]["parameters"]
    names = {p["name"] for p in params}

    assert {"page", "size", "q", "date_from", "date_to", "domain", "status"} <= names
    assert names.isdisjoint({"cursor", "limit"})
