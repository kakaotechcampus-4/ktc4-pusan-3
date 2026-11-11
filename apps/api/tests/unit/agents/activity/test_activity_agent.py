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
from zoneinfo import ZoneInfo

from app.agents.activity import agent as agent_module
from app.agents.activity.agent import MAX_STEPS, run
from app.agents.activity.areas import find_area
from app.agents.activity.context import ActivityContext
from app.agents.activity.prompt import RECALL_HINT
from app.agents.activity.schemas.common import (
    ActivitySetting,
    CaregiverRole,
    Intensity,
    PlaceCategory,
)
from app.agents.activity.store.inmemory import (
    InMemoryActivityDocs,
    InMemoryEvents,
    InMemoryPlaces,
    InMemorySafety,
    InMemoryWeather,
    in_memory_ports,
)
from app.agents.activity.store.ports import ActivityDocRow, CoarseLocation, PlaceRow, SafetyEntry
from app.agents.activity.weather import WEATHER_NOT_ASKED
from app.agents.common.llm_client import LLMResponse
from app.agents.common.reference import hazard_terms
from app.agents.common.schemas.task import DomainTask
from app.core.event_raw import RawEvent
from app.core.weather_raw import RawAdvisories, RawAdvisoryRow, RawForecast

CHILD = UUID(int=1)
NOW = datetime(2026, 10, 7, 1, tzinfo=UTC)  # KST 10:00 수요일
BORN_40_MONTHS = date(2023, 6, 1)
BORN_20_MONTHS = date(2025, 2, 1)
BUSAN = CoarseLocation(lat=35.18, lon=129.08)
KST = ZoneInfo("Asia/Seoul")
RAINY = RawForecast(sky="4", pcp="20.0mm", pop="90")

# 일반 추천은 문서 행이 근거다 (루트 §2). 기록 없는 아이의 후보가 이 행을 인용하게 둔다
DEFAULT_DOC = ActivityDocRow(
    id=UUID(int=20),
    doc_key="act-test-default",
    title="블록 쌓기",
    body="블록을 쌓고 무너뜨린다",
    min_month=0,
    max_month=72,
    setting=ActivitySetting.INDOOR,
    materials=("블록",),
    caregiver_role=CaregiverRole.TOGETHER,
    physical_intensity=Intensity.LOW,
    involves_food=False,
)


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
    content: str,
    *,
    setting: str = "indoor",
    materials=(),
    day: str = "today",
    place_name: str | None = None,
) -> dict[str, Any]:
    return {
        "place_name": place_name,
        "content": content,
        "setting": setting,
        "day": day,
        "materials": list(materials),
        "physical_intensity": "low",
        "involves_food": False,
        "caregiver_role": "together",
        "reason": "집에서 바로 할 수 있어요 오늘 오전이 비어 있어요",
        "evidence": [{"id": str(DEFAULT_DOC.id), "note": "이 나이 예시를 참고했어요"}],
    }


def propose(*contents: str, **kwargs) -> LLMResponse:
    return _tools(
        ("propose_activity_candidates", {"candidates": [candidate(c, **kwargs) for c in contents]})
    )


def memory() -> LLMResponse:
    return _tools(("search_activity_memory", {}))


