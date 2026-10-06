"""모델 tool 인자 스키마 검증.

- 모델은 후보를 `MAX_SUGGESTIONS`(3)개 낸다 — 2개 이하나 4개 이상이면 인자 검증에서
  거절된다. 보호자에게 1~2개가 가는 것은 안전 필터로 빠진 뒤의 일이다.
- 근거는 `EvidencePick(id, note)`. note 가 비면 보호자 화면에 빈 근거가 나가므로 거절한다.
- `materials` 는 비어도 받는다 — 필수로 만들면 모델이 재료를 지어낸다 (D6).
"""

import pytest
from pydantic import ValidationError

from app.agents.activity.registry import TOOL_SPECS
from app.agents.activity.schemas.common import DayLabel, EvidencePick
from app.agents.activity.schemas.outing import LookupWeatherArgs
from app.agents.activity.schemas.recommend import (
    ActivityCandidate,
    ProposeActivityCandidatesArgs,
)
from app.agents.activity.schemas.task import ActivityTaskType, task_type_of
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import MAX_SUGGESTIONS


def candidate(**kwargs) -> dict:
    base = dict(
        content="큰 블록으로 탑 쌓기",
        setting="indoor",
        materials=["큰 블록"],
        physical_intensity="low",
        involves_food=False,
        caregiver_role="together",
        why_this="블록을 오래 가지고 놀았어요",
        why_now="오늘 오후가 비어 있어요",
    )
    return {**base, **kwargs}


def propose(count: int) -> dict:
    return {"candidates": [candidate() for _ in range(count)]}


class TestCandidateCount:
    def test_개수는_공통_상수를_따른다(self):
        assert MAX_SUGGESTIONS == 3

    @pytest.mark.parametrize("count", [0, 1, 2, 4])
    def test_3개가_아니면_거절한다(self, count):
        with pytest.raises(ValidationError):
            ProposeActivityCandidatesArgs.model_validate(propose(count))

    def test_3개면_받는다(self):
        args = ProposeActivityCandidatesArgs.model_validate(propose(3))
        assert len(args.candidates) == 3

    def test_모델에게_보내는_스펙에도_개수가_실린다(self):
        """검증 규칙과 모델 스펙이 같은 Pydantic 모델에서 나온다."""
        spec = next(s for s in TOOL_SPECS if s["function"]["name"] == "propose_activity_candidates")
        candidates = spec["function"]["parameters"]["properties"]["candidates"]
        assert candidates["minItems"] == candidates["maxItems"] == MAX_SUGGESTIONS
        assert candidates["items"]["additionalProperties"] is False


class TestCandidate:
    def test_재료가_비어도_받는다(self):
        """비면 문장 스캔이 그 자리를 메운다. 필수로 만들면 모델이 재료를 지어낸다."""
        payload = candidate()
        del payload["materials"]
        assert ActivityCandidate.model_validate(payload).materials == []

    def test_정의되지_않은_필드는_받지_않는다(self):
        """status · kind 같은 값을 모델이 끼워 넣지 못한다."""
        with pytest.raises(ValidationError):
            ActivityCandidate.model_validate(candidate(kind="personalized"))

    def test_근거는_비어도_받는다(self):
        """아이 기록이 0행이면 코드가 일반 추천으로 분류한다. 모델이 막을 일이 아니다."""
        assert ActivityCandidate.model_validate(candidate()).evidence == []


class TestEvidencePick:
    def test_note_가_비면_거절한다(self):
        with pytest.raises(ValidationError):
            EvidencePick.model_validate({"id": "x", "note": ""})

    def test_출처_칸은_모델이_채우지_않는다(self):
        """source_kind · polarity · label 은 출력 tool 이 조회 결과에서 채운다 (D6)."""
        with pytest.raises(ValidationError):
            EvidencePick.model_validate(
                {"id": "x", "note": "모래놀이를 오래 했어요", "source_kind": "observation_activity"}
            )


class TestDayLabel:
    def test_날짜를_안_고르면_오늘이다(self):
        assert LookupWeatherArgs.model_validate({}).day == DayLabel.TODAY

    def test_날짜_문자열은_받지_않는다(self):
        """모델은 날짜를 계산하지 않는다. 라벨만 고른다."""
        with pytest.raises(ValidationError):
            LookupWeatherArgs.model_validate({"day": "2026-09-28"})


class TestTaskType:
    def task(self, **kwargs) -> DomainTask:
        base = dict(
            run_id="run-1", agent="activity", task_type=None, request_texts=("뭐 하고 놀까",)
        )
        return DomainTask(**{**base, **kwargs})

    def test_라벨이_없으면_놀이_추천이다(self):
        """Supervisor 는 Activity 에 라벨을 주지 않는다 (D1)."""
        assert task_type_of(self.task()) == ActivityTaskType.ACTIVITY_RECOMMENDATION

    def test_다른_Agent_의_task_는_받지_않는다(self):
        with pytest.raises(ValueError):
            task_type_of(self.task(agent="food", task_type="meal_recommendation"))

    def test_모르는_라벨은_받지_않는다(self):
        with pytest.raises(ValueError):
            task_type_of(self.task(task_type="outing_booking"))
