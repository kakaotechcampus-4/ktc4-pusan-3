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
    ObservationActivity,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.agent_reader import DbAffinityReader
from app.domains.safety.agent_reader import DbSafetyReader
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
    """Agent 마다 낼 결과를 정한다. 값: ("drafts", [SuggestionDraft], 되묻기?) · ("empty", 문구) ·
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
            question = what[2] if len(what) > 2 else None
            outcomes.append(
                entrypoint.SuggestOutcome(
                    task.agent, task.task_type, status, ids, readouts, question
                )
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


async def test_05_도_러너와_같은_안전_어댑터를_넘긴다(db_client, bearer, cid, agents):
    # 러너만 넘기면 홈 제안 · 다시 준비하기에서만 Food · Activity 가 닫힌다 (#460)
    plan, calls = agents
    headers, _ = bearer
    plan["food"] = ("empty", "준비할 수 있는 메뉴가 없어요.")
    plan["activity"] = ("empty", "준비할 수 있는 놀이가 없어요.")

    resp = await _post(db_client, cid, headers, agents=["food", "activity"])

    assert resp.status_code == 200
    assert isinstance(calls[0]["food_safety"], DbSafetyReader)
    assert calls[0]["activity_safety"] is calls[0]["food_safety"]
    assert calls[0]["growth_safety"] is calls[0]["food_safety"]


async def test_05_도_인증키가_있으면_도서_어댑터를_넘긴다(
    db_client, bearer, cid, agents, monkeypatch
):
    from app.integrations.data4library.client import Data4LibraryBooks

    monkeypatch.setattr(settings, "DATA4LIBRARY_AUTH_KEY", "test-auth-key")
    plan, calls = agents
    headers, _ = bearer
    plan["food"] = ("empty", "준비할 수 있는 메뉴가 없어요.")

    resp = await _post(db_client, cid, headers, agents=["food"])

    assert resp.status_code == 200
    assert isinstance(calls[0]["growth_books"], Data4LibraryBooks)


async def test_05_도_관심_어댑터를_넘긴다(db_client, bearer, cid, agents):
    # 러너만 넘기면 홈 제안 · 다시 준비하기에서만 관심이 근거에서 빠진다
    plan, calls = agents
    headers, _ = bearer
    plan["food"] = ("empty", "준비할 수 있는 메뉴가 없어요.")

    resp = await _post(db_client, cid, headers, agents=["food"])

    assert resp.status_code == 200
    assert isinstance(calls[0]["affinity_reader"], DbAffinityReader)


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
            # 알레르기를 적는 곳 — 04 의 safety_record 안내와 같은 값이다 (#625)
            "deeplink": "profile",
        }
    ]
    assert data["groups"] == []  # activity 는 실패 — 화면이 실패 카드를 그린다


async def test_구현_안_된_Agent_는_not_ready(db_client, bearer, cid, agents):
    headers, _ = bearer

    data = (await _post(db_client, cid, headers, agents=["health"])).json()

    assert [(g["code"], g["deeplink"]) for g in data["guards"]] == [("not_ready", None)]
    assert agents[1] == []  # Agent 를 부르지 않았다


async def test_Growth_처음_요청은_라벨을_못_골라_not_ready(db_client, bearer, cid, agents):
    """Growth 는 추천 라벨이 셋이라 run 없이 무엇을 돌릴지 모른다."""
    headers, _ = bearer

    data = (await _post(db_client, cid, headers, agents=["growth"])).json()

    assert [g["code"] for g in data["guards"]] == ["not_ready"]
    assert [g["blocked_agents"] for g in data["guards"]] == [["growth"]]
    assert agents[1] == []


async def test_run_이_있으면_Growth_추천도_그_run_에서_재생한다(
    db_client, session, bearer, cid, agents
):
    _, calls = agents
    headers, parent_id = bearer
    row = await _saved_in_run(session, cid, parent_id, "run-1", agent="growth")

    data = (await _post(db_client, cid, headers, agents=["growth"], run_id="run-1")).json()

    assert [s["id"] for s in data["suggestions"]] == [str(row.id)]
    assert data["guards"] == []
    assert calls == []  # 다시 만든 것이 없다


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


# ── #433 리뷰 정비 (#227 3단계) ──────────────────────────────────────────
async def test_맥락을_꺼낸_뒤_터져도_사용_중이_아니다(db_client, bearer, cid, agents, monkeypatch):
    """claim 뒤 예외로 끝나도 맥락을 푼다 — 안 풀면 15분 동안 다시 시도가 BUSY 다 (#433 리뷰 ②)."""
    headers, parent_id = bearer
    task = suggestion_retry.RetryTask("meal_recommendation", (), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="food", task=task
    )

    async def broken(session, *, child_id):
        raise RuntimeError("db")

    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", broken)
    try:
        await _post(db_client, cid, headers, agents=["food"], run_id="run-1")
    except RuntimeError:
        pass  # 테스트 클라이언트가 앱 예외를 그대로 올리는 경우

    kept = suggestion_retry.claim(run_id="run-1", parent_id=parent_id, child_id=cid, agent="food")
    assert kept == task


async def test_다시_만들었더니_닫혔으면_다음_요청도_같은_guard(db_client, bearer, cid, agents):
    """닫힘을 기록해 두지 않으면 다음 요청이 "이어서 할 수 없어요" 가 된다 (#433 리뷰 ⑧)."""
    plan, calls = agents
    headers, parent_id = bearer
    task = suggestion_retry.RetryTask(None, ("놀이 추천해줘",), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="activity", task=task
    )
    plan["activity"] = ("blocked", "알레르기 정보를 확인할 수 없어서 추천을 드릴 수 없어요.")

    first = (await _post(db_client, cid, headers, agents=["activity"], run_id="run-1")).json()
    second = (await _post(db_client, cid, headers, agents=["activity"], run_id="run-1")).json()

    assert [(g["code"], g["deeplink"]) for g in first["guards"]] == [("safety_unknown", "profile")]
    assert second["guards"] == first["guards"]
    assert second["groups"] == []
    assert len(calls) == 1  # 두 번째는 다시 돌리지 않는다


async def test_Agent_를_돌리기_전에_요청_세션의_트랜잭션을_닫는다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    """Agent 가 도는 동안(최대 20초) 요청 커넥션을 쥐지 않는다 (#433 리뷰 ⑨)."""
    headers, _ = bearer
    seen: list[bool] = []

    async def fake_suggest(**kwargs):
        seen.append(session.in_transaction())
        return entrypoint.SuggestRun((), ())

    monkeypatch.setattr(entrypoint, "suggest", fake_suggest)

    await _post(db_client, cid, headers, agents=["food"])

    assert seen == [False]


async def test_질문_id_는_질문을_낸_Agent_다(db_client, bearer, cid, agents, monkeypatch):
    """첫 Agent 로 적으면 답(#246)이 다른 Agent 로 간다 (#433 리뷰 ⑤)."""
    plan, _ = agents
    headers, _ = bearer
    monkeypatch.setattr(
        entrypoint,
        "_doc_index",
        lambda: {("activity_doc", DOC_ID): DOC_CARD, ("food_doc", DOC_ID): DOC_CARD},
    )
    plan["food"] = ("empty", "조건에 맞는 추천이 없어요.")
    plan["activity"] = ("drafts", [_general()], "요즘 실내와 야외 중 어디를 더 찾나요?")

    data = (await _post(db_client, cid, headers, agents=["food", "activity"])).json()

    assert data["scarcity"]["question"]["id"] == "activity:q"


async def test_한_run_에_행이_셋을_넘어도_500_이_아니다(db_client, session, bearer, cid, agents):
    """다시 만들기가 겹쳐 한 Agent 행이 3개를 넘어도 개수 안내가 터지지 않는다 (#433 리뷰 ⑦)."""
    headers, parent_id = bearer
    for _ in range(4):
        await _saved_in_run(session, cid, parent_id, "run-1")

    resp = await _post(db_client, cid, headers, agents=["food"], run_id="run-1")

    assert resp.status_code == 200
    assert resp.json()["groups"][0]["notice"] is None


async def test_allergens_가_없으면_사전검사_행을_읽지_않는다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    """사전검사 행은 처음 필요할 때 한 번 읽는다 (#433 리뷰 ⑬)."""
    plan, _ = agents
    headers, parent_id = bearer
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    reads: list[int] = []

    async def labels(session, *, child_id):
        reads.append(1)
        return []

    monkeypatch.setattr("app.api.v1.routers.suggestions.list_safety_labels", labels)
    plan["food"] = ("drafts", [await _personal(session, cid, parent_id)])

    await _post(db_client, cid, headers, agents=["food"])

    assert reads == []


# ── 05 응답 칸 (#227 3단계) ──────────────────────────────────────────────
CAUTION = "등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요."


async def test_식품_아닌_알레르기_확인_문구가_놀이_묶음에_선다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    """04 에서 보인 확인 문구가 05 에서 사라지지 않는다 — 재생 때도 코드로 다시 만든다 (#433 ③)."""
    plan, _ = agents
    headers, _ = bearer
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    session.add(
        HealthSafety(
            child_id=cid, kind=SafetyKind.ALLERGY, label="꽃가루", status=SafetyStatus.ACTIVE
        )
    )
    plan["activity"] = ("drafts", [_general()])
    plan["food"] = ("empty", "조건에 맞는 추천이 없어요.")

    data = (await _post(db_client, cid, headers, agents=["food", "activity"])).json()

    groups = {g["agent"]: g for g in data["groups"]}
    assert groups["activity"]["cautions"] == [CAUTION]
    assert groups["food"]["cautions"] == []


async def test_동의가_없으면_확인_문구도_읽지_않는다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    plan, _ = agents
    headers, _ = bearer
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(False))
    session.add(
        HealthSafety(
            child_id=cid, kind=SafetyKind.ALLERGY, label="꽃가루", status=SafetyStatus.ACTIVE
        )
    )
    plan["activity"] = ("drafts", [_general()])

    data = (await _post(db_client, cid, headers, agents=["activity"])).json()

    assert data["groups"][0]["cautions"] == []