def context(
    born=BORN_40_MONTHS, *, location=None, weather=None, docs=None, now=NOW, timezone=UTC
) -> ActivityContext:
    docs = docs if docs is not None else InMemoryActivityDocs([DEFAULT_DOC])
    return ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=now,
        timezone=timezone,
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
        assert result.readouts == ()  # 3개라 개수 안내 없음 · 실내 놀이만이라 날씨 안내 없음

    async def test_위험_경고는_그_추천에_싣는다(self):
        """40개월 — 구슬(작은 부품)은 72개월 미만 경고다. 결과 안내가 아니라 그 추천의 칸이다."""
        llm = FakeLLM(propose("구슬 꿰기", "그림자놀이", "색종이 접기"))
        result = await run(task(), context(), client=llm)
        small_parts = hazard_terms().axes["small_parts"].warning_text
        assert [draft.warnings for draft in result.suggestions] == [(small_parts,), (), ()]
        assert result.readouts == ()

    async def test_거절되면_같은_진입_안에서_고쳐_낸다(self):
        """바깥 활동이 어려운 날(비) 야외 후보는 거절된다. model_calls 는 그대로."""
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor"),
            propose("거실 보물찾기", "그림자놀이", "색종이 접기"),
        )
        rainy = InMemoryWeather(uv="4", forecast=RAINY)
        result = await run(task(), context(location=BUSAN, weather=rainy), client=llm)
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

    async def test_재호출로_합쳐도_경고는_그_추천을_따라간다(self):
        """40개월 — 전동킥보드는 차단, 구슬은 경고. 처음 통과한 구슬의 경고가 합친 뒤에도 남는다."""
        llm = FakeLLM(
            propose("전동킥보드 타기", "구슬 꿰기", "그림자놀이"),
            propose("색종이 접기", "컵 쌓기", "까꿍 놀이"),
        )
        result = await run(task(), context(), client=llm)
        small_parts = hazard_terms().axes["small_parts"].warning_text
        assert contents(result) == ["구슬 꿰기", "그림자놀이", "색종이 접기"]
        assert [draft.warnings for draft in result.suggestions] == [(small_parts,), (), ()]

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
            ports=in_memory_ports(
                CHILD, BORN_20_MONTHS, safety=safety, docs=InMemoryActivityDocs([DEFAULT_DOC])
            ),
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
    async def test_날씨를_불렀는데_못_읽으면_degraded_고_바깥도_낼_수_있다(self):
        """실내 놀이만 나가면 날씨 확인을 당부하지 않는다 — 묻지 않은 날과 같다."""
        weather = InMemoryWeather(uv="4", fail=frozenset({"forecast"}))
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task(), context(location=BUSAN, weather=weather), client=llm)
        assert result.status == "degraded"
        assert result.readouts == ()
        system = llm.seen[0][0]["content"]
        assert "날씨는 확인하지 못했다" in system
        assert "바깥 활동(outdoor)도 낼 수 있다" in system
        assert "바깥 활동이 어렵다" not in system

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
        """부르면 늘 실패만 돌려준다. 프롬프트는 "확인하지 않았다 · 바깥도 낼 수 있다" 로 간다."""
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), context(), client=llm)
        system = llm.seen[0][0]["content"]
        assert "날씨는 확인하지 않았다" in system
        assert "바깥 활동(outdoor)도 낼 수 있다" in system
        assert "위치를 모른다" in system

    async def test_위치가_없으면_날씨를_부르지_않고_정상이다(self):
        """묻지 않은 날은 실패가 아니다 (4-3). 실내 놀이만 나가면 날씨 안내를 붙이지 않는다."""
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        ctx = context()
        result = await run(task(), ctx, client=llm)
        assert result.status == "completed"
        assert result.readouts == ()
        assert ctx.state.weather[ctx.today].notices == (WEATHER_NOT_ASKED,)

    async def test_위치가_없어도_바깥_놀이를_내고_날씨_확인을_맡긴다(self):
        """1차 배포는 위치가 없다 — 실내만이면 공원 · 산책이 한 번도 안 나간다. 확인은 보호자 몫."""
        llm = FakeLLM(propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor"))
        result = await run(task(), context(), client=llm)
        assert result.status == "completed"
        assert contents(result)[0] == "공원 산책"
        assert [r.body for r in result.readouts] == [
            "날씨는 확인하지 않았어요. 바깥 놀이라면 나가기 전에 날씨를 한 번 봐 주세요."
        ]

    async def test_해가_져도_공원_산책은_막지_않는다(self):
        """해가 진 뒤에는 오늘 놀이터만 뺀다 — 공원은 보호자와 같이 걷는다 (#800)."""
        llm = FakeLLM(propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor"))
        night = datetime(2026, 10, 7, 21, 0, tzinfo=KST)
        result = await run(task(), context(now=night, timezone=KST), client=llm)
        assert contents(result)[0] == "공원 산책"
        assert "해가 져서 실내 놀이만 골랐어요." not in [r.body for r in result.readouts]

    async def test_다른_날_실내_놀이에는_오늘_밤_안내를_붙이지_않는다(self):
        """일요일 밤에 "다음 주 주말" 을 물었는데 "해가 져서" 가 붙던 버그 (#800)."""
        llm = FakeLLM(propose("키즈카페 탐색", "블록 쌓기", "역할놀이", day="다음 주 주말"))
        night = datetime(2026, 10, 11, 21, 0, tzinfo=KST)  # 일요일
        result = await run(task(), context(now=night, timezone=KST), client=llm)
        assert len(contents(result)) == 3
        assert all("어두워서" not in r.body for r in result.readouts)

    async def test_오늘_해가_져도_내일_바깥_놀이에는_해_진_안내를_붙이지_않는다(self):
        """안내는 결과에 나간 바깥 활동의 날로 고른다 — 오늘 판정을 붙이면 틀린다 (#515 리뷰)."""
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor", day="tomorrow")
        )
        night = datetime(2026, 10, 7, 21, 0, tzinfo=KST)
        result = await run(task(), context(now=night, timezone=KST), client=llm)
        assert contents(result)[0] == "공원 산책"
        assert [r.body for r in result.readouts] == [
            "날씨는 확인하지 않았어요. 바깥 놀이라면 나가기 전에 날씨를 한 번 봐 주세요."
        ]

    async def test_오늘_비가_와도_내일_바깥_놀이에는_실내만_안내를_붙이지_않는다(self):
        tomorrow = NOW.date() + timedelta(days=1)
        weather = InMemoryWeather(
            uv="4",
            forecast=RAINY,
            forecasts={tomorrow: RawForecast(sky="1", pcp="강수없음", pop="0")},
        )
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor", day="tomorrow")
        )
        result = await run(task(), context(location=BUSAN, weather=weather), client=llm)
        assert contents(result)[0] == "공원 산책"
        bodies = [r.body for r in result.readouts]
        assert not any("실내 놀이만" in body for body in bodies)

    async def test_예보를_못_읽어도_바깥_놀이를_내고_날씨_확인을_맡긴다(self):
        """조회 실패로 바깥을 막으면 위치를 켠 보호자가 안 켠 보호자보다 손해다 (#612 멘토 리뷰).
        "맑음" 이라고 하지 않고 확인 못 했다고 알린다 — 실패를 기본값으로 메우지 않는다 (D8)."""
        llm = FakeLLM(propose("공원 산책", "그림자놀이", "색종이 접기", setting="outdoor"))
        broken = InMemoryWeather(uv="4", fail=frozenset({"forecast"}))
        result = await run(task(), context(location=BUSAN, weather=broken), client=llm)
        assert contents(result)[0] == "공원 산책"
        assert result.status == "degraded"
        assert [r.body for r in result.readouts] == [
            "날씨를 확인하지 못했어요. 바깥 놀이라면 나가기 전에 날씨와 특보를 한 번 봐 주세요."
        ]

    async def test_예보를_못_읽었는데_특보가_경보면_막고_degraded_다(self):
        """막힌 날은 "확인하지 못했어요" 를 화면에 붙이지 않지만 예보를 못 읽은 건 status 에 남는다.
        모델에게는 "확인하지 못했다 · 바깥도 낼 수 있다" 가 아니라 "어렵다" 가 간다 (#645 리뷰)."""
        heavy_rain = RawAdvisoryRow(
            area_code="L1100200",
            warn_var="2",  # 호우
            warn_stress="1",  # 경보
            command="1",
            tm_fc="202610070900",
            tm_seq="1",
        )
        weather = InMemoryWeather(
            uv="4",
            fail=frozenset({"forecast"}),
            advisories=RawAdvisories(result_code="00", rows=(heavy_rain,)),
        )
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task(), context(location=BUSAN, weather=weather), client=llm)
        assert result.status == "degraded"
        assert [r.body for r in result.readouts] == [
            "오늘은 바깥 활동이 어려운 날이라 실내 놀이만 골랐어요."
        ]
        system = llm.seen[0][0]["content"]
        assert "오늘은 바깥 활동이 어렵다" in system
        assert "바깥 활동(outdoor)도 낼 수 있다" not in system

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
        docs = InMemoryActivityDocs([row, DEFAULT_DOC])
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
        system = llm.seen[0][0]["content"]
        # 프롬프트 [예시]에는 짧은 id 만 실린다 — UUID 를 베끼다 두 행의 id 를 이어 붙인다
        assert "id 예1 · " in system and str(row.id) not in system


