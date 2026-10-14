"""보호자 말이 "급식이 실제로 바뀌었다" 는 말인지 코드가 본다. 표는 `reference/daycare_intent.yaml`.

급식 갱신은 모델이 tool 을 꼭 부른다(`tool_choice="required"`) — 묻는 말이나 가정도 갱신 · 삭제로 올
수 있다. 삭제는 OCR 재적재 없이는 되돌릴 수 없어서 모델 판단에 맡기지 않는다(루트 CLAUDE.md §3).

- 묻는 말(`is_question`)이면 갱신 · 삭제 둘 다 아무것도 쓰지 않는다.
- 가정 · 짐작(`is_uncertain`)이면 삭제만 막는다. 갱신은 되돌릴 수 있고 "우유 나오면 두유로 바꿔줘"
  같은 부탁이 가정 꼴이다.
- 삭제는 빠지거나 끼니가 없었다는 말(`says_absence`)이 있어야 한다.
"""

import re
from dataclasses import dataclass
from functools import cache

from app.agents.common.reference import load_reference

SEED_FILE = "daycare_intent.yaml"

# 마지막 어절 뒤에 붙는 문장 부호 · 웃음 표시
_TRAILING = re.compile(r"[\s.!~…ㅋㅎ^]+$")
_PUNCT = re.compile(r"[.,!?~…]")


@dataclass(frozen=True)
class _Table:
    absence: tuple[str, ...]
    question_endings: tuple[str, ...]
    conditional_endings: tuple[str, ...]
    uncertain: tuple[str, ...]


@cache
def _table() -> _Table:
    data = load_reference(SEED_FILE)
    return _Table(
        absence=tuple(data["absence"]),
        question_endings=tuple(data["question_endings"]),
        conditional_endings=tuple(data["conditional_endings"]),
        uncertain=tuple(data["uncertain"]),
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _words(text: str) -> list[str]:
    return [word for word in _PUNCT.sub(" ", text).split() if word]


def is_question(text: str) -> bool:
    """묻는 말인가 — 물음표가 있거나 마지막 어절이 묻는 어미로 끝난다."""
    if "?" in text or "？" in text:
        return True
    words = _words(_TRAILING.sub("", text))
    return bool(words) and words[-1].endswith(_table().question_endings)


def is_uncertain(text: str) -> bool:
    """가정이나 짐작인가 — 어절이 조건 어미로 끝나거나 짐작하는 말이 있다."""
    table = _table()
    if any(word.endswith(table.conditional_endings) for word in _words(text)):
        return True
    compact = _compact(text)
    return any(phrase in compact for phrase in table.uncertain)


def says_absence(text: str) -> bool:
    """빠지거나 그 끼니가 없었다는 말이 있나. 없으면 삭제하지 않는다."""
    compact = _compact(text)
    return any(word in compact for word in _table().absence)
