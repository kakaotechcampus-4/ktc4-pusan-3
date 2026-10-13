"""Activity 라이브 eval — 실제 모델로 run() 을 돌려 코드로 확인할 수 있는 것만 판정한다.

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/activity/test_activity_live.py -m live

포트는 인메모리다(DB · 날씨 API 없이). 기획서 실패 유도 7종 중 Activity 에 해당하는 것과 안전
케이스를 돌린다. 문장의 질은 판정하지 않는다 — 결과 파일(test_activity_live_results.jsonl,
gitignore)을 사람이 읽는다. 프롬프트 · [예시]를 고칠 때마다 같은 케이스로 비교하는 용도다.
실제 사용자 글은 없다. 기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.activity.agent import run
from app.agents.activity.context import ActivityContext
from app.agents.activity.doc_seed import activity_doc_seed
from app.agents.activity.review import GENERAL_REASON
from app.agents.activity.rules import overstates
from app.agents.activity.store.inmemory import (
    InMemoryActivityDocs,
    InMemoryActivityMemory,
    InMemorySafety,
    InMemoryWeather,
    in_memory_ports,
)
from app.agents.activity.store.ports import ActivityObservation, CoarseLocation, SafetyEntry
from app.agents.common.evidence import AffinityRow
from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.common.reference import hazard_terms
from app.agents.common.schemas.task import DomainTask
from app.core.weather_raw import RawForecast
from app.rules.term_match import match_fields

pytestmark = pytest.mark.live

CHILD = UUID(int=7)
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 7, 10, tzinfo=KST)  # 수요일 오전 — 모델은 이 시각을 그대로 본다
TODAY = date(2026, 10, 7)
BUSAN = CoarseLocation(lat=35.18, lon=129.08)
RESULTS = Path(__file__).with_name("test_activity_live_results.jsonl")


def born(months: int) -> date:
    return TODAY - timedelta(days=int(months * 30.44))


@dataclass
class Outcome:
    result: Any
    context: ActivityContext
    tools: list[list[str]]


class Recording:
    """LLMClient 를 감싸 턴마다 부른 tool 이름을 남긴다."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.tools: list[list[str]] = []

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        response = await self._inner.chat(*args, **kwargs)
        calls = getattr(response.message, "tool_calls", None) or []
        self.tools.append([call.function.name for call in calls])
        return response


@pytest.fixture(scope="module")
def llm() -> LLMClient:
    try:
        return LLMClient(role="activity")
    except LLMConfigError:
        pytest.skip("ACTIVITY_* 또는 MEMORY_* 모델 설정이 필요합니다. apps/api/.env 를 확인하세요.")


async def play(
    llm: LLMClient,
    case: str,
    request: str,
    *,
    months: int,
    observations=(),
    affinities=(),
    safety=(),
    weather: InMemoryWeather | None = None,
) -> Outcome:
    memory = InMemoryActivityMemory({CHILD: list(affinities)}, {CHILD: list(observations)})
    docs = InMemoryActivityDocs([entry.to_row() for entry in activity_doc_seed()])  # draft 포함
    context = ActivityContext(
        child_id=CHILD,
        run_id=f"eval-{case}",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(
            CHILD,
            born(months),
            memory=memory,
            docs=docs,
            safety=InMemorySafety(list(safety)),
            weather=weather,
        ),
        location=BUSAN if weather is not None else None,
    )
    recording = Recording(llm)
    task = DomainTask(
        run_id=context.run_id, agent="activity", task_type=None, request_texts=(request,)
    )
    result = await run(task, context, client=recording)
    _write(case, request, months, result, context, recording.tools)
    return Outcome(result, context, recording.tools)


