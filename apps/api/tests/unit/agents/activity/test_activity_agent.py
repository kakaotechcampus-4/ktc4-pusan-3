"""Activity run() — 사전 조회 · tool 루프 · 거절 뒤 재제출 · 안전 필터 뒤 재호출 1회 (설계 3-2).

모델은 정해 둔 응답을 순서대로 내는 가짜다. 포트는 InMemory 다.
- 거절은 같은 진입 안에서 고쳐 다시 낸다 — model_calls 가 늘지 않는다.
- 안전 필터로 빠지면 사유 없이 제외 목록만 주고 1회 더 부른다 — model_calls=2.
- 재호출 결과는 처음 통과한 것을 먼저 두고 빈자리만 채운다.
"""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from app.agents.activity import agent as agent_module
from app.agents.activity.agent import MAX_STEPS, run
from app.agents.activity.context import ActivityContext
from app.agents.activity.schemas.common import ActivitySetting, CaregiverRole, Intensity
from app.agents.activity.store.inmemory import (
    InMemoryActivityDocs,
    InMemorySafety,
    InMemoryWeather,
    in_memory_ports,
)
from app.agents.activity.store.ports import ActivityDocRow, CoarseLocation, SafetyEntry
from app.agents.activity.weather import WEATHER_UNCHECKED
from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.core.weather_raw import RawForecast

CHILD = UUID(int=1)
NOW = datetime(2026, 10, 7, 1, tzinfo=UTC)  # KST 10:00 수요일
BORN_40_MONTHS = date(2023, 6, 1)
BORN_20_MONTHS = date(2025, 2, 1)
BUSAN = CoarseLocation(lat=35.18, lon=129.08)


class FakeLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []
        self.tool_choices: list[Any] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.seen.append([dict(message) for message in messages])
        self.tool_choices.append(kwargs.get("tool_choice"))
        if self._queue:
            return self._queue.pop(0)
        return _say("더 할 말이 없어요.")


_ids = iter(range(1, 1000))


def _tools(*calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    tool_calls = [
        SimpleNamespace(
            id=f"call-{next(_ids)}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
        )
        for name, args in calls
    ]
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=tool_calls), usage={}, latency_ms=1
    )


def _say(text: str) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=text, tool_calls=None), usage={}, latency_ms=1
    )


def candidate(
    content: str, *, setting: str = "indoor", materials=(), day: str = "today"
) -> dict[str, Any]:
    return {
        "content": content,
        "setting": setting,
        "day": day,
        "materials": list(materials),
        "physical_intensity": "low",
        "involves_food": False,
        "caregiver_role": "together",
        "reason": "집에서 바로 할 수 있어요 오늘 오전이 비어 있어요",
    }


def propose(*contents: str, **kwargs) -> LLMResponse:
    return _tools(
        ("propose_activity_candidates", {"candidates": [candidate(c, **kwargs) for c in contents]})
    )


def memory() -> LLMResponse:
    return _tools(("search_activity_memory", {}))


def context(born=BORN_40_MONTHS, *, location=None, weather=None, docs=None) -> ActivityContext:
    return ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(CHILD, born, weather=weather, docs=docs),
        location=location,
    )


def task(text="오늘 집에서 뭐 하고 놀까") -> DomainTask:
    return DomainTask(run_id="run-1", agent="activity", task_type=None, request_texts=(text,))


def contents(result) -> list[str]:
    return [draft.content for draft in result.suggestions]


