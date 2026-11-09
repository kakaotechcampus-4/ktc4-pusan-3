"""프롬프트 지문의 해시 규칙 (#770).

표준 라이브러리만 쓴다. 프롬프트를 모델에 보내는 곳은 agents 와 providers 둘이고(providers 는
agents 를 import 하지 못한다), 두 곳이 같은 규칙으로 지문을 만들어야 로그의 값과 평가 문서의
값이 같다. 그래서 규칙만 여기 두고, 어떤 입력을 지문에 넣는지는 각자(agents 는
`agents/common/prompt_fingerprint.py`) 정한다.

- 줄바꿈 `\\r\\n` · `\\r` 은 `\\n` 으로 통일한다 — Windows 에서 잰 값과 CI 값이 같아야 한다.
- 조각 사이에는 구분자를 넣는다 — `("ab", "c")` 와 `("a", "bc")` 가 같은 지문이 되지 않게.
- 모델 · reasoning_effort 는 입력에 넣지 않는다. 코드만 바뀐 커밋은 지문이 그대로다.
"""

import hashlib
import json
from typing import Any

SHORT_LENGTH = 12  # 평가 문서에 적는 앞자리 수
_SEPARATOR = "\n\x1e\n"  # ASCII record separator — 프롬프트 글에 나올 일이 없다


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_json(value: Any) -> str:
    """키 순서와 무관하고 한글을 이스케이프하지 않는 JSON 글. tool spec · 응답 스키마용."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(*parts: str) -> str:
    """조각들을 이어 붙인 글의 SHA-256 (16진수 64자)."""
    text = _SEPARATOR.join(_normalize(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(sha256: str) -> str:
    """문서 · 로그에 적는 앞 12자."""
    return sha256[:SHORT_LENGTH]
