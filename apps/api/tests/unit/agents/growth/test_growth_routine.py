"""`routine_coaching` 의 코드 쪽 검증.

- 카테고리 허용표 — 예절은 24개월부터, 습관 교정은 36개월부터다 (경계 양쪽).
- 루틴 근거 — **선언한 카테고리와 같은 관찰이 최근 14일 안**에 있어야 센다. 없으면 되묻는다.
- 의료 처치 · 증상처럼 보이는 습관은 목록으로 닫는다. 오탐 guard 가 흔한 말을 살린다.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.growth.gating import routine_mode
from app.agents.growth.store.ports import RoutineObservation
from app.agents.growth.tools.closures import (
    closure_key,
    closure_terms,
    parse_closure_terms,
)
from app.agents.growth.tools.routine import (
    ask_question,
    check_routine_category,
    current_assistance_level,
    routine_evidence,
    routine_gap,
)
from tests.unit.agents.growth.support import CHILD, TODAY, doc_inputs


def obs(
    n: int,
    days_ago: int,
    category: str = "self_care",
    *,
    subject: str = "양치하기",
    assistance: str | None = "partial_assist",
    trigger: str | None = None,
) -> RoutineObservation:
    return RoutineObservation(
        id=UUID(int=2000 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        routine_category=category,
        polarity=0,
        assistance_level=assistance,
        trigger=trigger,
    )


class TestCategory:
    @pytest.mark.parametrize(
        ("category", "months", "expected"),
        [
            ("habit", 35, "closed.habit_under36"),
            ("habit", 36, None),
            ("habit", 12, "closed.habit_under36"),
            ("social_manner", 23, "closed.manner_under24"),
            ("social_manner", 24, None),
            ("self_care", 12, None),
            ("mealtime", 12, None),
            ("household_task", 12, None),
            ("transition", 12, None),
            ("self_care", 71, None),
        ],
    )
    def test_연령_허용표(self, category, months, expected):
        assert check_routine_category(category, months) == expected

    @pytest.mark.parametrize(
        ("months", "category", "expected"),
        [
            (0, "self_care", "rhythm_info"),
            (8, "habit", "rhythm_info"),
            (11, "mealtime", "rhythm_info"),
            (12, "self_care", "next_step"),
            (35, "transition", "next_step"),
            (36, "habit", "habit_fix"),
            (40, "self_care", "next_step"),
            (30, None, "next_step"),
        ],
    )
    def test_모드는_월령과_카테고리로_정한다(self, months, category, expected):
        assert routine_mode(months, category) == expected


class TestEvidence:
    def test_14일_전_관찰은_근거이고_15일_전은_아니다(self):
        edge, stale = obs(1, 14), obs(2, 15)
        assert routine_evidence([edge, stale], category="self_care", today=TODAY) == (edge,)

    def test_다른_카테고리_관찰은_근거가_아니다(self):
        other = obs(1, 1, "mealtime")
        assert routine_evidence([other], category="self_care", today=TODAY) == ()

    def test_근거가_있으면_되묻지_않는다(self):
        assert routine_gap([obs(1, 3)], category="self_care", today=TODAY) is None

    @pytest.mark.parametrize(
        "cited",
        [[], [obs(1, 15)], [obs(1, 2, "mealtime")]],
        ids=["관찰 없음", "15일 전 것뿐", "다른 카테고리뿐"],
    )
    def test_근거가_없으면_일반_추천_대신_되묻는다(self, cited):
        assert routine_gap(cited, category="self_care", today=TODAY) == "ask.routine_current"

    def test_습관은_근거가_없으면_지금도_하는지_묻는다(self):
        assert routine_gap([], category="habit", today=TODAY) == "ask.habit_current"
        assert routine_gap([obs(1, 20, "habit")], category="habit", today=TODAY) == (
            "ask.habit_current"
        )

    def test_습관은_trigger_가_없으면_언제_하는지_묻는다(self):
        cited = [obs(1, 3, "habit", subject="손톱 물어뜯기", trigger=None)]
        assert routine_gap(cited, category="habit", today=TODAY) == "ask.habit_trigger"

    def test_빈_문자열_trigger_도_없는_것이다(self):
        cited = [obs(1, 3, "habit", trigger="  ")]
        assert routine_gap(cited, category="habit", today=TODAY) == "ask.habit_trigger"

    def test_trigger_가_하나라도_있으면_교정을_시작한다(self):
        cited = [obs(1, 3, "habit", trigger=None), obs(2, 4, "habit", trigger="긴장할 때")]
        assert routine_gap(cited, category="habit", today=TODAY) is None

    def test_질문은_한_번에_하나다(self):
        """어느 경우에도 키 하나만 돌려준다 — 습관 근거 0 이면 current 가 trigger 보다 먼저다."""
        assert isinstance(routine_gap([], category="habit", today=TODAY), str)

    def test_되묻기_한_줄은_코드가_만든_행동_이름으로_채운다(self):
        assert ask_question("ask.habit_current", "손톱 물어뜯기") == "요즘도 손톱 물어뜯기 하나요?"
        assert ask_question("ask.habit_trigger", "손톱 물어뜯기").startswith("손톱 물어뜯기")


class TestAssistanceLevel:
    def level(self, observations, **kwargs):
        defaults = dict(category="self_care", subject=None, today=TODAY)
        return current_assistance_level(observations, **{**defaults, **kwargs})

    def test_창_안에서_가장_최근_수준을_고른다(self):
        rows = [obs(1, 10, assistance="full_assist"), obs(2, 2, assistance="verbal_prompt")]
        assert self.level(rows) == "verbal_prompt"

    def test_창_밖_관찰은_보지_않는다(self):
        assert self.level([obs(1, 15, assistance="independent")]) is None

    def test_수준이_비어_있으면_추정하지_않는다(self):
        assert self.level([obs(1, 2, assistance=None)]) is None

    def test_수준이_빈_관찰은_건너뛰고_그_앞의_수준을_쓴다(self):
        rows = [obs(1, 8, assistance="partial_assist"), obs(2, 1, assistance=None)]
        assert self.level(rows) == "partial_assist"

    def test_같은_날_둘이면_도움이_더_필요한_쪽이다(self):
        rows = [obs(1, 2, assistance="independent"), obs(2, 2, assistance="partial_assist")]
        assert self.level(rows) == "partial_assist"

    def test_행동_이름이_다르면_보지_않는다(self):
        rows = [obs(1, 1, subject="손 씻기", assistance="independent")]
        assert self.level(rows, subject="양치하기") is None
        assert self.level(rows, subject="손 씻기") == "independent"

    def test_행동_이름은_공백과_상관없이_맞춘다(self):
        rows = [obs(1, 1, subject="손 씻기", assistance="independent")]
        assert self.level(rows, subject="손씻기") == "independent"

    def test_다른_카테고리는_보지_않는다(self):
        assert self.level([obs(1, 1, "mealtime", assistance="independent")]) is None


class TestClosures:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [("T41", "closed.medical_routine"), ("T42", "closed.symptom_habit")],
    )
    def test_문서의_닫힘_입력은_닫힌다(self, key, expected):
        assert closure_key([doc_inputs()[key]]) == expected

    def test_다른_입력은_하나도_닫히지_않는다(self):
        """T01 ~ T44 중 T41 · T42 만 걸려야 한다. 습관 교정(T18 ~ T20)은 그대로 돈다."""
        hit = {key for key, text in doc_inputs().items() if closure_key([text])}
        assert hit == {"T41", "T42"}

    @pytest.mark.parametrize(
        "text",
        [
            "치약 짜는 걸 혼자 하게 하고 싶어",
            "약속을 안 지켜요",
            "약간 느리게 먹어요",
            "약속 지키기 연습을 시키고 싶어",
            "동생을 약 올려요",
            "예약해 둔 수영 수업에 가기 싫어해",
            "관장님 말씀을 듣고 인사를 안 해요",
            "깜빡 잊고 양치를 안 했어요",
            "손톱을 자꾸 물어뜯어",
            "손가락을 빨아요",
            "코를 자꾸 파요",
        ],
    )
    def test_흔한_말은_걸리지_않는다(self, text):
        assert closure_key([text]) is None

    @pytest.mark.parametrize(
        "text",
        [
            "약 먹는 걸 너무 싫어해",
            "시럽 약을 주스에 섞어도 돼?",
            "안약을 넣을 때 울어",
            "연고 바르는 걸 싫어해",
            "흡입기 쓰는 연습을 시키고 싶어",
            "혈당 재는 걸 무서워해",
            "해열제 먹이는 방법",
            "관장을 하려는데 울어요",
        ],
    )
    def test_의료_처치는_걸린다(self, text):
        assert closure_key([text]) == "closed.medical_routine"

    @pytest.mark.parametrize(
        "text",
        [
            "눈을 자꾸 깜빡여",
            "킁킁거리는 소리를 자꾸 내요",
            "자꾸 헛기침을 해",
            "머리를 박는 행동을 해요",
            "까치발로만 걸어요",
            "말을 더듬어요",
        ],
    )
    def test_증상처럼_보이는_행동은_걸린다(self, text):
        assert closure_key([text]) == "closed.symptom_habit"

    def test_둘_다_걸리면_의료_처치_문구가_먼저다(self):
        assert closure_key(["약 먹을 때 눈을 자꾸 깜빡여"]) == "closed.medical_routine"

    def test_문장은_하나씩_따로_대조한다(self):
        assert closure_key(["아침 준비", "약 먹는 게 싫대"]) == "closed.medical_routine"
        assert closure_key([]) is None

    def test_목록을_읽어_두_갈래를_만든다(self):
        terms = closure_terms()
        assert terms.medical and terms.symptom
        assert {t.key for t in terms.medical}.isdisjoint({t.key for t in terms.symptom})


class TestClosureLoader:
    GOOD = {
        "medical_routine": [{"label": "연고", "aliases": ["연고"], "guards": []}],
        "symptom_habit": [{"label": "헛기침", "aliases": ["헛기침"], "guards": []}],
    }

    def test_정상_목록은_읽힌다(self):
        assert parse_closure_terms(self.GOOD).medical[0].key == "연고"

    def test_목록이_빠지면_거부한다(self):
        with pytest.raises(ValueError, match="목록이 없다"):
            parse_closure_terms({"medical_routine": self.GOOD["medical_routine"]})

    def test_목록이_비면_거부한다(self):
        with pytest.raises(ValueError, match="비었다"):
            parse_closure_terms({**self.GOOD, "symptom_habit": []})

    def test_guard_없는_한_글자_별칭은_거부한다(self):
        bad = {**self.GOOD, "medical_routine": [{"label": "약", "aliases": ["약"], "guards": []}]}
        with pytest.raises(ValueError, match="guard"):
            parse_closure_terms(bad)

    def test_guard_가_있으면_한_글자_별칭을_허용한다(self):
        ok = {
            **self.GOOD,
            "medical_routine": [{"label": "약", "aliases": ["약"], "guards": ["치약"]}],
        }
        assert parse_closure_terms(ok).medical[0].aliases == ("약",)

    def test_label_이_겹치면_거부한다(self):
        dup = {
            "medical_routine": [{"label": "연고", "aliases": ["연고"]}],
            "symptom_habit": [{"label": "연고", "aliases": ["연고도"]}],
        }
        with pytest.raises(ValueError, match="겹친다"):
            parse_closure_terms(dup)

    def test_정해지지_않은_칸은_거부한다(self):
        bad = {**self.GOOD, "medical_routine": [{"label": "연고", "aliases": ["연고"], "level": 1}]}
        with pytest.raises(ValueError, match="정해지지 않은 칸"):
            parse_closure_terms(bad)