async def test_예시의_짧은_id_를_run_state_에_남긴다():
    """출력 tool 이 이 표로 문서 행 id 로 되돌린다. 프롬프트에 실린 순서 그대로다."""
    ctx = context()
    llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
    await run(task(), ctx, client=llm)
    system = llm.seen[0][0]["content"]
    assert ctx.state.example_ids  # 빈 표면 아래 비교가 아무것도 안 본다
    assert set(ctx.state.example_ids.values()) == set(ctx.state.seen_docs)
    for short_id, doc_id in ctx.state.example_ids.items():
        assert f"id {short_id} · {ctx.state.seen_docs[doc_id].title}" in system


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


class TestArea:
    """보호자가 말한 지역으로 장소를 찾는다 — 위치가 없어도 된다."""

    PARK = PlaceRow(
        name="○○공원",
        category=PlaceCategory.PARK,
        distance_m=500,
        source="city_park",
        data_date=date(2026, 6, 30),
    )

    def area_context(self, *, weather=None) -> ActivityContext:
        return ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=NOW,
            timezone=KST,
            ports=in_memory_ports(
                CHILD,
                BORN_40_MONTHS,
                weather=weather,
                docs=InMemoryActivityDocs([DEFAULT_DOC]),
                places=InMemoryPlaces([self.PARK]),
            ),
        )

    async def test_위치가_없으면_지역을_말했을_때만_찾는다고_알린다(self):
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), self.area_context(), client=llm)
        system = llm.seen[0][0]["content"]
        assert "위치를 모른다" in system
        assert "area" in system

    async def test_위치가_없어도_말한_지역의_장소를_붙인다(self):
        park = {**candidate("모래성 쌓기", setting="outdoor"), "place_name": "○○공원"}
        llm = FakeLLM(
            _tools(("search_nearby_places", {"category": "park", "area": "해운대"})),
            _tools(
                (
                    "propose_activity_candidates",
                    {"candidates": [park, candidate("그림자놀이"), candidate("색종이 접기")]},
                )
            ),
        )
        result = await run(task("해운대 가서 뭐 하고 놀까"), self.area_context(), client=llm)
        assert contents(result)[0] == "○○공원에서 모래성 쌓기"
        assert result.status == "completed"

    async def test_못_찾은_지역이면_장소_없이_한_줄_안내를_붙인다(self):
        llm = FakeLLM(
            _tools(("search_nearby_places", {"category": "park", "area": "서면"})),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task("서면 가서 뭐 하고 놀까"), self.area_context(), client=llm)
        assert contents(result) == ["종이컵 탑 쌓기", "그림자놀이", "색종이 접기"]
        assert [r.body for r in result.readouts] == [
            "말씀하신 지역을 찾지 못해 장소 없이 골랐어요. "
            "시·도와 구·군까지 말씀해 주시면 그 지역 장소도 찾아볼게요."
        ]

    async def test_그_지역_날씨를_못_읽으면_degraded_다(self):
        """보호자 위치가 없어도 그 지역 날씨는 물었다 — 묻지 않은 날이 아니다."""
        broken = InMemoryWeather(fail=frozenset({"forecast"}))
        llm = FakeLLM(
            _tools(("search_nearby_places", {"category": "library", "area": "해운대"})),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(
            task("해운대 가서 뭐 하고 놀까"), self.area_context(weather=broken), client=llm
        )
        assert result.status == "degraded"


# ── 음식 칸 · 재호출 힌트 · 요청한 음식 안내 (#582) ───────────────────────────────────


def _allergic(label: str, born=BORN_40_MONTHS) -> ActivityContext:
    safety = InMemorySafety([SafetyEntry(kind="allergy", label=label, status="active")])
    return ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(CHILD, born, safety=safety, docs=InMemoryActivityDocs([DEFAULT_DOC])),
    )


