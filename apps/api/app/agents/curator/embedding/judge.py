"""동일 대상 판정기 계약. 새 관찰의 subject 가 후보 Profile 중 어느 것과 같은 대상인지 묻는다.

판정기는 답만 한다 — 후보 하나 · none(같은 후보 없음) · uncertain(판단 불가).
그 답을 확인하고 연결하거나 새 Profile 을 만드는 것은 연결 단계(코드)다 (루트 CLAUDE.md §3).
이름이 같은 후보 · 후보 없음은 판정기를 부르지 않는다.

묻는 것은 "같은 대상인가" 하나다. "같은 선호의 근거인가"(딸기–딸기잼)는 묻지 않는다 —
Profile 은 대상 하나에 대한 선호라서, 근거가 주장을 그대로 뒷받침해야 한다.

실패는 LLMError 계열로 올린다. 연결 단계가 잡아 보류하고 다음 실행에서 다시 시도한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.agents.curator.embedding.ports import CuratorDomain

NONE = "none"
UNCERTAIN = "uncertain"

# 판단 기준 문장. 실험 3 · 4 에서 검증한 문장(v1)과 글자까지 같아야 한다
# (tests/unit/agents/curator/test_judge.py 가 확인). 바꾸면 실험을 다시 돌린다.
PROMPT = (
    "subject와 candidate가 동일한 대상의 다른 표현인지 판정하세요. "
    "같은 대상에 대한 반복 관찰로 집계할 수 있어야 합니다. "
    "단순 관련성, 상위/하위 범주, 원재료/가공품은 동일하지 않습니다. "
    "확실한 동의어, 오타, 띄어쓰기 차이는 같은 대상입니다. "
    "대상의 범위를 바꾸는 수식어를 임의로 무시하지 마세요. "
    "정보가 부족하거나 모호하면 uncertain을 선택하세요."
    " subject와 동일한 후보 하나를 선택하세요. 후보 중 동일한 대상이 없으면 none, "
    "정보가 부족하거나 모호하면 uncertain. 후보 순서나 단순 관련성으로 선택하지 마세요."
)


@dataclass(frozen=True)
class JudgeAnswer:
    """판정기가 돌려준 값 그대로. 후보 목록에 있는지는 연결 단계가 확인한다."""

    choice: str  # 후보 merge_key · none · uncertain
    model: str | None = None
    confidence: float | None = None  # 기록만 한다. 이 값으로 연결을 정하지 않는다


class IdentityJudge(Protocol):
    async def judge(
        self, *, subject: str, domain: CuratorDomain, candidates: Sequence[str]
    ) -> JudgeAnswer:
        """candidates 는 비어 있지 않고, subject 와 이름이 같은 후보는 없다 (연결 단계가 거른다)."""
        ...