class TestNormal:
    async def test_조회하고_세_개를_내면_한_번에_끝난다(self):
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task(), context(), client=llm)
        assert result.status == "completed"
        assert result.model_calls == 1
        assert contents(result) == ["종이컵 탑 쌓기", "그림자놀이", "색종이 접기"]
        assert all(draft.kind == "general" for draft in result.suggestions)  # 기록 0건
        assert result.readouts == ()  # 3개라 개수 안내 없음 · 위치 없어 날씨 안내 없음

    async def test_거절되면_같은_진입_안에서_고쳐_낸다(self):
        """바깥 활동이 어려운 날(위치 없음 → 실내만) 야외 후보는 거절된다. model_calls 는 그대로."""
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor"),
            propose("거실 보물찾기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task(), context(), client=llm)
        assert result.model_calls == 1
        assert contents(result)[0] == "거실 보물찾기"
        rejected = json.loads(llm.seen[1][-1]["content"])
        assert rejected["success"] is False

    async def test_tool_을_반드시_부르게_한다(self):
        """공통 루프에 required 를 넘긴다 — 라이브에서 auto 는 0/3, required 는 3/3 (#320 리뷰)."""
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), context(), client=llm)
        assert llm.tool_choices == ["required"]

    async def test_그래도_말로_끝내면_실패다(self):
        """모델이 쓴 말은 화면에 내지 않는다 — 출력 채널이 아니다."""
        llm = FakeLLM(_say("이런 놀이는 어때요?"))
        result = await run(task(), context(), client=llm)
        assert (result.status, result.suggestions, result.model_calls) == ("failed", (), 1)
        assert len(llm.seen) == 1

    async def test_왕복_상한을_넘기면_실패다(self):
        llm = FakeLLM(*[memory() for _ in range(MAX_STEPS)])
        result = await run(task(), context(), client=llm)
        assert result.status == "failed"
        assert len(llm.seen) == MAX_STEPS


class TestRecall:
    async def test_안전_필터로_빠지면_한_번_더_불러_빈자리만_채운다(self):
        """20개월 — 구슬(작은 부품)은 36개월 미만 차단. 처음 통과한 둘을 먼저 둔다."""
        llm = FakeLLM(
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("까꿍 놀이", "그림자놀이", "컵 쌓기"),
        )
        result = await run(task(), context(BORN_20_MONTHS), client=llm)
        assert result.model_calls == 2
        assert contents(result) == ["그림자놀이", "색종이 접기", "까꿍 놀이"]
        assert result.readouts == ()

    async def test_재호출에는_빠진_것과_고른_것을_제외_목록으로만_준다(self):
        """빠진 이유(작은 부품)는 알려 주지 않는다 — 단어만 바꿔 다시 내는 법을 배운다."""
        llm = FakeLLM(
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("까꿍 놀이", "공 굴리기", "컵 쌓기"),
        )
        await run(task(), context(BORN_20_MONTHS), client=llm)
        recall = llm.seen[1][-1]
        assert recall["role"] == "user"
        assert "구슬 꿰기" in recall["content"] and "그림자놀이" in recall["content"]
        assert "작은 부품" not in recall["content"] and "삼킬" not in recall["content"]

    async def test_재호출은_같은_대화에_이어_쓴다(self):
        """새 대화면 조회를 처음부터 다시 해 왕복이 늘고 20초에 걸리기 쉽다 (Tool_공통 §5-2)."""
        llm = FakeLLM(
            memory(),
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("까꿍 놀이", "공 굴리기", "컵 쌓기"),
        )
        await run(task(), context(BORN_20_MONTHS), client=llm)
        first, recall = llm.seen[1], llm.seen[2]
        assert recall[: len(first)] == first  # 앞의 조회 결과를 그대로 본다
        assert [m["role"] for m in recall[len(first) :]] == ["assistant", "tool", "user"]

    async def test_전부_빠졌는데_재호출도_못_내면_실패다(self):
        """ "조건에 맞는 추천이 없어요" 가 아니다 — 모델이 실패한 것이다 (#359 리뷰)."""
        llm = FakeLLM(propose("구슬 꿰기", "단추 꿰기", "동전 쌓기"))  # 재호출은 말로 끝난다
        result = await run(task(), context(BORN_20_MONTHS), client=llm)
        assert (result.status, result.model_calls, result.suggestions) == ("failed", 2, ())

    async def test_남은_게_있으면_재호출이_못_내도_있는_만큼_낸다(self):
        llm = FakeLLM(propose("구슬 꿰기", "그림자놀이", "색종이 접기"))
        result = await run(task(), context(BORN_20_MONTHS), client=llm)
        assert result.status == "completed"
        assert contents(result) == ["그림자놀이", "색종이 접기"]
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_문장부호만_다른_같은_후보는_건너뛴다(self):
        """최근 중복 검사와 같은 정규화다 — 공백만 지우면 "그림자 놀이!" 가 따로 들어간다."""
        llm = FakeLLM(
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("그림자 놀이!", "까꿍 놀이", "컵 쌓기"),
        )
        result = await run(task(), context(BORN_20_MONTHS), client=llm)
        assert contents(result) == ["그림자놀이", "색종이 접기", "까꿍 놀이"]

    async def test_재호출로도_못_채우면_있는_만큼_내고_안내한다(self):
        llm = FakeLLM(
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("단추 꿰기", "구슬 목걸이", "동전 쌓기"),
        )
        result = await run(task(), context(BORN_20_MONTHS), client=llm)
        assert contents(result) == ["그림자놀이", "색종이 접기"]
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_재호출해도_알레르기_확인_문구는_남는다(self):
        """식품 사전에 없는 알레르기(꽃가루)의 확인 문구는 run 에 한 벌이다 (#320). 재호출로 채운
        추천에도 같이 붙어야 한다."""
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="꽃가루", status="active")])
        ctx = ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=NOW,
            timezone=UTC,
            ports=in_memory_ports(CHILD, BORN_20_MONTHS, safety=safety),
        )
        llm = FakeLLM(
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("까꿍 놀이", "공 굴리기", "컵 쌓기"),
        )
        result = await run(task(), ctx, client=llm)
        assert result.model_calls == 2
        assert len(ctx.state.cautions) == 1 and "꽃가루" in ctx.state.cautions[0]
        # pipeline 은 결과만 받으므로 확인 문구는 결과의 readouts 로도 나가야 한다 (#380)
        (caution,) = [r for r in result.readouts if "꽃가루" in r.body]
        assert (caution.kind, caution.authored_by) == ("notice", "code")
        assert result.readouts[0] is caution  # 개수 안내보다 먼저 보인다

    async def test_다_빠져도_실패가_아니라_안내만_낸다(self):
        llm = FakeLLM(
            propose("구슬 꿰기", "단추 꿰기", "동전 쌓기"),
            propose("구슬 목걸이", "작은 블록 놀이", "자석 붙이기"),
        )
        result = await run(task(), context(BORN_20_MONTHS), client=llm)
        assert (result.status, result.suggestions) == ("completed", ())
        assert [r.code for r in result.readouts] == ["empty"]