def _food(content: str, *foods: str) -> dict[str, Any]:
    return {**candidate(content), "involves_food": True, "foods": list(foods)}


def _propose_foods(*items: dict[str, Any]) -> LLMResponse:
    return _tools(("propose_activity_candidates", {"candidates": list(items)}))


class TestFoods:
    async def test_재호출_힌트는_빠진_이유와_상관없이_같다(self):
        """알레르기로 빠졌을 때만 힌트를 주면 그 자체가 사유다 (Tool_공통 §5-2 · #582)."""
        hazard = FakeLLM(
            propose("구슬 꿰기", "그림자놀이", "색종이 접기"),
            propose("까꿍 놀이", "공 굴리기", "컵 쌓기"),
        )
        await run(task(), context(BORN_20_MONTHS), client=hazard)
        allergy = FakeLLM(
            _propose_foods(
                _food("쿠키 굽기", "쿠키"), candidate("그림자놀이"), candidate("컵 쌓기")
            ),
            propose("까꿍 놀이", "공 굴리기", "색종이 접기"),
        )
        await run(task(), _allergic("밀"), client=allergy)

        first, second = hazard.seen[1][-1]["content"], allergy.seen[1][-1]["content"]
        assert RECALL_HINT in first and RECALL_HINT in second
        assert "알레르기" not in second and "밀" not in second

    async def test_요청한_음식이_막히면_보호자가_쓴_말로_빼고_골랐다고_안내한다(self):
        """등록한 알레르기 이름이 아니라 보호자가 쓴 어절을 싣는다 — Food 요청 안내와 같다."""
        llm = FakeLLM(propose("그림자놀이", "색종이 접기", "까꿍 놀이"))

        result = await run(task("땅콩버터 샌드위치 만들기 하자"), _allergic("땅콩"), client=llm)

        (notice,) = [r for r in result.readouts if "요청하신" in r.body]
        assert notice.body == (
            "요청하신 땅콩버터에는 등록해 두신 알레르기와 겹치는 것이 있어서 그런 놀이는 빼고 "
            "골랐어요."
        )
        assert (notice.kind, notice.authored_by) == ("notice", "code")

    async def test_빼_달라고_한_음식은_안내하지_않는다(self):
        llm = FakeLLM(propose("그림자놀이", "색종이 접기", "까꿍 놀이"))

        result = await run(task("땅콩 빼고 놀 거리 알려 줘"), _allergic("땅콩"), client=llm)

        assert not [r for r in result.readouts if "요청하신" in r.body]

    async def test_연령으로_막힌_음식도_보호자가_쓴_말로_안내한다(self):
        llm = FakeLLM(propose("그림자놀이", "색종이 접기", "까꿍 놀이"))

        result = await run(
            task("꿀 넣은 요거트 맛보기 해 볼까"), context(date(2025, 12, 1)), client=llm
        )

        (notice,) = [r for r in result.readouts if "요청하신" in r.body]
        assert notice.body == "요청하신 꿀은 12개월 전에는 먹이지 않아서 그런 놀이는 빼고 골랐어요."

    async def test_다_빠지면_예시로_채우지_않고_안내만_낸다(self):
        """[예시] 행으로 채우면 요청과 상관없는 추천이 나간다 (#582 실험) — 정직하게 0개."""
        peanut = [_food(f"땅콩버터 놀이 {n}", "땅콩버터") for n in range(1, 4)]
        llm = FakeLLM(_propose_foods(*peanut), _propose_foods(*peanut))

        result = await run(task("땅콩버터로 놀고 싶어"), _allergic("땅콩"), client=llm)

        assert (result.status, result.suggestions, result.model_calls) == ("completed", (), 2)
        codes = [r.code for r in result.readouts]
        assert "empty" in codes
        assert any("요청하신 땅콩버터" in r.body for r in result.readouts)


