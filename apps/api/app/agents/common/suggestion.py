"""추천 한 건. `suggestion` 테이블에 `draft` 로 저장된다.

Agent 는 값만 만들고 INSERT 는 주입된 writer 가 한다. `status` 와 `expires_at` 은
tool 인자에 두지 않는다 — 모델이 만료나 승인 상태를 정할 수 없다.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from app.agents.common.readout import Readout, ReadoutCatalog, ReadoutText, code_readout
from app.agents.common.refs import EvidenceCitation, count_child_records

SuggestionKind = Literal["general", "personalized"]

# 요청 1건당 최대 이 개수 (Tool_공통.md §5-2). 채울 수 있으면 다 채운다. 덜 내도 되는 것은
# 더 채울 길이 없을 때뿐이다 — 안전 필터 뒤 재호출 1회를 했거나, 후보 풀이 처음부터 모자라다.
MAX_SUGGESTIONS = 3

# 개수 안내. 다 채우지 못했을 때만 추천과 함께 나간다 — 문구는 yaml 에, 고르는 것은 count_notice
_NOTICES = ReadoutCatalog.from_yaml(Path(__file__).with_name("suggestion.readout.yaml"))
PARTIAL_NOTICE: ReadoutText = _NOTICES.get("suggestion.partial")
EMPTY_NOTICE: ReadoutText = _NOTICES.get("suggestion.empty")


class SuggestionRejected(ValueError):
    """출력 tool 이 후보를 거절했다. 모델에게 사유와 함께 돌려준다.

    `check_count` 의 거절 중 하나는 모델에게 돌려주지 않는다 (Tool_공통.md §5) — 안전 필터로
    모자라졌으면 사유 없이 재호출 1회를 한다. 더 채울 길이 없는데 0개인 것은 거절이 아니다 —
    `check_count` 가 통과시키고 추천 없이 `count_notice(0)` 안내로 끝난다.
    """


@dataclass(frozen=True)
class SuggestionDraft:
    agent: str  # food · activity · growth · health
    kind: SuggestionKind
    content: str
    reason: str
    citations: tuple[EvidenceCitation, ...] = ()
    # 그 추천에 들어 있거나 제품에 따라 들어 있을 수 있는 알레르기 항목의 정식 명칭.
    # 19종 밖은 이름으로 담는다. 승인할 때 안내에 쓴다
    allergens: tuple[str, ...] = ()
    # 일정으로 만들 때 준비물 하나씩
    items: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "kind": self.kind,
            "content": self.content,
            "reason": self.reason,
            "citations": [item.to_payload() for item in self.citations],
            "allergens": list(self.allergens),
            "items": list(self.items),
        }


def build(
    *,
    agent: str,
    content: str,
    reason: str,
    citations: tuple[EvidenceCitation, ...] = (),
    general_reason: str | None = None,
    allergens: tuple[str, ...] = (),
    items: tuple[str, ...] = (),
) -> SuggestionDraft:
    """아이 기록 근거의 행 수로 `kind` 를 정하고 값을 검증한다.

    아이 기록 0행은 정상 경로다. 그때는 `kind="general"` 이 되고 `reason` 을 코드 템플릿으로
    덮어쓴다. general 인데 인용이 0행인 것은 거절 사유가 아니라 품질 지표다.

    기피 근거를 인용했으면 `reason` 에 무엇을 피했는지가 있어야 한다. 인용만 하고 말하지
    않으면 보호자에게는 근거 없는 추천과 같다.
    """
    for item in citations:
        if not item.note.strip():
            raise SuggestionRejected(
                f"{agent}: {item.ref.kind} 를 인용했는데 무엇을 근거로 봤는지(note)가 비었다"
            )

    refs = tuple(item.ref for item in citations)
    if count_child_records(refs) == 0:
        if general_reason is None:
            raise SuggestionRejected(
                f"{agent}: 아이 기록 근거가 0행인데 일반 추천 문구(general_reason)가 없다"
            )
        return SuggestionDraft(
            agent=agent,
            kind="general",
            content=content,
            reason=general_reason,
            citations=citations,
            allergens=allergens,
            items=items,
        )

    if not reason.strip():
        raise SuggestionRejected(f"{agent}: 개인화 추천인데 이유가 비었다")
    if any(item.is_avoidance for item in citations) and not _mentions_avoidance(reason, citations):
        raise SuggestionRejected(f"{agent}: 기피 근거를 인용했는데 무엇을 피했는지가 이유에 없다")
    return SuggestionDraft(
        agent=agent,
        kind="personalized",
        content=content,
        reason=reason,
        citations=citations,
        allergens=allergens,
        items=items,
    )


def check_count(drafts: tuple[SuggestionDraft, ...], *, exhausted: bool = False) -> None:
    """개수 검사. 추천은 최대 `MAX_SUGGESTIONS` 개다 (Tool_공통.md §5-2).

    - 넘치면 거절한다.
    - 모자라면(0개 포함) `exhausted` 일 때만 통과한다. 더 채울 길이 없다는 뜻이다 — 안전 필터
      뒤 재호출 1회를 이미 했거나, 후보 풀이 처음부터 모자라 재호출해도 못 채운다.
      아니면 거절한다. 채울 수 있는데 덜 낸 것을 통과시키지 않는다 — 안전 필터로 빠졌으면
      호출부가 사유 없이 재호출 1회를 하고, 모델이 덜 냈으면 사유와 함께 모델에게 돌려준다.
    - 더 채울 길이 없는데 0개여도 예외를 던지지 않는다. 0개는 실패가 아니라 안전 조건을 통과한
      후보가 없다는 결과라, 호출부는 이어서 `count_notice(len(drafts))` 로 안내만 붙이면 된다.
      예외로 두면 호출부가 놓쳤을 때 pipeline 이 Agent 실패로 세고 화면에 다시 시도가 뜬다.
    """
    count = len(drafts)
    if count > MAX_SUGGESTIONS:
        raise SuggestionRejected(f"추천은 최대 {MAX_SUGGESTIONS}개다. 받은 것: {count}개")
    if count < MAX_SUGGESTIONS and not exhausted:
        raise SuggestionRejected(f"추천을 {MAX_SUGGESTIONS}개 채울 수 있는데 {count}개만 받았다")


def count_notice(count: int, *, empty: ReadoutText = EMPTY_NOTICE) -> Readout | None:
    """다 채우지 못한 추천에 붙일 안내 한 줄. 다 채웠으면 없다 (Tool_공통.md §5-2).

    `count` 는 `check_count` 를 거친 뒤 실제로 나가는 개수다. 0개는 실패가 아니라 안전 조건을
    통과한 후보가 없다는 뜻이라, 그 Agent 는 추천 없이 이 안내만 낸다. Food 처럼 0개 문구가
    따로 있는 Agent 는 `empty` 로 넘긴다.

    `code` 는 여기서 붙인다. 문구가 Agent 마다 달라도 0개면 `empty` 라서 화면이 문구를 읽지
    않고 가른다. 같은 `kind="notice"` 인 날씨 안내와 섞여 와도 개수 안내는 이 값으로 찾는다.
    """
    if not 0 <= count <= MAX_SUGGESTIONS:
        raise ValueError(f"추천 개수는 0~{MAX_SUGGESTIONS} 이다. 받은 것: {count}")
    if count == MAX_SUGGESTIONS:
        return None
    if count == 0:
        return replace(code_readout(empty), code="empty")
    return replace(code_readout(PARTIAL_NOTICE, count=count), code="fewer")


def _mentions_avoidance(reason: str, citations: tuple[EvidenceCitation, ...]) -> bool:
    """기피한 대상의 라벨이 이유 문장에 들어 있는가.

    문장 품질까지는 못 본다. 라벨조차 안 나오면 확실히 말하지 않은 것이라는 하한만 건다.
    """
    return any(item.label and item.label in reason for item in citations if item.is_avoidance)
