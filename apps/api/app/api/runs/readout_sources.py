"""readout 의 문서 근거 → 화면 참고 줄 (#574).

일반 정보 답변(`doc_answer`)만 싣는다. SSE(`translate.to_sse`)와 지난 대화
(`chat_record.to_reply`)가 같은 함수를 쓴다 — 실시간 화면과
다시 그린 화면의 참고 줄이 같아야 한다. 문서 행(`activity_doc` · `food_doc` · `growth_doc`)만
바꾸고, 아이 기록 같은 다른 근거는 내보내지 않는다. 시드에 없는(검수가 풀린) 행은 빼고 경고를
남긴다.
"""

import logging
from typing import get_args

from app.agents.entrypoint import Readout, describe_docs
from app.api.chat_replies import DocSourceKind, StoredDocRef, StoredDocSource

logger = logging.getLogger(__name__)

DOC_SOURCE_KINDS: frozenset[str] = frozenset(get_args(DocSourceKind))


# 참고 줄을 싣는 readout 종류. 문서 행을 `source_refs` 에 두고도 화면에 싣지 않는 readout
# (Growth 리듬 안내 등)이 있어서, 근거를 단 답변(`doc_answer`)만 연다
SOURCED_KINDS: frozenset[str] = frozenset({"doc_answer"})


def doc_sources(readout: Readout) -> list[StoredDocSource]:
    if readout.kind not in SOURCED_KINDS:
        return []
    refs = [(ref.kind, ref.id) for ref in readout.source_refs if ref.kind in DOC_SOURCE_KINDS]
    if not refs:
        return []
    cards = describe_docs(refs)
    sources: list[StoredDocSource] = []
    for kind, key in dict.fromkeys(refs):
        card = cards.get((kind, key))
        if card is None:
            # id 만 남긴다 — 문서 행은 아이 정보가 아니지만 본문은 싣지 않는다
            logger.warning("문서 출처를 찾지 못함 kind=%s id=%s", kind, key)
            continue
        sources.append(
            StoredDocSource(
                ref=StoredDocRef(kind=kind, id=str(key)),
                title=card.title,
                source_org=card.source_org,
                source_title=card.source_title,
                source_url=card.source_url,
            )
        )
    return sources