class TestEvents:
    """행사 어댑터가 있으면 search_events 가 열리고, 찾은 행사는 장소처럼 이름이 붙는다."""

    HAEUNDAE = find_area("해운대")
    FEST = RawEvent(
        content_id="1",
        title="해운대 모래축제",
        start="20261001",
        end="20261012",
        mapx=f"{HAEUNDAE.location.lon}",
        mapy=f"{HAEUNDAE.location.lat}",
        kind_code="EV01",
        progress="",
    )

    def events_context(self) -> ActivityContext:
        return ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=NOW,
            timezone=KST,
            ports=in_memory_ports(
                CHILD,
                BORN_40_MONTHS,
                docs=InMemoryActivityDocs([DEFAULT_DOC]),
                events=InMemoryEvents([self.FEST]),
            ),
        )

    async def test_행사_어댑터가_있으면_행사를_찾을_수_있다고_알린다(self):
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), self.events_context(), client=llm)
        assert "search_events" in llm.seen[0][0]["content"]

    async def test_행사_어댑터가_없으면_행사_이야기를_하지_않는다(self):
        llm = FakeLLM(propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        await run(task(), context(), client=llm)
        assert "search_events" not in llm.seen[0][0]["content"]

    async def test_말한_지역의_행사를_붙이고_행사_확인_문구를_싣는다(self):
        fest = {
            **candidate("모래 조각 구경하기", setting="outdoor"),
            "place_name": "해운대 모래축제",
        }
        llm = FakeLLM(
            _tools(("search_events", {"area": "해운대"})),
            _tools(
                (
                    "propose_activity_candidates",
                    {"candidates": [fest, candidate("그림자놀이"), candidate("색종이 접기")]},
                )
            ),
        )
        result = await run(task("해운대 축제 가볼까"), self.events_context(), client=llm)
        assert contents(result)[0] == "해운대 모래축제에서 모래 조각 구경하기"
        assert result.suggestions[0].place_notice.startswith("한국관광공사 행사 정보예요.")
        # 행사는 근거가 아니다 — [예시] 문서 행만 남는다
        assert {c.ref.kind for c in result.suggestions[0].citations} == {"activity_doc"}


class TestWhen:
    """언제 하는 활동인지는 보호자가 쓴 표현으로 받는다 (#800)."""

    async def test_지난_날을_내면_고쳐_다시_내게_한다(self):
        llm = FakeLLM(
            propose("공원 산책", "그림자놀이", "색종이 접기", day="어제"),
            propose("공원 산책", "그림자놀이", "색종이 접기", day="내일"),
        )
        result = await run(task(), context(), client=llm)
        assert len(contents(result)) == 3
        retry = llm.seen[1][-1]["content"]
        assert "지난 날" in retry

    async def test_밤에_놀이터를_찾으려_하면_안내를_붙인다(self):
        """오늘 밤 놀이터는 장소 조회가 막는다. 막혔으면 "어두워서 놀이터는 뺐어요" 를 싣는다."""
        playground = PlaceRow(
            name="○○놀이터",
            category=PlaceCategory.PLAYGROUND,
            distance_m=300,
            source="kids_play_facility",
            data_date=None,
        )
        ctx = ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=datetime(2026, 10, 7, 21, 0, tzinfo=KST),
            timezone=KST,
            ports=in_memory_ports(
                CHILD,
                BORN_40_MONTHS,
                docs=InMemoryActivityDocs([DEFAULT_DOC]),
                places=InMemoryPlaces([playground]),
            ),
            location=CoarseLocation(lat=35.18, lon=129.08),
        )
        llm = FakeLLM(
            _tools(("search_nearby_places", {"category": "playground", "day": "오늘"})),
            propose("거실 보물찾기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task(), ctx, client=llm)
        assert len(contents(result)) == 3
        assert "지금은 어두워서 놀이터는 뺐어요." in [r.body for r in result.readouts]


class TestPlaceFit:
    """장소는 "이 아이가 지금 어떤 곳이 맞는지" 를 먼저 쓰고, 시설을 지어내지 않는다 (#814)."""

    async def test_프롬프트가_장소를_고른_이유와_시설_금지를_적는다(self):
        llm = FakeLLM(propose("거실 보물찾기", "그림자놀이", "색종이 접기"))
        await run(task("키즈카페 갈 만한 데 있어?"), context(), client=llm)
        system = llm.seen[0][0]["content"]
        assert "왜 그 종류의 장소인지" in system
        assert "을 짐작해 쓰지 않는다" in system
        assert "기록에 장소 이름이 있으면" in system

    async def test_보호자가_말한_결이_오늘_상태보다_먼저다(self):
        """많이 뛴 날이어도 보호자가 "또 신나게" 를 원하면 그대로 — 기록은 그 안에서 고르는 데만."""
        llm = FakeLLM(propose("거실 보물찾기", "그림자놀이", "색종이 접기"))
        await run(task("오후에도 몸으로 신나게 놀 데 추천해 줘"), context(), client=llm)
        system = llm.seen[0][0]["content"]
        assert "보호자가 이번 요청에서 원하는 결" in system


class TestDarkPlaygroundRun:
    async def test_내일로_찾은_놀이터를_오늘_후보로_내면_거절하고_안내를_붙인다(self):
        """장소 조회는 day 마다 막지만, 내일로 찾은 놀이터를 오늘 후보에 붙이는 길을 출력 검증이
        막는다 (#805 리뷰)."""
        playground = PlaceRow(
            name="○○놀이터",
            category=PlaceCategory.PLAYGROUND,
            distance_m=300,
            source="kids_play_facility",
            data_date=None,
        )
        ctx = ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=datetime(2026, 10, 7, 21, 0, tzinfo=KST),
            timezone=KST,
            ports=in_memory_ports(
                CHILD,
                BORN_40_MONTHS,
                docs=InMemoryActivityDocs([DEFAULT_DOC]),
                places=InMemoryPlaces([playground]),
            ),
            location=CoarseLocation(lat=35.18, lon=129.08),
        )
        llm = FakeLLM(
            _tools(("search_nearby_places", {"category": "playground", "day": "내일"})),
            propose(
                "미끄럼틀 타기", "그림자놀이", "색종이 접기", place_name="○○놀이터", day="오늘"
            ),
            propose("거실 보물찾기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task(), ctx, client=llm)
        assert "미끄럼틀 타기" not in " ".join(contents(result))
        assert "지금은 어두워서 놀이터는 뺐어요." in [r.body for r in result.readouts]


class TestSaidTexts:
    """해석한 요청은 모델에게 가고, "보호자가 말했는가" 를 보는 검사는 보호자 원문만 읽는다."""

    async def test_해석한_요청과_보호자_원문을_따로_둔다(self):
        ctx = context()
        asked = DomainTask(
            run_id="run-1",
            agent="activity",
            task_type=None,
            request_texts=("컵 쌓기 말고 더 쉬운 실내 놀이 추천",),
            original_texts=("더 쉬운 건 없어?",),
        )
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))

        await run(asked, ctx, client=llm)

        assert ctx.state.request_texts == ("컵 쌓기 말고 더 쉬운 실내 놀이 추천",)
        assert ctx.state.said() == ("더 쉬운 건 없어?",)

    async def test_원문_칸이_비면_요청_글이_말한_글이다(self):
        ctx = context()
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))

        await run(task("공놀이 하고 싶어"), ctx, client=llm)

        assert ctx.state.original_texts == ()
        assert ctx.state.said() == ("공놀이 하고 싶어",)