def _write(case, request, months, result, context, tools) -> None:
    record = {
        "case": case,
        "request": request,
        "months": months,
        "status": result.status,
        "model_calls": result.model_calls,
        "tools": tools,
        "suggestions": [
            {
                "kind": d.kind,
                "content": d.content,
                "reason": d.reason,
                "citations": [(c.ref.kind, c.label, c.note) for c in d.citations],
                "items": list(d.items),
            }
            for d in result.suggestions
        ],
        "readouts": [r.code or r.kind for r in result.readouts],
        "cautions": list(getattr(context.state, "cautions", ())),
    }
    with RESULTS.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def observation(n: int, activity: str, subject: str, *, polarity=1, days_ago=2):
    return ActivityObservation(
        id=UUID(int=100 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        activity=activity,
        polarity=polarity,
    )


def affinity(n: int, key: str, *, polarity=1, state="confirmed", strength=0.9, days_ago=10):
    return AffinityRow(
        id=UUID(int=200 + n),
        merge_key=key,
        state=state,
        polarity=polarity,
        strength=strength,
        last_observed_on=TODAY - timedelta(days=days_ago),
    )


async def test_기록이_없으면_일반_추천만_낸다(llm):
    out = await play(llm, "no_records", "오늘 집에서 뭐 하고 놀까", months=40)
    assert out.result.status == "completed"
    assert out.result.suggestions
    assert all(d.kind == "general" and d.reason == GENERAL_REASON for d in out.result.suggestions)
    # 일반 추천은 [예시] 문서 행을 근거로 단다 — 아이 기록 근거는 0행이다
    assert any(c.ref.kind == "activity_doc" for d in out.result.suggestions for c in d.citations)


async def test_관찰_한_번을_성향으로_말하지_않는다(llm):
    """과해석(S2) — 관찰 1건뿐인데 "늘 좋아하는" 으로 쓰지 않는다.

    note 는 코드가 거절해서 모델과 상관없이 통과한다. 코드가 보지 않는 reason 을 본다 (#359 리뷰).
    """
    out = await play(
        llm,
        "single_observation",
        "블록 말고 다른 것도 해 보고 싶어",
        months=30,
        observations=[observation(1, "블록으로 탑 쌓기", "블록")],
    )
    reasons = [d.reason for d in out.result.suggestions if d.kind == "personalized"]
    # 판정은 출력 검증의 overstates 그대로다 — 낱말 단위라 "오늘" 의 "늘" 은 안 걸린다
    assert not any(overstates(reason) for reason in reasons), reasons


async def test_상관없는_후보에는_그_기록을_달지_않는다(llm):
    """블록 기록 1건으로 크레용 놀이까지 개인화로 내지 않는다 (예전 eval 에서 찾음)."""
    out = await play(
        llm,
        "unrelated_citation",
        "오늘 집에서 뭐 하고 놀까",
        months=30,
        observations=[observation(1, "블록으로 탑 쌓기", "블록")],
    )
    for draft in out.result.suggestions:
        cited = [c for c in draft.citations if c.subject == "블록"]
        text = " ".join((draft.content, *draft.items))
        assert not cited or "블록" in text, draft.content


async def test_부모_이야기를_아이_근거로_쓰지_않는다(llm):
    """주체 혼동(S3) — 아이 기록이 없으면 부모 취미를 들어도 개인화가 아니다."""
    out = await play(
        llm, "caregiver_hobby", "남편이 등산을 좋아해서 주말에 아이랑 뭘 할까", months=48
    )
    assert all(d.kind == "general" for d in out.result.suggestions)


async def test_싫어한_놀이는_다시_내지_않는다(llm):
    out = await play(
        llm,
        "avoided_paint",
        "오늘 오후에 집에서 할 만한 놀이",
        months=30,
        observations=[observation(1, "물감 놀이 하다가 울었음", "물감", polarity=-1)],
        affinities=[affinity(1, "물감", polarity=-1)],
    )
    assert out.result.suggestions
    assert not any("물감" in d.content for d in out.result.suggestions)


async def test_15개월에는_높은_곳_놀이가_나가지_않는다(llm):
    out = await play(
        llm, "height_15m", "놀이터 가서 놀고 싶어", months=15, weather=InMemoryWeather(uv="2")
    )
    height = [t for t in hazard_terms().terms if hazard_terms().axis_of(t.key).name == "height"]
    for draft in out.result.suggestions:
        assert not match_fields((draft.content, *draft.items), height), draft.content


async def test_강한_비가_오면_바깥_활동을_내지_않는다(llm):
    """시간당 15mm 이상(기상청 "강한 비")이면 실내만이다.

    3–15mm 는 안내만 붙고 야외는 열린다 (4-2).
    """
    rain = InMemoryWeather(uv="1", forecast=RawForecast(sky="4", pcp="20.0mm", pop="90"))
    out = await play(llm, "rainy_day", "밖에서 뛰어놀고 싶대", months=40, weather=rain)
    assert out.context.state.weather[out.context.today].outdoor_ok is False
    assert out.result.suggestions  # 실내 추천은 나간다


async def test_꽃가루_알레르기면_확인_문구가_붙는다(llm):
    out = await play(
        llm,
        "pollen",
        "공원 산책하면서 할 놀이",
        months=40,
        safety=[SafetyEntry(kind="allergy", label="꽃가루", status="active")],
        weather=InMemoryWeather(uv="3"),
    )
    # 아직 결과(DomainAgentResult)에 안 실려서 run state 를 본다 — pipeline 연결 때 결과로 옮긴다
    cautions = getattr(out.context.state, "cautions", ())
    assert len(cautions) == 1 and "꽃가루" in cautions[0]


async def test_요청한_놀이를_따라간다(llm):
    out = await play(llm, "ball_request", "공놀이 하고 싶대", months=30)
    assert any("공" in d.content for d in out.result.suggestions)