async def _activity_record(session, cid, parent_id):
    today = datetime.now(UTC).date()
    session.add(
        ObservationActivity(
            child_id=cid,
            source_writer=parent_id,
            raw_text="블록을 쌓았다",
            subject="블록",
            activity="블록 쌓기",
            confidence_source=ConfidenceSource.PARENT_DIRECT,
            observed_range=Range(today, today + timedelta(days=1)),
        )
    )
    await session.flush()


async def test_일반_추천은_그_도메인의_기록_수를_싣는다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    """카드의 "기록 N건" 은 그 Agent 도메인 수다 — 합을 쓰면 다른 주제까지 센다 (#433 ⑥)."""
    plan, _ = agents
    headers, parent_id = bearer
    monkeypatch.setattr(
        entrypoint,
        "_doc_index",
        lambda: {("activity_doc", DOC_ID): DOC_CARD, ("food_doc", DOC_ID): DOC_CARD},
    )
    await _personal(session, cid, parent_id)  # 식사 관찰 1건
    await _personal(session, cid, parent_id)  # 식사 관찰 2건
    await _activity_record(session, cid, parent_id)  # 놀이 관찰 1건
    food_doc = EvidenceCitation(ref=Ref(kind="food_doc", id=DOC_ID), note="또래 식사 자료")
    plan["food"] = (
        "drafts",
        [
            SuggestionDraft(
                agent="food",
                kind="general",
                content="두부조림",
                reason="또래 기준",
                citations=(food_doc,),
            )
        ],
    )
    plan["activity"] = ("drafts", [_general()])

    data = (await _post(db_client, cid, headers, agents=["food", "activity"])).json()

    counts = {g["agent"]: g["record_count"] for g in data["general"]}
    assert counts == {"food": 2, "activity": 1}
    assert data["scarcity"]["count"] == 3  # 질문 카드의 "이 주제" 는 합 그대로


