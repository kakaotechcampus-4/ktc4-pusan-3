"""증상 코드 집합 — 제출 API 가 받은 `symptom_codes` 를 거를 때 쓴다.

`app/core` 는 `agents` 를 import 하지 않는다. 그래서 같은 `reference/symptom_codes.yaml` 의 코드만
따로 읽고, Health 의 사전 로더와 같은 값인지는 이 테스트가 본다.

    - 코드 집합은 Health 사전의 코드와 같다
    - 사전 안의 코드는 통과하고, 밖의 코드는 `ValueError`(→ 제출 요청 모델에서 422)
    - 초안 계약(`ScheduleDraft`)은 사전 밖 코드를 거절하지 않는다 — 사전에서 빠진 옛 코드를 가진
      코스의 수정 초안(`before`)이 만들어져야 해서, 거르는 일은 제출 요청 모델의 몫이다
"""

import pytest
from pydantic import BaseModel, ValidationError, field_validator

from app.agents.health.reference import symptom_codes
from app.core.medication_draft import ScheduleDraft
from app.core.symptom_codes import symptom_code_set, validate_symptom_codes


def test_코드_집합은_Health_사전과_같다():
    assert symptom_code_set() == frozenset(term.key for term in symptom_codes().terms)
    assert "fever" in symptom_code_set()


def test_사전_안의_코드는_통과하고_순서를_지킨다():
    assert validate_symptom_codes(["cough", "fever"]) == ["cough", "fever"]


def test_빈_목록은_통과():
    assert validate_symptom_codes([]) == []


@pytest.mark.parametrize("codes", [["foo"], ["fever", "emergency_x"], ["FEVER"], [" fever"]])
def test_사전_밖의_코드는_거절(codes):
    with pytest.raises(ValueError):
        validate_symptom_codes(codes)


def test_거절_메시지에_모르는_코드가_담긴다():
    with pytest.raises(ValueError, match="emergency_x"):
        validate_symptom_codes(["fever", "emergency_x"])


def test_초안_계약은_사전_밖_옛_코드도_받는다():
    schedule = ScheduleDraft.model_validate(
        {
            "title": "항생제",
            "dosage": None,
            "indication_text": None,
            "storage": None,
            "med_form": None,
            "note": None,
            "symptom_codes": ["legacy_code"],
            "starts_on": None,
            "ends_on": None,
        }
    )

    assert schedule.symptom_codes == ["legacy_code"]


def test_제출_요청_모델의_validator_로_쓰면_사전_밖_코드가_거절된다():
    # 모듈 docstring 의 사용 예 — BE 의 제출 요청 모델이 이렇게 쓴다
    class SubmitBody(BaseModel):
        symptom_codes: list[str] = []

        _codes = field_validator("symptom_codes")(validate_symptom_codes)

    assert SubmitBody(symptom_codes=["fever"]).symptom_codes == ["fever"]
    with pytest.raises(ValidationError, match="emergency_x"):
        SubmitBody(symptom_codes=["fever", "emergency_x"])
