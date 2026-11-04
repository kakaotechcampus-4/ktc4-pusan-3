"""삭제 제안 tool 의 인자 (#535).

모델은 지울 것을 고르기만 한다. 후보(종류별 마지막 조회 결과)와 탭은 코드가 정한다
(`tools/proposal.py`). 보호자가 화면 카드에서 체크를 고치고 버튼을 눌러야 서버가 지운다.

고른 것 = (`ids` ∪ `all_kinds` 종류의 조회 결과 전부) − `except_ids`.
"전부" 를 id 로 다 적게 하면 한 응답 출력(1400토큰)을 넘는다 — id 하나가 20토큰 남짓이라 다섯 종류
200건이면 4000토큰을 넘는다. 그래서 "그 종류 전부" 와 "그중 빼기" 를 따로 받는다.
"""

from typing import Annotated, Literal

from pydantic import Field

from app.agents.memory.schemas.common import RecordId, ToolArgs

DeleteKind = Literal[
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "event",
]


class ProposeDeleteArgs(ToolArgs):
    ids: Annotated[
        list[RecordId],
        Field(
            default_factory=list,
            description=(
                "하나씩 고를 때. 이 run 의 조회 결과 중 지워 달라고 한 것의 id. "
                "한 종류를 전부 지울 거면 id 를 늘어놓지 말고 all_kinds 를 쓴다"
            ),
        ),
    ]
    all_kinds: Annotated[
        list[DeleteKind],
        Field(
            default_factory=list,
            description=(
                '이 종류의 조회 결과를 전부 고른다. "저번주 기록 다 지워줘" 처럼 기간 · 종류의 '
                "전부를 지울 때. 조회 결과의 resource 값이다"
            ),
        ),
    ]
    except_ids: Annotated[
        list[RecordId],
        Field(
            default_factory=list,
            description=(
                'all_kinds 로 고른 것 중 빼 달라고 한 것("간식은 빼고")의 id. '
                "빼는 것도 카드에 체크 없이 보인다"
            ),
        ),
    ]
