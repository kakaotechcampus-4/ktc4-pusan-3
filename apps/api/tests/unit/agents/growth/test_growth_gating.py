"""Growth 월령 · 동의 경계를 run() 결과로 고정한다 — 경계 양쪽, 닫힘 문구 글자, 모델 호출 수.

tool 이 열리는 월령은 test_growth_registry, 카테고리 허용표는 test_growth_routine, 기준일(KST)과
동의별 읽기는 test_growth_context 가 본다. 여기는 그 판정이 run() 을 지나 화면 결과로 어떻게
나가는지다 — growth_plan §10 · test_expected_growth §10 · 롤아웃 게이트 G2 · G3.

예절 · 습관은 모델이 카테고리를 정해야 코드가 닫을 수 있어 닫혀도 모델 1회다. 교육 · 리듬 안내
닫힘은 모델 0회다.
"""

import re
from uuid import UUID

from app.agents.growth.agent import run
from app.agents.growth.doc_seed import growth_doc_seed
from app.agents.growth.readouts import READOUTS
from app.agents.growth.store.inmemory import InMemoryGrowthDocs, InMemorySafety
from app.agents.growth.store.ports import GrowthDocRow
from tests.unit.agents.growth.support import birth_for, context, task
from tests.unit.agents.growth.test_growth_agent import FakeLLM, call

_EXAMPLE_ID = re.compile(r"^- id ([0-9a-f-]{36}) ·", re.M)
_SEED = {row.id: row for row in (entry.to_row() for entry in growth_doc_seed())}
LEARN = "숫자 놀이 뭐 없어?"
MEAL = "밥 먹을 때 혼자 하게 하고 싶어"


def at(months: int, **ports):
    return context(birth_for(months), docs=InMemoryGrowthDocs.from_seed(), **ports)


def says(category: str, subject: str) -> FakeLLM:
    """모델은 카테고리와 행동 이름만 정한다 — 닫힘 · 되묻기는 코드가 고른다."""
    return FakeLLM(
        call("propose_routine_plan", {"category": category, "subject": subject, "items": []})
    )


def examples(llm: FakeLLM) -> list[GrowthDocRow]:
    """첫 호출의 system 프롬프트 [예시]에 실린 문서 행."""
    system = llm.seen[0][0]["content"]
    return [_SEED[UUID(raw)] for raw in _EXAMPLE_ID.findall(system)]


def ask(key: str, subject: str) -> tuple[str, ...]:
    return (READOUTS.render(key, subject=subject).body,)


class TestEducationAt12:
    async def test_11개월은_모델_없이_돌_전_안내다(self):
        llm = FakeLLM()
        result = await run(task("learning_suggestion", LEARN), at(11), client=llm)
        assert (result.status, result.model_calls, llm.seen) == ("blocked", 0, [])
        assert result.readouts == (READOUTS.render("closed.infant_learning"),)

    async def test_12개월은_표준보육과정_예시를_싣고_모델까지_간다(self):
        llm = FakeLLM()
        await run(task("learning_suggestion", LEARN), at(12), client=llm)
        rows = examples(llm)
        assert rows and all(row.min_month <= 12 < row.max_month <= 36 for row in rows)


class TestCurriculumAt36:
    async def test_35개월은_표준보육과정에서_쓴_행이다(self):
        llm = FakeLLM()
        await run(task("learning_suggestion", LEARN), at(35), client=llm)
        rows = examples(llm)
        assert rows and all(row.max_month <= 36 for row in rows)

    async def test_36개월은_누리과정에서_쓴_행이다(self):
        llm = FakeLLM()
        await run(task("learning_suggestion", LEARN), at(36), client=llm)
        rows = examples(llm)
        assert rows and all(row.min_month >= 36 for row in rows)


class TestRoutineAt12:
    async def test_11개월은_모델_없이_리듬_안내_하나다(self):
        llm = FakeLLM()
        result = await run(task("routine_coaching", MEAL), at(11), client=llm)
        assert (result.status, result.model_calls, result.suggestions) == ("completed", 0, ())
        assert [r.kind for r in result.readouts] == ["rhythm_info"] and llm.seen == []

    async def test_12개월은_모델이_카테고리를_정하고_기록이_없어_되묻는다(self):
        result = await run(
            task("routine_coaching", MEAL), at(12), client=says("mealtime", "숟가락 쓰기")
        )
        assert (result.status, result.model_calls) == ("completed", 1)
        assert result.needs_observation == ask("ask.routine_current", "숟가락 쓰기")


class TestMannerAt24:
    REQUEST = "어른 보면 인사를 안 해."

    async def test_23개월_예절은_닫힌다(self):
        result = await run(
            task("routine_coaching", self.REQUEST), at(23), client=says("social_manner", "인사하기")
        )
        assert (result.status, result.model_calls) == ("blocked", 1)
        assert result.readouts == (READOUTS.render("closed.manner_under24"),)

    async def test_24개월_예절은_닫히지_않고_지금_수준을_되묻는다(self):
        result = await run(
            task("routine_coaching", self.REQUEST), at(24), client=says("social_manner", "인사하기")
        )
        assert result.status == "completed" and result.readouts == ()
        assert result.needs_observation == ask("ask.routine_current", "인사하기")


class TestHabitAt36:
    REQUEST = "손톱을 자꾸 물어뜯어."

    async def test_35개월_습관은_교정_대신_닫힌다(self):
        result = await run(
            task("routine_coaching", self.REQUEST), at(35), client=says("habit", "손톱 물어뜯기")
        )
        assert (result.status, result.model_calls) == ("blocked", 1)
        assert result.readouts == (READOUTS.render("closed.habit_under36"),)

    async def test_36개월_습관은_닫히지_않고_요즘도_하는지_되묻는다(self):
        result = await run(
            task("routine_coaching", self.REQUEST), at(36), client=says("habit", "손톱 물어뜯기")
        )
        assert result.status == "completed" and result.readouts == ()
        assert result.needs_observation == ask("ask.habit_current", "손톱 물어뜯기")


class TestConsentWithdrawn:
    """동의 철회는 성장 추이만 닫는다.

    성장 추이 쪽은 test_growth_context 의 TestRun · TestConsent 가 본다.
    """

    async def test_교육은_건강정보를_읽지_않고_모델까지_간다(self):
        safety = InMemorySafety()
        llm = FakeLLM()
        await run(
            task("learning_suggestion", LEARN), at(30, consent=False, safety=safety), client=llm
        )
        assert safety.calls == 0 and llm.seen

    async def test_루틴은_그대로_열린다(self):
        result = await run(
            task("routine_coaching", "양치를 혼자 하게 하려면 어떻게 해?"),
            at(30, consent=False),
            client=says("self_care", "양치"),
        )
        assert (result.status, result.model_calls) == ("completed", 1)
        assert result.needs_observation == ask("ask.routine_current", "양치")
