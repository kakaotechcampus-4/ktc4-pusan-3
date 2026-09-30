"""아이 스코프 엔드포인트의 요청·응답 — 계약서 §06.

🚨 프론트 apps/web/src/lib/api/types.ts 의 CreateInputRequest · CreateInputResponse 와
   같은 모양이어야 한다.
"""

from typing import Annotated

from pydantic import BaseModel, StringConstraints


class CreateInputRequest(BaseModel):
    """POST /children/{cid}/inputs — 한 줄 입력."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    """보호자가 적은 한 줄. 공백뿐이면 400 — 빈 줄을 Agent 에 보내면 모델 호출 한 번이 낭비다."""

    source: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    """어디서 온 입력인가 — home_input · photo 등. 계약서가 값을 고정하지 않아 문자열로 받는다."""

    reply_to: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    """직전 run 에서 Memory가 물은 것에 대한 답이면 그 run_id. 없으면 일반 새 입력이다.

      값이 있는데 맥락을 못 찾으면 400 이다."""


class CreateInputResponse(BaseModel):
    """202 — run_id 만. 결과는 전부 GET /runs/{rid}/events 로 흐른다."""

    run_id: str