class TestLocationMissing:
    """위치도 지역도 없으면 장소 · 행사를 못 찾는다 — 왜 집 놀이만 나오는지 한 줄로 알린다
    (11-09 운영: "주말에 할만한 거" 에 집 놀이만)."""

    NOTICE = "위치를 허용하거나 지역(예: 부산 해운대구)을 말씀해 주시면 행사 · 장소도 찾아볼게요."

    def ctx(self, *, location=None):
        return ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=NOW,
            timezone=UTC,
            ports=in_memory_ports(
                CHILD,
                BORN_40_MONTHS,
                docs=InMemoryActivityDocs([DEFAULT_DOC]),
                places=InMemoryPlaces([]),
            ),
            location=location,
        )

    def bodies(self, result):
        return [r.body for r in result.readouts]

    async def test_나들이를_물었는데_위치도_지역도_없으면_안내한다(self):
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task("이번 주 주말에 할만한 거 있을까?"), self.ctx(), client=llm)
        assert self.NOTICE in self.bodies(result)

    async def test_장소를_찾으려다_위치가_없어_막혀도_안내한다(self):
        llm = FakeLLM(
            _tools(("search_nearby_places", {"category": "park", "day": "오늘"})),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task("애랑 할만한거 뭐있을까"), self.ctx(), client=llm)
        assert self.NOTICE in self.bodies(result)

    async def test_집_놀이를_물었으면_안내하지_않는다(self):
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task("오늘 집에서 뭐 하고 놀까"), self.ctx(), client=llm)
        assert self.NOTICE not in self.bodies(result)

    async def test_위치가_있으면_안내하지_않는다(self):
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(
            task("이번 주 주말에 할만한 거 있을까?"),
            self.ctx(location=CoarseLocation(lat=35.18, lon=129.08)),
            client=llm,
        )
        assert self.NOTICE not in self.bodies(result)

    async def test_나들이_낱말이_있어도_집에서를_물었으면_안내하지_않는다(self):
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(task("주말에 집에서 놀 거 어디 없을까"), self.ctx(), client=llm)
        assert self.NOTICE not in self.bodies(result)

    async def test_앞_턴에서_나들이를_물었어도_이번_말만_본다(self):
        asked = DomainTask(
            run_id="run-1",
            agent="activity",
            task_type=None,
            request_texts=("오늘 그냥 뭐 하고 놀까",),
            prior_texts=("주말에 갈 만한 행사 있어?",),
        )
        llm = FakeLLM(memory(), propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"))
        result = await run(asked, self.ctx(), client=llm)
        assert self.NOTICE not in self.bodies(result)

    async def test_말한_지역을_못_찾았으면_지역_안내만_붙인다(self):
        llm = FakeLLM(
            _tools(
                (
                    "search_nearby_places",
                    {"category": "indoor_playground", "day": "오늘", "area": "가상동"},
                )
            ),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task("가상동 근처 키즈카페 갈 만한 곳"), self.ctx(), client=llm)
        bodies = self.bodies(result)
        assert self.NOTICE not in bodies
        assert any("말씀하신 지역을 찾지 못해" in body for body in bodies)


class TestNoEvents:
    """행사를 물었는데 그날 열리는 행사가 없으면 없다고 한 줄로 알린다 — 말하지 않으면 행사 조회가
    고장 난 것처럼 보인다(11-11 운영: "부산 근처 다른 지역 행사" 에 일반 놀이만). 코드 문구라
    모델이 이유 문장에 쓰는지에 기대지 않는다."""

    NEAR = (
        "가까운 곳(20km 안)에서 그날 열리는 행사를 찾지 못했어요. "
        "다른 지역은 '김해 행사'처럼 지역 이름을 말씀해 주시면 찾아볼게요."
    )
    AREA = "말씀하신 지역에서 그날 열리는 행사를 찾지 못했어요."
    UNAVAILABLE = "행사 정보를 지금 불러오지 못해서 행사 없이 골랐어요."
    HAEUNDAE = find_area("해운대")

    def ctx(self, events: InMemoryEvents) -> ActivityContext:
        return ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=NOW,
            timezone=KST,
            ports=in_memory_ports(
                CHILD, BORN_40_MONTHS, docs=InMemoryActivityDocs([DEFAULT_DOC]), events=events
            ),
            location=BUSAN,
        )

    def fest(self, near: CoarseLocation) -> RawEvent:
        return RawEvent(
            content_id="1",
            title="가상 모래축제",
            start="20261001",
            end="20261012",
            mapx=f"{near.lon}",
            mapy=f"{near.lat}",
            kind_code="EV01",
            progress="",
        )

    async def ask(self, text: str, events: InMemoryEvents, *args: dict[str, Any]) -> list[str]:
        llm = FakeLLM(
            _tools(*(("search_events", {"day": "오늘", **arg}) for arg in args)),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(task(text), self.ctx(events), client=llm)
        return [r.body for r in result.readouts]

    async def test_가까운_곳에_행사가_없으면_반경과_지역_이름으로_찾는_법을_알린다(self):
        far = CoarseLocation(lat=35.86, lon=128.63)  # 약 80km — 반경 밖
        assert self.NEAR in await self.ask(
            "오늘 갈 만한 행사 있어?", InMemoryEvents([self.fest(far)]), {}
        )

    async def test_말한_지역에_행사가_없으면_그_지역에서_못_찾았다고_알린다(self):
        bodies = await self.ask("해운대 축제 가볼까", InMemoryEvents([]), {"area": "해운대"})
        assert self.AREA in bodies and self.NEAR not in bodies

    async def test_행사_정보를_못_불러오면_알린다(self):
        bodies = await self.ask("오늘 갈 만한 행사 있어?", InMemoryEvents(fail=True), {})
        assert self.UNAVAILABLE in bodies

    async def test_행사를_찾았으면_알리지_않는다(self):
        bodies = await self.ask("오늘 갈 만한 행사 있어?", InMemoryEvents([self.fest(BUSAN)]), {})
        assert not {self.NEAR, self.AREA, self.UNAVAILABLE} & set(bodies)

    async def test_행사를_묻지_않았으면_모델이_찾아봤어도_알리지_않는다(self):
        bodies = await self.ask("오늘 밖에서 뭐 하고 놀까", InMemoryEvents([]), {})
        assert not {self.NEAR, self.AREA, self.UNAVAILABLE} & set(bodies)

    async def test_앞에서_보여_준_행사뿐이면_그_안내만_붙인다(self):
        asked = DomainTask(
            run_id="run-1",
            agent="activity",
            task_type=None,
            request_texts=("다른 행사는 없어?",),
            shown_before=("가상 모래축제에서 모래 조각 구경하기",),
        )
        llm = FakeLLM(
            _tools(("search_events", {"day": "오늘"})),
            propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기"),
        )
        result = await run(asked, self.ctx(InMemoryEvents([self.fest(BUSAN)])), client=llm)
        bodies = [r.body for r in result.readouts]
        assert "앞에서 보여 드린 행사 말고는 그날 열리는 행사를 더 찾지 못했어요." in bodies
        assert self.NEAR not in bodies
