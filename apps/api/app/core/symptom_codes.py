"""증상 표준 코드 집합 — 제출 API 가 받은 `symptom_codes` 를 사전 안의 코드로 거를 때 쓴다.

복약 초안의 `schedule.symptom_codes` 는 화면을 한 바퀴 돌아 제출 요청으로 되돌아온다. 제출 API 는
받은 값을 저장하므로, 사전 밖의 값이 DB 에 들어가지 않게 요청 모델에서 한 번 더 거른다(일정
제출이 `event_type` · `category` 를 거르는 것과 같다). 코드를 사전에 맞추는 일은 Agent 가
하고 여기서는 모르는 코드인지만 확인한다.

- `agents` 를 import 하지 않는다. 같은 `reference/symptom_codes.yaml` 의 `code` 만 따로 읽고, Health
  의 사전 로더와 같은 값인지는 테스트(`tests/unit/core/test_symptom_codes.py`)가 본다.
- 초안 계약(`ScheduleDraft`)은 이 검사를 하지 않는다. 사전에서 빠진 옛 코드를 가진 코스의
  수정 초안이 만들어져야 해서, 거르는 일은 제출 요청 모델이 이 함수를 쓰는 것으로 한다.
- 수정 제출이면 DB 에 이미 저장된 코드는 통과시키고 새로 들어온 코드만 거른다.
  화면은 이 칸을 고칠 수 없어서, 사전에서 빠진 옛 코드("retired_code")를 그대로 거르면 보호자가
  그 코스를 계속 고치지 못한다.

```python
class SubmitMedicationBody(BaseModel):          # 생성 제출
    symptom_codes: list[str] = []

    _codes = field_validator("symptom_codes")(validate_symptom_codes)


# 수정 제출 — 저장된 값은 통과, 새로 들어온 것만
validate_symptom_codes([c for c in body.symptom_codes if c not in row.symptom_codes])
```

이 이중 점검을 다른 도메인에 넓히는 기준은 "Agent 가 만든 값이 화면을 거쳐 제출 본문으로
되돌아오는가" 다. Food · Activity 추천은 서버가 바로 저장하고 화면은 id 만 보내서 해당하지
않고, 일정 초안은 `event_type` · `category` 를 Literal 로 이미 거른다. 같은 구조가 새로 생기면
그때 정한다.
"""

from collections.abc import Sequence
from functools import cache
from pathlib import Path

import yaml

_DICTIONARY = Path(__file__).resolve().parents[2] / "reference" / "symptom_codes.yaml"  # apps/api


@cache
def symptom_code_set() -> frozenset[str]:
    """증상 사전(`reference/symptom_codes.yaml`)의 코드 전부."""
    with _DICTIONARY.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return frozenset(str(term["code"]) for term in data["terms"])


def validate_symptom_codes(codes: Sequence[str]) -> list[str]:
    """사전 밖의 코드가 있으면 `ValueError`. pydantic validator 로 쓰면 422 가 된다."""
    unknown = [code for code in codes if code not in symptom_code_set()]
    if unknown:
        raise ValueError(f"사전에 없는 증상 코드: {', '.join(unknown)}")
    return list(codes)
