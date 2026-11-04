"""삭제 제안 tool 의 인자 (#535).

모델은 지울 id 를 고르기만 한다. 후보(종류별 마지막 조회 결과)와 탭은 코드가 정한다
(`tools/proposal.py`). 보호자가 화면 카드에서 체크를 고치고 버튼을 눌러야 서버가 지운다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.memory.schemas.common import RecordId, ToolArgs


class ProposeDeleteArgs(ToolArgs):
    ids: Annotated[
        list[RecordId],
        Field(
            default_factory=list,
            description=(
                "이 run 의 조회 결과 중 보호자가 지워 달라고 한 것의 id. "
                "빼 달라고 한 것은 넣지 않는다. "
                "조회 결과에 있던 나머지도 카드에 체크 없이 함께 보인다"
            ),
        ),
    ]
