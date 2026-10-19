"""지난 대화 다시 보기 — `GET /children/{cid}/chat` (#228).

    ① 내 대화만 — 같은 아이와 연결된 다른 보호자의 대화는 없는 것처럼
    ② 하루 안은 최근 것부터 끊고, 한 페이지 안은 오래된 순 — next_cursor 로 이어 받는다
    ③ prev_date · next_date 는 대화가 없는 날을 건너뛴다 — 처음 · 마지막 날이면 null
    ④ 깨진 커서는 400
    ⑥ 못 읽는 답이 있어도 그날은 열린다 — 그 답만 빠진다 (배포를 되돌린 뒤 옛 코드)
    ⑤ 저장한 기록은 지금 상태로 — run 화면의 saved 와 같은 모양(건강 포함), 지운 기록은 내용 없이
    ⑦ 추천은 지금 상태로 — 24시간 지난 draft 는 expired, 못 찾은 id 는 빠진다
    ⑧ 되묻기는 맥락이 살아 있을 때만 reply_open

아이 접근 확인(403)은 다른 아이 주소와 같은 의존성이라 여기서 다시 보지 않는다.
무엇을 어떻게 남기는지는 tests/integration/test_chat_record.py. 발화는 전부 가짜 값이다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.entrypoint import PendingMemoryContext
from app.agents.memory.schemas.task import WorkType
from app.api.runs import pending_reply
from app.domains.chat.repository import add_turn
from app.domains.memory.observation.models import ConfidenceSource
from app.domains.memory.observation.repository import create_observation, delete_observation
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionKind,
    SuggestionStatus,
)
from app.domains.suggestion.repository import create_suggestion
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

CHAT = "/api/v1/children/{cid}/chat"
DAY = date(2026, 10, 15)
NOON = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # KST 12:00


async def _turn(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    day: date = DAY,
    at: datetime = NOON,
    text: str = "synthetic line",
    replies: list | None = None,
) -> str:
    run_id = uuid.uuid4().hex
    await add_turn(
        session,
        parent_id=parent_id,
        child_id=child_id,
        run_id=run_id,
        conversation_date=day,
        raw_text=text,
        reply_to=None,
        replies=replies or [{"type": "done", "completed": True}],
        created_at=at,
    )
    return run_id


async def test_shows_only_my_turns(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """대화는 쓴 보호자 본인의 개인 기록이다 (#228 10-06) — 함께 보는 보호자에게도 보이지 않는다."""
    headers, me = bearer
    _, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=cid)
    mine = await _turn(session, parent_id=me, child_id=cid, text="mine")
    await _turn(session, parent_id=other, child_id=cid, text="theirs")

    response = await db_client.get(
        CHAT.format(cid=cid), params={"date": "2026-10-15"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert [t["run_id"] for t in body["turns"]] == [mine]
    assert body["turns"][0]["replies"] == [{"type": "done", "completed": True}]


async def test_pages_a_day_from_the_latest_and_keeps_each_page_in_order(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """대화 화면은 아래(최근)부터 그리고 위로 올리면 이전 것을 붙인다."""
    headers, me = bearer
    first, second, third = [
        await _turn(session, parent_id=me, child_id=cid, at=NOON + timedelta(minutes=m))
        for m in (0, 10, 20)
    ]
    url = CHAT.format(cid=cid)

    latest = (
        await db_client.get(url, params={"date": "2026-10-15", "limit": 2}, headers=headers)
    ).json()
    earlier = (
        await db_client.get(
            url,
            params={"date": "2026-10-15", "limit": 2, "cursor": latest["next_cursor"]},
            headers=headers,
        )
    ).json()

    assert [t["run_id"] for t in latest["turns"]] == [second, third]
    assert [t["run_id"] for t in earlier["turns"]] == [first]
    assert earlier["next_cursor"] is None


async def test_cursor_keeps_its_day_across_midnight(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """23:59 에 받은 커서를 자정 뒤에 날짜 없이 보내도 그날을 잇는다 — 오늘에 적용하면 빈 목록과
    prev_date=어제가 나가 화면이 어제를 처음부터 다시 그리고, 보이던 턴이 두 번 생긴다."""
    headers, me = bearer
    first, _ = [
        await _turn(session, parent_id=me, child_id=cid, at=NOON + timedelta(minutes=m))
        for m in (0, 10)
    ]
    url = CHAT.format(cid=cid)
    latest = (
        await db_client.get(url, params={"date": "2026-10-15", "limit": 1}, headers=headers)
    ).json()

    earlier = await db_client.get(url, params={"cursor": latest["next_cursor"]}, headers=headers)
    mismatched = await db_client.get(
        url, params={"date": "2026-10-16", "cursor": latest["next_cursor"]}, headers=headers
    )

    assert earlier.json()["date"] == "2026-10-15"
    assert [t["run_id"] for t in earlier.json()["turns"]] == [first]
    assert mismatched.status_code == 400


async def test_prev_and_next_date_skip_days_without_turns(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """캘린더에서 지난 날을 바로 열면 위로는 prev_date, 아래로는 next_date 로 잇는다 (#500 리뷰)."""
    headers, me = bearer
    await _turn(session, parent_id=me, child_id=cid, day=date(2026, 10, 12))
    await _turn(session, parent_id=me, child_id=cid, day=DAY)
    url = CHAT.format(cid=cid)

    today = (await db_client.get(url, params={"date": "2026-10-15"}, headers=headers)).json()
    first = (await db_client.get(url, params={"date": "2026-10-12"}, headers=headers)).json()

    assert (today["prev_date"], today["next_date"]) == ("2026-10-12", None)
    assert (first["prev_date"], first["next_date"]) == (None, "2026-10-15")


async def test_broken_cursor_is_400(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """형만 틀린 커서가 500 으로 새지 않는다 — 기록 탭 커서에서 실제로 있었다 (#259)."""
    headers, _ = bearer

    response = await db_client.get(
        CHAT.format(cid=cid),
        params={"cursor": "WzFd"},  # "[1]"
        headers=headers,
    )

    assert response.status_code == 400


async def _observation(
    session: AsyncSession, *, domain: str, child_id: uuid.UUID, writer: uuid.UUID, fields: dict
) -> str:
    record = await create_observation(
        session,
        domain=domain,
        child_id=child_id,
        source_writer=writer,
        raw_text="synthetic record",
        observed_range=Range(DAY, DAY + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, **fields},
    )
    return str(record.id)


async def test_saved_records_come_back_as_they_are_now(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """지운 기록이 지난 대화에서 되살아나면 안 된다 (#228 완료 기준).

    건강 기록은 run 화면의 saved(#471)처럼 건강 모양으로 실린다 — 그 자리에서 본 카드와 같게.
    """
    headers, me = bearer
    kept = await _observation(
        session, domain="food", child_id=cid, writer=me, fields={"subject": "계란말이"}
    )
    erased = await _observation(
        session, domain="food", child_id=cid, writer=me, fields={"subject": "당근"}
    )
    health = await _observation(
        session, domain="health", child_id=cid, writer=me, fields={"symptom": ["기침"]}
    )
    await delete_observation(session, domain="food", child_id=cid, observation_id=uuid.UUID(erased))
    saved = {
        "type": "saved",
        "refs": [
            {"kind": "observation_food", "id": kept},
            {"kind": "observation_food", "id": erased},
            {"kind": "observation_health", "id": health},
        ],
    }
    done = {"type": "done", "completed": True}
    await _turn(session, parent_id=me, child_id=cid, replies=[saved, done])

    body = (
        await db_client.get(CHAT.format(cid=cid), params={"date": "2026-10-15"}, headers=headers)
    ).json()

    [turn] = body["turns"]
    [filled, _] = turn["replies"]
    assert [(o["kind"], o["id"]) for o in filled["observations"]] == [
        ("observation_food", kept),
        ("observation_health", health),
    ]
    assert filled["deleted"] == [{"kind": "observation_food", "id": erased}]


async def test_unreadable_reply_is_skipped_not_the_whole_day(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """배포를 되돌리면 옛 코드가 새 코드가 쓴 행을 읽는다 (#498 리뷰). 늘어난 칸은 버리고, 모르는
    종류의 답은 그 답만 뺀다 — 하루치를 한 번에 검증하면 그날 대화 전체가 500 이 된다."""
    headers, me = bearer
    done = {"type": "done", "completed": True}
    run_id = await _turn(
        session,
        parent_id=me,
        child_id=cid,
        replies=[
            {"type": "note", "text": "기록했어요.", "kind": "message", "added_later": 1},
            {"type": "added_later", "body": "synthetic"},
            done,
        ],
    )

    response = await db_client.get(
        CHAT.format(cid=cid), params={"date": "2026-10-15"}, headers=headers
    )

    assert response.status_code == 200
    [turn] = response.json()["turns"]
    assert (turn["run_id"], turn["raw_text"]) == (run_id, "synthetic line")
    assert turn["replies"] == [
        {"type": "note", "text": "기록했어요.", "kind": "message"},
        done,
    ]


async def test_suggestions_come_back_with_their_current_status(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """며칠 지난 턴의 "후보 보기" 가 막다른 길이 되지 않게 화면이 고를 수 있는 추천을 가른다
    (#500 리뷰).

    🚨 만료는 상태 칸에 써지지 않는다 — 24시간이 지난 draft 를 그대로 내보내면 고를 수 있는 것처럼
    보인다.
    """
    headers, me = bearer
    now = datetime.now(UTC)

    async def suggestion(expires_at: datetime) -> str:
        row = await create_suggestion(
            session,
            child_id=cid,
            agent=SuggestionAgent.FOOD,
            kind=SuggestionKind.GENERAL,
            content="synthetic menu",
            reason=None,
            citations=[],
            expires_at=expires_at,
        )
        return str(row.id)

    alive = await suggestion(now + timedelta(hours=1))
    lapsed = await suggestion(now - timedelta(hours=1))
    approved = await suggestion(now - timedelta(hours=1))
    row = await session.get(Suggestion, uuid.UUID(approved))
    row.status = SuggestionStatus.APPROVED
    await session.flush()
    gone = str(uuid.uuid4())
    result = {
        "type": "agent_result",
        "agent": "food",
        "task_type": "meal_recommendation",
        "status": "completed",
        "readouts": [],
        "question": None,
        "suggestion_ids": [alive, lapsed, gone, approved],
        "kind": "general",
        "reason": None,
        "record_count": 3,
    }
    await _turn(
        session, parent_id=me, child_id=cid, replies=[result, {"type": "done", "completed": True}]
    )

    body = (
        await db_client.get(CHAT.format(cid=cid), params={"date": "2026-10-15"}, headers=headers)
    ).json()

    [turn] = body["turns"]
    [filled, _] = turn["replies"]
    assert filled["suggestion_ids"] == [alive, lapsed, gone, approved]
    assert filled["suggestions"] == [
        {"id": alive, "status": "draft"},
        {"id": lapsed, "status": "expired"},
        {"id": approved, "status": "approved"},
    ]


@pytest.fixture
def _no_pending():
    pending_reply.clear()
    yield
    pending_reply.clear()


@pytest.mark.usefixtures("_no_pending")
async def test_reply_open_only_while_the_question_context_lives(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """앱을 다시 켜도 15분 안이면 이어서 답할 수 있다 (#500 리뷰). 맥락이 없으면(만료 · 이미 답함 ·
    서버 재시작) 닫힌다 — 열었다가 400 을 받게 하지 않는다."""
    headers, me = bearer
    question = {"type": "note", "text": "synthetic question", "kind": "question"}
    done = {"type": "done", "completed": True}
    open_run = await _turn(session, parent_id=me, child_id=cid, replies=[question, done])
    await _turn(
        session,
        parent_id=me,
        child_id=cid,
        at=NOON + timedelta(minutes=1),
        replies=[question, done],
    )
    pending_reply.put(
        run_id=open_run,
        parent_id=me,
        child_id=cid,
        context=PendingMemoryContext("synthetic", "synthetic question", WorkType.OBSERVE),
    )

    body = (
        await db_client.get(CHAT.format(cid=cid), params={"date": "2026-10-15"}, headers=headers)
    ).json()

    assert [t["reply_open"] for t in body["turns"]] == [True, False]
