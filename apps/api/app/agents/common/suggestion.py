"""추천 한 건. `suggestion` 테이블에 `draft` 로 저장된다.

Agent 는 값만 만들고 INSERT 는 주입된 writer 가 한다. `status` 와 `expires_at` 은
tool 인자에 두지 않는다 — 모델이 만료나 승인 상태를 정할 수 없다.
"""

from dataclasses import dataclass
from typing import Any, Literal

from app.agents.common.evidence import RankedEvidence
from app.agents.common.refs import Ref, count_child_records

SuggestionKind = Literal["general", "personalized"]

# 요청 1건당 정확히 이 개수. 2개 이하나 4개 이상이면 출력 tool 이 거절한다.
# 안전 필터 뒤에 모자라면 재호출 1회, 그래도 못 채우면 남은 만큼만 낸다 (유일한 예외).
REQUIRED_COUNT = 3


class SuggestionRejected(ValueError):
    """출력 tool 이 후보를 거절했다. 모델에게 사유와 함께 돌려준다."""


@dataclass(frozen=True)
class SuggestionDraft:
    agent: str  # food · activity · growth · health
    kind: SuggestionKind
    content: str
    reason: str
    source_refs: tuple[Ref, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "kind": self.kind,
            "content": self.content,
            "reason": self.reason,
            "source_refs": [ref.to_payload() for ref in self.source_refs],
        }


def build(
    *,
    agent: str,
    content: str,
    reason: str,
    evidence: tuple[RankedEvidence, ...] = (),
    general_reason: str | None = None,
) -> SuggestionDraft:
    """근거 유무로 `kind` 를 정하고 값을 검증한다.

    근거 0행은 정상 경로다. 그때는 `kind="general"` 이 되고 `reason` 을 코드 템플릿으로
    **덮어쓴다** — 근거가 없는데 모델이 쓴 개인화 문장이 나가면 안 된다.

    기피 근거를 인용했으면 `reason` 에 무엇을 피했는지가 있어야 한다. 인용만 하고 말하지
    않으면 보호자에게는 근거 없는 추천과 같다.
    """
    refs = tuple(item.ref for item in evidence)
    if count_child_records(refs) == 0:
        if general_reason is None:
            raise SuggestionRejected(
                f"{agent}: 근거가 0행인데 일반 추천 문구(general_reason)가 없다"
            )
        return SuggestionDraft(agent=agent, kind="general", content=content, reason=general_reason)

    if not reason.strip():
        raise SuggestionRejected(f"{agent}: 개인화 추천인데 이유가 비었다")
    if any(item.is_avoidance for item in evidence) and not _mentions_avoidance(reason, evidence):
        raise SuggestionRejected(f"{agent}: 기피 근거를 인용했는데 무엇을 피했는지가 이유에 없다")
    return SuggestionDraft(
        agent=agent, kind="personalized", content=content, reason=reason, source_refs=refs
    )


def check_count(drafts: tuple[SuggestionDraft, ...], *, after_retry: bool = False) -> None:
    """개수 검사. 재호출까지 하고도 모자란 경우만 예외다."""
    if len(drafts) == REQUIRED_COUNT:
        return
    if after_retry and 0 < len(drafts) < REQUIRED_COUNT:
        return  # 풀이 고갈됐다. 남은 만큼만 낸다
    raise SuggestionRejected(f"추천은 정확히 {REQUIRED_COUNT}개여야 한다. 받은 것: {len(drafts)}개")


def _mentions_avoidance(reason: str, evidence: tuple[RankedEvidence, ...]) -> bool:
    """기피한 대상의 라벨이 이유 문장에 들어 있는가.

    문장 품질까지는 못 본다. 라벨조차 안 나오면 확실히 말하지 않은 것이라는 하한만 건다.
    """
    return any(item.label and item.label in reason for item in evidence if item.is_avoidance)