class TestWeather:
    async def test_날씨를_불렀는데_못_읽으면_degraded_와_안내(self):
        weather = InMemoryWeather(uv="4", fail=frozenset({"forecast"}))
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task(), context(location=BUSAN, weather=weather), client=llm)
        assert result.status == "degraded"
        assert any(r.kind for r in result.readouts)
        assert "날씨는 확인하지 못했다" in llm.seen[0][0]["content"]
        assert "바깥 활동이 어렵다" not in llm.seen[0][0]["content"]

    async def test_비가_많이_오면_바깥_활동이_어렵다고_알린다(self):
        rain = InMemoryWeather(uv="1", forecast=RawForecast(sky="4", pcp="20.0mm", pop="90"))
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), context(location=BUSAN, weather=rain), client=llm)
        assert "바깥 활동이 어렵다" in llm.seen[0][0]["content"]

    async def test_오늘_비가_와도_내일_야외_후보는_나간다(self):
        """후보의 day 로 그날 판정을 본다. 모델이 lookup_weather 를 안 불러도 출력 tool 이
        그날을 판정한다 (#377)."""
        tomorrow = NOW.date() + timedelta(days=1)
        weather = InMemoryWeather(
            uv="1",
            forecast=RawForecast(sky="4", pcp="20.0mm", pop="90"),
            forecasts={tomorrow: RawForecast(sky="1", pcp="강수없음", pop="0")},
        )
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor", day="tomorrow")
        )
        result = await run(
            task("내일 밖에서 놀고 싶대"), context(location=BUSAN, weather=weather), client=llm
        )
        assert contents(result) == ["공원 산책", "그림자놀이", "색종이 접기"]
        assert tomorrow in weather.forecast_days

    async def test_오늘_비가_오면_오늘_야외_후보는_거절된다(self):
        rain = InMemoryWeather(uv="1", forecast=RawForecast(sky="4", pcp="20.0mm", pop="90"))
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor"),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task(), context(location=BUSAN, weather=rain), client=llm)
        assert contents(result) == ["종이컵 탑 쌓기", "그림자놀이", "색종이 접기"]
        rejected = llm.seen[1][-1]["content"]  # 거절 사유가 tool 결과로 돌아간다
        assert "그날(day)은 바깥 활동이 어려운 날" in rejected

    async def test_위치나_날씨_어댑터가_없으면_날씨_tool_을_열지_않는다(self):
        """부르면 늘 실패만 돌려준다. 프롬프트도 "확인하지 못했다" 로 간다."""
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), context(), client=llm)
        assert "날씨는 확인하지 못했다" in llm.seen[0][0]["content"]
        assert "위치를 모른다" in llm.seen[0][0]["content"]

    async def test_위치가_없으면_날씨를_부르지_않고_정상이다(self):
        """위치가 없으면 실내 전용으로 정상 동작한다 (4-3). "날씨를 못 읽었어요" 를 안 붙인다."""
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        ctx = context()
        result = await run(task(), ctx, client=llm)
        assert result.status == "completed"
        assert result.readouts == ()
        assert WEATHER_UNCHECKED in ctx.state.weather[ctx.today].notices

    async def test_맑으면_바깥_활동을_할_수_있다고_알린다(self):
        llm = FakeLLM(propose("공원 산책", "그림자놀이", "색종이 접기", setting="either"))
        result = await run(
            task(), context(location=BUSAN, weather=InMemoryWeather(uv="4")), client=llm
        )
        assert result.status == "completed"
        assert "바깥 활동을 할 수 있다" in llm.seen[0][0]["content"]


