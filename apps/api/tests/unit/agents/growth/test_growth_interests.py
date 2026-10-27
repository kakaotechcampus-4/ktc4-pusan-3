"""도서 검색어의 근거 — 교육 · 관심 · 놀이 · 생활 루틴 기록을 코드가 점수로 줄 세운다.

출처 가중치 · 최근성 · 극성 · 낱말 병합은 코드가 정하고, 그 낱말로 어떻게 검색할지는 모델이 정한다.
음식 기록은 읽지 않는다 — 읽는 포트 자체가 없다.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.common.evidence import AffinityRow
from app.agents.growth.context import build_gate
from app.agents.growth.schemas.memory import SearchBookInterestsArgs
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemoryGrowthMemory
from app.agents.growth.store.ports import (
    ActivityObservation,
    EducationObservation,
    RoutineObservation,
)
from app.agents.growth.tools.interests import (
    MAX_EVIDENCE_PER_THEME,
    MAX_THEMES,
    search_book_interests,
    search_term,
)
from tests.unit.agents.growth.support import CHILD, TODAY, birth_for, context

BOOK = GrowthTaskType.BOOK_SUGGESTION


def affinity(n: int, key: str, *, state="confirmed", days_ago=5, strength=0.9, polarity=1):
    return AffinityRow(
        id=UUID(int=100 + n),
        merge_key=key,
        state=state,
        polarity=polarity,
        strength=strength,
        last_observed_on=TODAY - timedelta(days=days_ago),
    )


def education(n: int, subject: str, *, days_ago=1, polarity=1):
    return EducationObservation(
        id=UUID(int=200 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        topic=f"{subject} 기록",
        polarity=polarity,
    )


def activity(n: int, subject: str, *, days_ago=1, polarity=1):
    return ActivityObservation(
        id=UUID(int=300 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        activity=f"{subject} 놀이",
        polarity=polarity,
    )


def routine(n: int, subject: str, category="self_care", *, days_ago=1, polarity=0):
    return RoutineObservation(
        id=UUID(int=400 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        routine_category=category,
        polarity=polarity,
        assistance_level="partial_assist",
    )


async def interests(months=48, **memory):
    ctx = context(birth_for(months), memory=InMemoryGrowthMemory(**memory))
    ctx.state.gate = await build_gate(ctx, BOOK)
    result = await search_book_interests(ctx, SearchBookInterestsArgs())
    return ctx, result


def keywords(result) -> list[str]:
    return [theme["keyword"] for theme in result.data["themes"]]


class TestPriority:
    async def test_확정_관심이_더_최근의_교육_기록보다_앞선다(self):
        _, result = await interests(
            affinities=[("education", affinity(1, "공룡"))],
            education=[education(1, "숫자", days_ago=0)],
        )
        assert keywords(result) == ["공룡", "숫자"]

    async def test_같은_출처면_더_최근_기록이_앞선다(self):
        _, result = await interests(
            education=[education(1, "공룡", days_ago=10), education(2, "숫자", days_ago=1)]
        )
        assert keywords(result) == ["숫자", "공룡"]

    async def test_출처_순서는_확정_관심_후보_관심_교육_놀이_루틴이다(self):
        _, result = await interests(
            affinities=[
                ("education", affinity(1, "공룡")),
                ("activity", affinity(2, "자동차", state="candidate", strength=0.8)),
            ],
            education=[education(1, "숫자")],
            activities=[activity(1, "공놀이")],
            routine=[routine(1, "양치하기", polarity=-1)],
        )
        assert keywords(result) == ["공룡", "자동차", "숫자", "공놀이", "양치"]

    async def test_기록이_여러_번_쌓인_낱말이_한_번_본_낱말보다_앞선다(self):
        _, result = await interests(
            education=[
                education(1, "공룡", days_ago=3),
                education(2, "공룡", days_ago=4),
                education(3, "숫자", days_ago=3),
            ]
        )
        assert keywords(result) == ["공룡", "숫자"]

    async def test_극성이_중립인_기록은_긍정보다_낮게_센다(self):
        _, result = await interests(
            education=[education(1, "숫자", polarity=0), education(2, "공룡", polarity=1)]
        )
        assert keywords(result) == ["공룡", "숫자"]


class TestMerge:
    async def test_서로_포함하는_낱말은_한_주제로_합치고_출처를_다_보인다(self):
        _, result = await interests(
            affinities=[("activity", affinity(1, "농구"))],
            activities=[activity(1, "농구공")],
        )
        assert keywords(result) == ["농구"]
        theme = result.data["themes"][0]
        assert theme["signals"] == ["확정 관심", "놀이 기록"]
        assert len(theme["evidence"]) == 2

    async def test_한_글자는_다른_낱말_안에_들어_있어도_합치지_않는다(self):
        _, result = await interests(
            education=[education(1, "물"), education(2, "물감")],
        )
        assert sorted(keywords(result)) == ["물", "물감"]


class TestSearchTerm:
    @pytest.mark.parametrize(
        ("subject", "term"),
        [
            ("양치하기", "양치"),
            ("정리하기", "정리"),
            ("말하기", "말하기"),  # 떼면 한 글자라 그대로 둔다
            ("손 씻기", "손 씻기"),
            ("공룡", "공룡"),
            ("  인사   하기 ", "인사"),
        ],
    )
    def test_행동_이름의_하기를_떼고_두_글자_미만이면_그대로_둔다(self, subject, term):
        assert search_term(subject) == term


class TestExclusion:
    async def test_싫어한_교육_놀이_관심은_검색어가_되지_않는다(self):
        _, result = await interests(
            affinities=[("education", affinity(1, "곤충", polarity=-1))],
            education=[education(1, "퍼즐", polarity=-1)],
            activities=[activity(1, "물놀이", polarity=-1), activity(2, "공놀이")],
        )
        assert keywords(result) == ["공놀이"]

    async def test_6개월_지난_관심은_검색어가_되지_않는다(self):
        _, result = await interests(
            affinities=[("education", affinity(1, "공룡", days_ago=200))],
            education=[education(1, "숫자")],
        )
        assert keywords(result) == ["숫자"]

    async def test_14일_넘은_관찰은_읽지_않는다(self):
        _, result = await interests(education=[education(1, "공룡", days_ago=20)])
        assert result.error["code"] == "NO_RECORDS"

    async def test_습관_카테고리와_약_증상_행동은_루틴_주제가_되지_않는다(self):
        _, result = await interests(
            routine=[
                routine(1, "손톱 물어뜯기", "habit", polarity=-1),
                routine(2, "약 먹기", "self_care", polarity=-1),
                routine(3, "눈 깜빡임", "self_care"),
                routine(4, "손 씻기", "self_care", polarity=-1),
            ]
        )
        assert keywords(result) == ["손 씻기"]

    async def test_도움_수준만_적은_중립_생활_기록은_주제가_되지_않는다(self):
        _, result = await interests(
            routine=[
                routine(1, "양치하기", polarity=0),
                routine(2, "손 씻기", polarity=1),
            ]
        )
        assert result.error["code"] == "NO_RECORDS"

    async def test_힘들어하는_생활_행동도_주제가_된다(self):
        _, result = await interests(routine=[routine(1, "양치하기", polarity=-1)])
        assert keywords(result) == ["양치"]
        assert result.data["themes"][0]["evidence"][0]["polarity"] == -1

    async def test_12개월_전에는_교육_기록을_읽지_않는다(self):
        _, result = await interests(
            months=8,
            education=[education(1, "공룡")],
            activities=[activity(1, "까꿍놀이")],
        )
        assert keywords(result) == ["까꿍놀이"]


class TestResult:
    async def test_기록이_없으면_요청_낱말로_찾으라고_알린다(self):
        _, result = await interests()
        assert result.error["code"] == "NO_RECORDS"
        assert "요청" in result.error["message"]

    async def test_주제는_다섯_개까지고_주제마다_근거는_세_개까지다(self):
        _, result = await interests(
            education=[education(n, f"주제{n}") for n in range(1, 9)]
            + [education(20 + n, "주제1", days_ago=n) for n in range(1, 6)]
        )
        assert len(result.data["themes"]) == MAX_THEMES
        assert all(len(t["evidence"]) <= MAX_EVIDENCE_PER_THEME for t in result.data["themes"])

    async def test_돌려준_근거_id_만_이번_run_에서_인용할_수_있다(self):
        ctx, result = await interests(
            affinities=[("education", affinity(1, "공룡"))],
            education=[education(1, "숫자"), education(2, "블록")],
        )
        returned = {UUID(e["id"]) for theme in result.data["themes"] for e in theme["evidence"]}
        assert returned and returned == set(ctx.state.seen_evidence)

    @pytest.mark.parametrize("field", ["score", "keyword", "signals", "evidence"])
    async def test_주제마다_점수와_근거_출처가_있다(self, field):
        _, result = await interests(education=[education(1, "공룡")])
        assert field in result.data["themes"][0]

    async def test_점수는_높은_순서대로_내려간다(self):
        _, result = await interests(
            affinities=[("education", affinity(1, "공룡"))],
            education=[education(1, "숫자"), education(2, "블록", days_ago=9)],
        )
        scores = [theme["score"] for theme in result.data["themes"]]
        assert scores == sorted(scores, reverse=True)