async def test_한도에_걸려도_다른_Agent_재생은_보인다(
    db_client, session, bearer, cid, agents, monkeypatch
):
    """요청 전체를 429 로 막지 않고 돌릴 Agent 묶음에만 안내를 단다 (#433 리뷰 ④)."""
    headers, parent_id = bearer
    monkeypatch.setattr(settings, "INPUT_DAILY_LIMIT", 0)
    row = await _saved_in_run(session, cid, parent_id, "run-1")
    task = suggestion_retry.RetryTask(None, ("놀이 추천해줘",), datetime.now(UTC))
    suggestion_retry.put_failed(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="activity", task=task
    )

    resp = await _post(db_client, cid, headers, agents=["food", "activity"], run_id="run-1")

    assert resp.status_code == 200
    data = resp.json()
    assert [s["id"] for s in data["suggestions"]] == [str(row.id)]
    groups = {g["agent"]: g for g in data["groups"]}
    assert groups["activity"]["notice"]["code"] == "daily_limit"
    assert agents[1] == []  # 돌리지 않았다
    kept = suggestion_retry.claim(
        run_id="run-1", parent_id=parent_id, child_id=cid, agent="activity"
    )
    assert kept == task  # 내일 다시 시도할 수 있게 맥락은 남는다(15분 안이면)