class TestDocEvidence:
    async def test_일반_추천은_예시_문서_행을_근거로_단다(self):
        """기록이 없는 아이도 어느 문서 행을 보고 골랐는지 suggestion_evidence 에 남는다."""
        row = ActivityDocRow(
            id=UUID(int=21),
            doc_key="act-test-tower",
            title="종이컵 탑 쌓기",
            body="종이컵을 쌓고 무너뜨린다",
            min_month=36,
            max_month=48,
            setting=ActivitySetting.INDOOR,
            materials=("종이컵",),
            caregiver_role=CaregiverRole.TOGETHER,
            physical_intensity=Intensity.LOW,
            involves_food=False,
        )
        docs = InMemoryActivityDocs([row])
        proposal = candidate(row.title)
        proposal["evidence"] = [{"id": str(row.id), "note": "이 나이 예시를 참고했어요"}]
        llm = FakeLLM(
            _tools(
                (
                    "propose_activity_candidates",
                    {"candidates": [proposal, candidate("그림자놀이"), candidate("색종이 접기")]},
                )
            )
        )
        result = await run(task(), context(docs=docs), client=llm)
        first = result.suggestions[0]
        assert first.kind == "general"
        assert [(c.ref.kind, c.ref.id) for c in first.citations] == [("activity_doc", row.id)]
        assert f"id {row.id}" in llm.seen[0][0]["content"]  # 프롬프트 [예시]에 id 가 실린다


class TestPrompt:
    async def test_월령은_개월_수만_주고_분류는_싣지_않는다(self):
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), context(), client=llm)
        system = llm.seen[0][0]["content"]
        assert "40개월" in system
        assert "general" not in system and "personalized" not in system
        assert "preschool" not in system

    async def test_예시는_아이와_날짜로_돌린다(self, monkeypatch):
        """같은 날 같은 아이는 같은 [예시], 날이나 아이가 바뀌면 바뀐다 (#343)."""
        seen: list[str] = []
        original = agent_module.search_activity_doc

        async def spy(docs, **kwargs):
            seen.append(kwargs["rotation"])
            return await original(docs, **kwargs)

        monkeypatch.setattr(agent_module, "search_activity_doc", spy)
        ctx = context()
        await run(
            task(), ctx, client=FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        )
        assert seen == [f"{CHILD}|{ctx.today.isoformat()}"]

    async def test_보호자_요청을_그대로_user_로_준다(self):
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task("비 오는데 뭐 하지"), context(), client=llm)
        assert llm.seen[0][1] == {"role": "user", "content": "비 오는데 뭐 하지"}
