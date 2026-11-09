"""공지 글자 정리. 파일에서 꺼낸 글자와 모델이 낸 글자가 DB 에 닿기 전에 지나는 한 곳이다.

Postgres text 는 NUL(0x00)을 거절하고, 드라이버는 짝 없는 서로게이트를 인코딩하지 못한다. 둘 다
저장을 500 으로 터뜨린다. 말뭉치에는 없었지만 HWP 제어 문자와 모델 응답으로 들어올 수 있다.
"""

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff\ufeff\u200b]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    cleaned = _SURROGATE.sub("", _CONTROL.sub("", text)).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
