"""05 추천 엔드포인트 (#249) — 응답 모양 · 판단 순서 · 사전검사 · 일반 추천 · 한도."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.agents import entrypoint
from app.agents.common.readout import Readout
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionDraft
from app.agents.suggestion_save import SuggestionBatch
from app.api import agent_ports, quota
from app.api.runs import suggestion_retry
from app.core.config import settings
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.safety.models import HealthSafety, SafetyKind, SafetyStatus
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
    SuggestionStatus,
)

DOC_ID = uuid.UUID("00000000-0000-5000-8000-0000000000aa")
DOC_CARD = entrypoint.DocCard("블록 쌓기", "교육부", "표준보육과정", None)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    suggestion_retry.clear()
    quota.clear()
    monkeypatch.setattr(entrypoint, "_doc_index", lambda: {("activity_doc", DOC_ID): DOC_CARD})
    yield
    suggestion_retry.clear()
    quota.clear()


@pytest.fixture(autouse=True)
def _same_session(monkeypatch, session):
    """저장 · 기억 포트가 여는 짧은 세션을 테스트 세션으로 — 테스트가 만든 관찰이 근거 검증에
    보이고, 끝나면 같이 롤백된다. conftest 의 session 은 핸들러가 commit 해도 바깥 트랜잭션이
    남는다."""

    @asynccontextmanager
    async def same():
        yield session

    monkeypatch.setattr(agent_ports, "background_session", same)


@pytest.fixture
def agents(monkeypatch):
    """Agent 마다 낼 결과를 정한다. 값: ("drafts", [SuggestionDraft]) · ("empty", 문구) ·
    ("blocked", 문구) · ("fail",)."""
    plan: dict[str, tuple] = {}
    calls: list[dict] = []

    async def fake_suggest(**kwargs):
        calls.append(kwargs)
        outcomes, failed = [], []
        for task in kwargs["tasks"]:
            what = plan[task.agent]
            if what[0] == "fail":
                failed.append(task.agent)
                continue
            ids: tuple[str, ...] = ()
            readouts: tuple[Readout, ...] = ()
            status = "completed"
            if what[0] == "drafts":
                batch = SuggestionBatch(
                    kwargs["save_run_id"], task.agent, task.task_type, tuple(what[1])
                )
                ids = await kwargs["save_suggestions"](batch)
            elif what[0] == "empty":
                readouts = (Readout(kind="notice", body=what[1], authored_by="code", code="empty"),)
            elif what[0] == "blocked":
                status = "blocked"
                readouts = (Readout(kind="blocked", body=what[1], authored_by="code"),)
            outcomes.append(
                entrypoint.SuggestOutcome(task.agent, task.task_type, status, ids, readouts, None)
            )
        return entrypoint.SuggestRun(tuple(outcomes), tuple(failed))

    monkeypatch.setattr(entrypoint, "suggest", fake_suggest)
    return plan, calls


def _general(agent="activity", content="블록 쌓기") -> SuggestionDraft:
    doc = EvidenceCitation(ref=Ref(kind="activity_doc", id=DOC_ID), note="또래 놀이 자료")
    return SuggestionDraft(
        agent=agent,
        kind="general",
        content=content,
        reason="또래 아이들이 많이 하는 놀이예요.",
        citations=(doc,),
    )


async def _personal(session, cid, parent_id, *, agent="food", content="계란말이", allergens=()):
    today = datetime.now(UTC).date()
    obs = ObservationFood(
        child_id=cid,
        source_writer=parent_id,
        raw_text="계란말이를 다 먹었다",
        subject="계란말이",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(today, today + timedelta(days=1)),
    )
    session.add(obs)
    await session.flush()
    return SuggestionDraft(
        agent=agent,
        kind="personalized",
        content=content,
        reason="잘 먹어서요",
        citations=(
            EvidenceCitation(
                ref=Ref(kind="observation_food", id=obs.id), note="잘 먹음", observed_on=today
            ),
        ),
        allergens=tuple(allergens),
    )


async def _post(client, cid, headers, **body):
    return await client.post(f"/api/v1/children/{cid}/suggestions", json=body, headers=headers)


def _granted(value: bool):
    async def read(session, *, child_id):
        return value

    return read


# ── 응답 모양 ────────────────────────────────────────────────────────────
async def test_홈_요청은_Agent_마다_묶음과_안내를_싣는다(db_client, session, bearer, cid, agents):
    plan, calls = agents
    headers, parent_id = bearer
    plan["food"] = ("drafts", [await _personal(session, cid, parent_id)])
    plan["activity"] = ("empty", "조건에 맞는 추천이 없어요.")

    resp = await _post(db_client, cid, headers, agents=["food", "activity"])

    assert resp.status_code == 200
    data = resp.json()
    [food] = data["suggestions"]
    assert food["evidence"][0]["label"] == "계란말이"
    assert food["evidence"][0]["is_stale"] is False
    groups = {g["agent"]: g for g in data["groups"]}
    assert groups["food"]["notice"] == {
        "code": "fewer",
        "message": "조건에 맞는 추천을 1개 준비했어요.",
    }
    assert groups["food"]["merges_into_one"] is True
    assert groups["activity"]["notice"]["code"] == "empty"
    assert data["guards"] == [] and data["general"] == [] and data["scarcity"] is None
    assert calls[0]["save_run_id"] is None


async def test_홈_버튼_키는_표의_문장만_Agent_에_보낸다(db_client, bearer, cid, agents):
    plan, calls = agents
    headers, _ = bearer
    plan["food"] = ("empty", "없어요")

    resp = await _post(db_client, cid, headers, agents=["food"], prompt_key="dinner")

    assert resp.status_code == 200
    assert calls[0]["tasks"][0].request_texts == ("저녁 추천해줘",)  # 화면이 보낸 문장은 없다


async def test_닫힌_Agent_는_guard_로_실패는_묶음에서_빠진다(db_client, bearer, cid, agents):
    plan, _ = agents
    headers, _ = bearer
    plan["food"] = ("blocked", "알레르기 정보를 확인할 수 없어서 추천을 드릴 수 없어요.")
    plan["activity"] = ("fail",)

    data = (await _post(db_client, cid, headers, agents=["food", "activity"])).json()

    assert data["guards"] == [
        {
            "code": "safety_unknown",
            "blocked_agents": ["food"],
            "message": "알레르기 정보를 확인할 수 없어서 추천을 드릴 수 없어요.",
            "deeplink": None,
        }
    ]
    assert data["groups"] == []  # activity 는 실패 — 화면이 실패 카드를 그린다


async def test_구현_안_된_Agent_는_not_ready(db_client, bearer, cid, agents):
    headers, _ = bearer

    data = (await _post(db_client, cid, headers, agents=["growth"])).json()

    assert [g["code"] for g in data["guards"]] == ["not_ready"]
    assert agents[1] == []  # Agent 를 부르지 않았다


# ── 판단 순서 ────────────────────────────────────────────────────────────
async def _saved_in_run(
    session, cid, parent_id, run_id, *, agent="food", status=SuggestionStatus.DRAFT
):
    draft = await _personal(session, cid, parent_id, agent=agent)
    row = Suggestion(
        child_id=cid,
        agent=SuggestionAgent(agent),
        kind=SuggestionKind.PERSONALIZED,
        content=draft.content,
        reason=draft.reason,
        status=status,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        run_id=run_id,
    )
    session.add(row)
    await session.flush()
    session.add(
        SuggestionEvidence(
            suggestion_id=row.id,
            source_kind="observation_food",
            source_id=draft.citations[0].ref.id,
            note="잘 먹음",
        )
    )
    await session.flush()
    return row


async def test_run_id_섞인_판단(db_client, session, bearer, cid, agents):
    """Food 는 그 run 의 추천 그대로, Activity 는 기록이 없어 이어서 할 수 없다 — 한 응답에."""
    _, calls = agents
    headers, parent_id = bearer
    row = await _saved_in_run(session, cid, parent_id, "run-1")

    data = (
        await _post(db_client, cid, headers, agents=["food", "activity"], run_id="run-1")
    ).json()

    assert [s["id"] for s in data["suggestions"]] == [str(row.id)]
    groups = {g["agent"]: g for g in data["groups"]}
    assert groups["activity"]["notice"]["code"] == "cannot_resume"
    assert calls == []  # 다시 만든 것이 없다


async def test_채택한_추천도_돌려준다(db_client, session, bearer, cid, agents):
    headers, parent_id = bearer
    row = await _saved_in_run(session, cid, parent_id, "run-1", status=SuggestionStatus.APPROVED)

    data = (await _post(db_client, cid, headers, agents=["food"], run_id="run-1")).json()

    assert [(s["id"], s["status"]) for s in data["suggestions"]] == [(str(row.id), "approved")]


async def test_0개_기록은_같은_안내(db_client, bearer, cid, agents):
    headers, parent_id = bearer
    suggestion_retry.put_empty(
        run_id="run-1",
        parent_id=parent_id,
        child_id=cid,
        agent="activity",
        message="조건에 맞는 놀이가 없어요.",
    )

    data = (await _post(db_client, cid, headers, agents=["activity"], run_id="run-1")).json()

    assert data["groups"][0]["notice"] == {"code": "empty", "message": "조건에 맞는 놀이가 없어요."}
    assert agents[1] == []


async def test_실패_맥락은_처음_시각과_문장으로_다시_만든다(
    db_client, session, bearer, cid, agents
):
    plan, calls = agents
    headers, parent_id = bearer
    sent_at = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    suggestion_retry.put_failed(
        run_id="run-1",
        parent_id=parent_id,
        child_id=cid,
        agent="food",
        task=suggestion_retry.RetryTask("meal_recommendation", ("내일 아침 추천해줘",), sent_at),
    )
    plan["food"] = ("drafts", [await _personal(session, cid, parent_id)])

    data = (await _post(db_client, cid, headers, agents=["food"], run_id="run-1")).json()

    assert len(data["suggestions"]) == 1
    [call] = calls
    assert call["sent_at"] == sent_at
    assert call["tasks"][0].request_texts == ("내일 아침 추천해줘",)
    assert call["save_run_id"] == "run-1"  # 새로고침하면 ① 로 찾는다
    assert call["request_id"] != "run-1"  # seed 는 새 요청 id
    # 성공했으니 맥락은 지웠다
    gone = suggestion_retry.claim(run_id="run-1", parent_id=parent_id, child_id=cid, agent="food")
    assert gone is None


async def test_다시_만들기가_또_실패하면_맥락은_남는다(db_client, bearer, cid, agents):
    plan, _ = agents
    headers, parent_id = bearer
    task = suggestion_retry.RetryTask("meal_recommendation", (), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="food", task=task
    )
    plan["food"] = ("fail",)

    data = (await _post(db_client, cid, headers, agents=["food"], run_id="run-1")).json()

    assert data["groups"] == []
    kept = suggestion_retry.claim(run_id="run-1", parent_id=parent_id, child_id=cid, agent="food")
    assert kept == task


async def test_사용_중인_맥락은_실패로_둔다(db_client, bearer, cid, agents):
    headers, parent_id = bearer
    task = suggestion_retry.RetryTask("meal_recommendation", (), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="food", task=task
    )
    suggestion_retry.claim(run_id="run-1", parent_id=parent_id, child_id=cid, agent="food")

    data = (await _post(db_client, cid, headers, agents=["food"], run_id="run-1")).json()

    assert data["groups"] == [] and agents[1] == []


async def test_다른_보호자의_맥락은_보이지_않는다(db_client, bearer, cid, agents):
    headers, _ = bearer
    task = suggestion_retry.RetryTask("meal_recommendation", (), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=uuid.uuid4(), child_id=cid, agent="food", task=task
    )

    data = (await _post(db_client, cid, headers, agents=["food"], run_id="run-1")).json()

    assert data["groups"][0]["notice"]["code"] == "cannot_resume"


# ── 사전검사 · 근거 · 일반 추천 · 한도 ───────────────────────────────────
async def test_사전검사(db_client, session, bearer, cid, agents, monkeypatch):
    """active 에 걸린 추천은 빼고, 행이 없는 항목만 묻는다 (types.ts Precheck)."""
    plan, _ = agents
    headers, parent_id = bearer
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    session.add(
        HealthSafety(
            child_id=cid, kind=SafetyKind.ALLERGY, label="우유", status=SafetyStatus.ACTIVE
        )
    )
    milk = await _personal(session, cid, parent_id, content="우유죽", allergens=["우유"])
    nuts = await _personal(session, cid, parent_id, content="땅콩버터빵", allergens=["땅콩"])
    plan["food"] = ("drafts", [milk, nuts])

    data = (await _post(db_client, cid, headers, agents=["food"])).json()

    [shown] = data["suggestions"]
    assert shown["content"] == "땅콩버터빵"
    assert data["prechecks"] == [
        {
            "code": "unknown_ingredient",
            "item": "땅콩",
            "note": "알레르기 기록에 없음",
            "suggestion_id": shown["id"],
        }
    ]


async def test_동의가_없으면_알레르기_든_추천을_뺀다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    plan, _ = agents
    headers, parent_id = bearer
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(False))
    plan["food"] = ("drafts", [await _personal(session, cid, parent_id, allergens=["땅콩"])])

    data = (await _post(db_client, cid, headers, agents=["food"])).json()

    assert data["suggestions"] == [] and data["prechecks"] == []
    assert data["groups"][0]["notice"]["code"] == "empty"


async def test_보이는_근거가_없는_개인화는_뺀다(db_client, session, bearer, cid, agents):
    headers, parent_id = bearer
    row = await _saved_in_run(session, cid, parent_id, "run-1")
    source_id = await session.scalar(
        select(SuggestionEvidence.source_id).where(SuggestionEvidence.suggestion_id == row.id)
    )
    obs = await session.get(ObservationFood, source_id)
    obs.status = ObservationStatus.DELETED
    await session.flush()

    data = (await _post(db_client, cid, headers, agents=["food"], run_id="run-1")).json()

    assert data["suggestions"] == []


async def test_일반_추천은_문서_근거와_기록_건수를_싣는다(db_client, bearer, cid, agents):
    plan, _ = agents
    headers, _ = bearer
    plan["activity"] = ("drafts", [_general()])

    data = (await _post(db_client, cid, headers, agents=["activity"])).json()

    assert data["suggestions"] == []
    [general] = data["general"]
    assert general["basis"] == "또래 아이들이 많이 하는 놀이예요."
    assert general["evidence"] == [
        {
            "ref": {"kind": "activity_doc", "id": str(DOC_ID)},
            "title": "블록 쌓기",
            "source_org": "교육부",
            "source_title": "표준보육과정",
            "source_url": None,
            "note": "또래 놀이 자료",
        }
    ]
    assert data["scarcity"] == {"count": 0, "question": None}


async def test_문서_행을_읽을_수_없으면_일반_추천을_내지_않는다(
    db_client, bearer, cid, agents, monkeypatch
):
    """일반 추천의 근거는 문서 행이다 (루트 §2). 행을 못 찾으면 근거 없이 서지 않는다."""
    plan, _ = agents
    headers, _ = bearer
    monkeypatch.setattr(entrypoint, "_doc_index", lambda: {})
    plan["activity"] = ("drafts", [_general()])

    data = (await _post(db_client, cid, headers, agents=["activity"])).json()

    assert data["general"] == []
    assert data["scarcity"] is None


async def test_다시_도는_요청만_하루_한도를_쓴다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    plan, _ = agents
    headers, parent_id = bearer
    monkeypatch.setattr(settings, "INPUT_DAILY_LIMIT", 1)
    await _saved_in_run(session, cid, parent_id, "run-1")
    # 재생은 몇 번이든 한도를 쓰지 않는다
    for _ in range(3):
        resp = await _post(db_client, cid, headers, agents=["food"], run_id="run-1")
        assert resp.status_code == 200
    plan["activity"] = ("empty", "없어요")

    assert (await _post(db_client, cid, headers, agents=["activity"])).status_code == 200
    resp = await _post(db_client, cid, headers, agents=["activity"])

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "daily_input_limit"


async def test_한도에_걸리면_꺼낸_맥락은_사용_중이_아니다(
    db_client, bearer, cid, agents, monkeypatch
):
    """429 로 끝나도 맥락이 "사용 중" 으로 남으면 15분 동안 다시 시도할 수 없다."""
    headers, parent_id = bearer
    monkeypatch.setattr(settings, "INPUT_DAILY_LIMIT", 0)
    task = suggestion_retry.RetryTask("meal_recommendation", (), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="food", task=task
    )

    resp = await _post(db_client, cid, headers, agents=["food"], run_id="run-1")

    assert resp.status_code == 429
    kept = suggestion_retry.claim(run_id="run-1", parent_id=parent_id, child_id=cid, agent="food")
    assert kept == task


async def test_표에_없는_prompt_key_는_400(db_client, bearer, cid, agents):
    headers, _ = bearer

    resp = await _post(db_client, cid, headers, agents=["food"], prompt_key="없는키")

    assert resp.status_code == 400
    assert agents[1] == []
