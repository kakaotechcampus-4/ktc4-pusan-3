"""run 이 저장한 기록을 화면에 `saved` 로 보낸다 — #471.

기록 하나로 끝나는 입력("오늘 친구들이랑 자전거타고 놀았대")은 저장은 되는데 `saved` 가 나가지
않아서, 04 화면이 "기록은 찾지 못했어요 … 저장하지 않았어요" 라고 답했다.

실서버와 같은 길(러너 + `DbMemoryStore`)을 실제 DB 로 탄다. 진짜 Agent(LLM)만 가짜로 바꾼다 —
가짜 pipeline 은 진짜와 같은 순서로 쓴다: 관찰 쓰기 → `commit()` → `Saved(refs)` → `Done`.
러너가 여는 세션은 테스트 세션으로 바꿔 끼운다. conftest 의 session 은 commit 해도 바깥 트랜잭션이
남아서 테스트가 끝나면 같이 롤백된다.
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta

import pytest

from app.agents import entrypoint
from app.agents.common.datetime_rules import DateRange
from app.agents.entrypoint import Done, PendingMemoryContext, Ref, Saved, Step
from app.agents.memory.schemas.task import WorkType
from app.api import agent_ports, idempotency
from app.api.quota import KST, today_kst
from app.api.runs import registry, runner
from app.domains.memory.observation import repository as observation_repository

OBSERVATIONS = "/api/v1/children/{cid}/observations"
RAW_TEXT = "오늘 친구들이랑 자전거타고 놀았대"

ACTIVITY = {
    "domain": "activity",
    "raw_text": RAW_TEXT,
    "fields": {
        "subject": "자전거",
        "polarity": 1,
        "confidence_source": "parent_hearsay",
        "activity": "자전거 타기",
        "companions": "친구",
    },
}
FOOD = {
    "domain": "food",
    "raw_text": "점심에 사과 먹었어",
    "fields": {"subject": "사과", "polarity": 1, "confidence_source": "parent_direct"},
}
HEALTH = {
    "domain": "health",
    "raw_text": "어제부터 기침해",
    "fields": {"symptom": ["기침"], "confidence_source": "parent_direct"},
}


@pytest.fixture(autouse=True)
def _clean():
    registry.clear()
    idempotency.clear()
    yield
    registry.clear()
    idempotency.clear()


@pytest.fixture(autouse=True)
def _same_session(monkeypatch, session):
    """러너 · 포트가 여는 세션을 테스트 세션으로."""

    @asynccontextmanager
    async def same():
        yield session

    monkeypatch.setattr(runner, "background_session", same)
    monkeypatch.setattr(agent_ports, "background_session", same)


def _pipeline(monkeypatch, *observations: dict, delete_first: bool = False) -> list[str]:
    """진짜 pipeline 과 같은 순서로 쓰고 알리는 가짜 진입점. 만든 관찰 id 가 run 뒤에 채워진다.

    delete_first — 첫 관찰을 같은 run 에서 지운다 (Memory 가 만들고 바로 고친 경우).
    """
    created: list[str] = []

    async def fake_handle_input(**kwargs):
        emit, store = kwargs["emit"], kwargs["store"]
        emit(Step(1, 3, "적어주신 말을 읽고 있어요"))
        today = today_kst()
        refs = []
        for obs in observations:
            row = await store.create_observation(
                domain=obs["domain"],
                child_id=kwargs["child_id"],
                source_writer=kwargs["parent_id"],
                raw_text=obs["raw_text"],
                observed_on=today,
                observed_range=DateRange(start=today, end=today + timedelta(days=1)),
                fields=obs["fields"],
            )
            created.append(row.id)
            refs.append(Ref(f"observation_{obs['domain']}", row.id))
        if delete_first:
            await store.delete_observation(
                domain=observations[0]["domain"], observation_id=created[0]
            )
        await kwargs["commit"]()
        if refs:
            emit(Saved(tuple(refs)))
        emit(Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    return created


async def _run(cid, parent_id, **job_kwargs) -> registry.RunChannel:
    channel = registry.open_run(parent_id=parent_id)
    job = runner.agent_job(child_id=cid, parent_id=parent_id, raw_text=RAW_TEXT, **job_kwargs)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=5)
    return channel


def _names(channel) -> list[str]:
    return [name for name, _ in channel.events]


def _saved(channel) -> list[dict]:
    [payload] = [data for name, data in channel.events if name == "saved"]
    return payload["observations"]


async def test_기록_하나로_끝나는_입력은_commit_뒤_done_앞에_saved_를_보낸다(
    monkeypatch, bearer, cid
):
    """완료 기준 1 — 04 답에 "기록 1건 저장됨" 이 서려면 saved 가 done 앞에 와야 한다."""
    _, parent_id = bearer
    created = _pipeline(monkeypatch, ACTIVITY)

    channel = await _run(cid, parent_id)

    assert _names(channel) == ["step", "saved", "done"]
    [saved] = _saved(channel)
    assert [saved["id"]] == created
    assert saved["kind"] == "observation_activity"
    assert saved["raw_text"] == RAW_TEXT
    assert saved["subject"] == "자전거"
    assert saved["confidence_source"] == "parent_hearsay"
    assert saved["observed_label"] == "오늘"


async def test_saved_의_기록은_기록_탭과_같은_모양이다(monkeypatch, db_client, bearer, cid):
    """완료 기준 2 — 같은 기록을 07 기록 탭 API 로 읽은 것과 한 글자도 다르지 않다."""
    headers, parent_id = bearer
    _pipeline(monkeypatch, ACTIVITY)

    channel = await _run(cid, parent_id)
    listed = (await db_client.get(OBSERVATIONS.format(cid=cid), headers=headers)).json()

    assert _saved(channel) == listed["items"]


async def test_관찰_두_건은_saved_하나에_저장한_순서대로_실린다(monkeypatch, bearer, cid):
    _, parent_id = bearer
    created = _pipeline(monkeypatch, FOOD, ACTIVITY)

    channel = await _run(cid, parent_id)

    assert _names(channel).count("saved") == 1
    assert [o["id"] for o in _saved(channel)] == created


async def test_health_관찰도_saved_에_health_모양으로_실린다(monkeypatch, db_client, bearer, cid):
    """1번 결정 (#471 댓글) — 빼면 "어제부터 기침해" 가 계속 "저장하지 않았어요" 로 남는다.

    🚨 health 는 subject · polarity · strong_signals · affinity 키가 **아예 없다** (null 이 아니다).
       화면은 `kind === "observation_health"` 로 가른다 (apps/web types.ts 의 ObservationHealth).
    🚨 기록 탭에는 여전히 안 보인다 — 건강 쪽은 첫 배포 범위 밖이다 (#259).
    """
    headers, parent_id = bearer
    created = _pipeline(monkeypatch, HEALTH)

    channel = await _run(cid, parent_id)

    [saved] = _saved(channel)
    assert [saved["id"]] == created
    assert saved["kind"] == "observation_health"
    assert saved["raw_text"] == "어제부터 기침해"
    assert saved["domain_fields"]["symptom"] == ["기침"]
    assert saved["observed_label"] == "오늘"
    assert not {"subject", "polarity", "strong_signals", "affinity"} & saved.keys()

    listed = (await db_client.get(OBSERVATIONS.format(cid=cid), headers=headers)).json()
    assert listed["total"] == 0


async def test_health_관찰_시각은_한국_시간으로_싣는다(monkeypatch, bearer, cid):
    """1차 검토 1번 — DB 는 timestamptz 를 UTC 로 돌려준다. 그대로 실으면 보호자가 말한 밤 9시가
    화면에 `…T12:00:00Z` 로 9시간 어긋나 보인다. 서비스 시간대(KST)로 바꿔 싣는다.
    """
    _, parent_id = bearer
    nine_pm = datetime.combine(today_kst(), time(21, 0), KST)
    _pipeline(monkeypatch, {**HEALTH, "fields": {**HEALTH["fields"], "observed_time": nine_pm}})

    channel = await _run(cid, parent_id)

    [saved] = _saved(channel)
    assert saved["domain_fields"]["observed_time"] == nine_pm.isoformat()  # …T21:00:00+09:00


async def test_이어받기_run_도_saved_를_보낸다(monkeypatch, bearer, cid):
    """완료 기준 3 — 되묻기에 답한 run 도 같은 commit → Saved 길을 탄다."""
    _, parent_id = bearer
    created = _pipeline(monkeypatch, HEALTH)
    pending = PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)

    channel = await _run(cid, parent_id, continuation=pending, reply_to="r-prev")

    assert _names(channel) == ["step", "saved", "done"]
    assert [o["id"] for o in _saved(channel)] == created


async def test_같은_run_에서_지운_관찰은_싣지_않는다(monkeypatch, bearer, cid, caplog):
    """refs 에는 있어도 DB 에 없는 기록을 "저장됨" 으로 보이면 안 된다.

    정상 경로라 키 불일치 경고를 남기지 않는다 — 경고는 진짜 어긋남에만 (#471 리뷰).
    """
    _, parent_id = bearer
    created = _pipeline(monkeypatch, FOOD, ACTIVITY, delete_first=True)

    with caplog.at_level(logging.WARNING):
        channel = await _run(cid, parent_id)

    assert [o["id"] for o in _saved(channel)] == created[1:]
    assert "찾지 못했다" not in caplog.text


async def test_실을_기록이_하나도_없으면_saved_를_보내지_않는다(monkeypatch, bearer, cid):
    _, parent_id = bearer
    _pipeline(monkeypatch, FOOD, delete_first=True)

    channel = await _run(cid, parent_id)

    assert _names(channel) == ["step", "done"]


async def test_기록을_쓰지_않은_run_은_saved_가_없다(monkeypatch, bearer, cid):
    """일정만 있는 입력 등 — 화면은 기존대로 "일정만 찾았어요" 를 띄운다."""
    _, parent_id = bearer
    _pipeline(monkeypatch)

    channel = await _run(cid, parent_id)

    assert _names(channel) == ["step", "done"]


async def test_commit_뒤_다시_읽기가_실패해도_run_은_done_으로_끝난다(
    monkeypatch, bearer, cid, caplog
):
    """저장은 이미 확정됐다 — failed 로 끝내면 오히려 "저장 안 됐다" 로 보인다.

    saved 만 건너뛴다. 대신 코드 버그(스키마가 바뀌어 KeyError 등)가 "saved 빠짐" 으로만 묻히지 않게
    ERROR 로 남긴다 — 예외 종류와 코드 위치만, 원문 없이 (루트 §2).
    """
    _, parent_id = bearer
    _pipeline(monkeypatch, ACTIVITY)

    async def broken(*args, **kwargs):
        raise RuntimeError(f"input_value={RAW_TEXT!r}")

    monkeypatch.setattr(observation_repository, "find_observation", broken)

    with caplog.at_level(logging.WARNING):
        channel = await _run(cid, parent_id)

    assert _names(channel) == ["step", "done"]
    assert channel.ended_with == "done"
    [record] = [r for r in caplog.records if "saved 를 건너뛴다" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert "RuntimeError" in caplog.text
    assert "test_run_saved.py" in caplog.text  # 터진 곳(파일 · 줄)이 남는다
    assert RAW_TEXT not in caplog.text


async def test_다시_읽기_뒤_rollback_까지_실패해도_다음_단계로_이어진다(
    monkeypatch, session, bearer, cid, caplog
):
    """화면에 싣기 위한 부가 읽기가 run 을 끊으면 안 된다 — 혼합형이면 추천 단계가 통째로 빠진다.

    rollback 실패는 로그로 남긴다 — 같은 세션을 받는 도메인 Agent 가 뒤이어 실패할 때 원인이 보이게.
    """
    _, parent_id = bearer

    async def fake_handle_input(**kwargs):
        today = today_kst()
        await kwargs["store"].create_observation(
            domain="food",
            child_id=kwargs["child_id"],
            source_writer=kwargs["parent_id"],
            raw_text=FOOD["raw_text"],
            observed_on=today,
            observed_range=DateRange(start=today, end=today + timedelta(days=1)),
            fields=FOOD["fields"],
        )
        await kwargs["commit"]()
        kwargs["emit"](Step(3, 3, "다음 행동을 준비하고 있어요"))  # commit 뒤 추천 단계
        kwargs["emit"](Done(kwargs["run_id"], 1))

    async def broken(*args, **kwargs):
        raise RuntimeError("읽기 실패")

    async def broken_rollback():
        raise ConnectionError("연결 끊김")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    monkeypatch.setattr(observation_repository, "find_observation", broken)
    monkeypatch.setattr(session, "rollback", broken_rollback)
    try:
        with caplog.at_level(logging.WARNING):
            channel = await _run(cid, parent_id)
    finally:
        monkeypatch.undo()  # 테스트 세션 정리는 진짜 rollback 으로

    assert _names(channel) == ["step", "done"]
    assert channel.events[0][1]["label"] == "다음 행동을 준비하고 있어요"
    assert "ConnectionError" in caplog.text


async def test_이번_run_이_만들지_않은_ref_는_싣지_않는다(monkeypatch, bearer, cid, caplog):
    """읽는 대상은 이 run 의 store 가 만든 관찰뿐이다 — refs 에 다른 id 가 와도 싣지 않는다.

    refs 와 읽어 둔 키가 어긋난 것이라 경고가 남는다 (#471 리뷰).
    """
    _, parent_id = bearer
    other = uuid.uuid4()

    async def fake_handle_input(**kwargs):
        await kwargs["commit"]()
        kwargs["emit"](Saved((Ref("observation_activity", str(other)),)))
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)

    with caplog.at_level(logging.WARNING):
        channel = await _run(cid, parent_id)

    assert _names(channel) == ["done"]
    assert "찾지 못했다" in caplog.text