async def test_05_도_Food_카탈로그_어댑터를_넘긴다(db_client, bearer, cid, agents):
    """#442 — 입력 run(러너)과 05 가 같은 어댑터를 넘긴다.

    한쪽만 넘기면 두 경로의 후보 풀이 갈라진다.
    """
    plan, calls = agents
    headers, _ = bearer
    plan["food"] = ("empty", "조건에 맞는 추천이 없어요.")

    resp = await _post(db_client, cid, headers, agents=["food"])

    assert resp.status_code == 200
    assert type(calls[0]["food_catalog"]).__name__ == "DbMenuCatalog"
    assert type(calls[0]["food_recipes"]).__name__ == "DbRecipeCatalog"
    assert type(calls[0]["food_suggestions"]).__name__ == "DbSuggestionHistory"


# ── Food 일반 추천의 메뉴 근거 (#442) ────────────────────────────────────
async def test_Food_일반_추천은_고른_메뉴를_근거로_보여_준다(
    db_client, session, bearer, cid, agents
):
    """문서 행이 없는 월령에도 Food 일반 추천이 나간다 — 근거는 고른 메뉴(레시피 카탈로그 행)다."""
    from app.domains.food import repository as food_repository
    from app.rules.menu_evidence import menu_evidence_id

    plan, _ = agents
    headers, _ = bearer
    await food_repository.upsert_recipes(
        session,
        rows=[
            {
                "source": "mafra_recipe",
                "source_id": "7",
                "menu_key": "두부조림",
                "display_name": "두부조림",
                "ingredients": ["두부"],
                "resolved": True,
                "synced_at": datetime.now(UTC),
            }
        ],
    )
    menu = menu_evidence_id("mafra_recipe", "7")
    plan["food"] = (
        "drafts",
        [
            SuggestionDraft(
                agent="food",
                kind="general",
                content="두부조림",
                reason="또래 아이들이 많이 먹는 메뉴예요.",
                citations=(
                    EvidenceCitation(
                        ref=Ref(kind="recipe_catalog", id=menu), note="재료가 검수된 메뉴예요."
                    ),
                ),
            )
        ],
    )

    resp = await _post(db_client, cid, headers, agents=["food"])

    assert resp.status_code == 200
    [general] = resp.json()["general"]
    [evidence] = general["evidence"]
    assert evidence["ref"] == {"kind": "recipe_catalog", "id": str(menu)}
    assert evidence["title"] == "두부조림"
    assert evidence["source_org"] and evidence["note"] == "재료가 검수된 메뉴예요."


# ── Growth 도서 추천의 책 근거 ───────────────────────────────────────────
async def test_Growth_도서_일반_추천은_그_책을_정보나루_출처로_보여_준다(
    db_client, session, bearer, cid, agents
):
    """도서는 책 정보 표가 없어서 카드 제목은 그 추천의 "제목(저자)" 다 — 근거는 그 책 자체다."""
    from app.domains.suggestion.repository import save_agent_suggestions
    from app.rules.book_evidence import book_evidence_id

    headers, _ = bearer
    book = book_evidence_id("9788900000001")
    draft = SuggestionDraft(
        agent="growth",
        kind="general",
        content="공룡 기차(가나다)",
        reason="쌓인 배움 기록이 아직 적어서, 이 나이에 맞는 형태의 책 중에서 골랐어요.",
        citations=(
            EvidenceCitation(
                ref=Ref(kind="book_catalog", id=book),
                note="도서관 정보나루 검색 결과에 있는 책이에요.",
            ),
        ),
        items=("공룡 기차(가나다)",),
    )
    await save_agent_suggestions(
        session, child_id=cid, run_id="run-1", payloads=[draft.to_payload()], now=datetime.now(UTC)
    )

    resp = await _post(db_client, cid, headers, agents=["growth"], run_id="run-1")

    assert resp.status_code == 200
    [general] = resp.json()["general"]
    [evidence] = general["evidence"]
    assert evidence["ref"] == {"kind": "book_catalog", "id": str(book)}
    assert evidence["title"] == "공룡 기차(가나다)"
    assert (evidence["source_org"], evidence["source_title"]) == (
        "국립중앙도서관",
        "도서관 정보나루",
    )
    assert evidence["source_url"] == "https://data4library.kr"
