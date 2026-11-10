"""한 줄 입력의 글자 수 상한 — 서버 스키마가 Agent 가 감당하는 길이에서 자른다."""

import pytest
from pydantic import ValidationError

from app.api.v1.schemas.children import INPUT_TEXT_MAX_LENGTH, CreateInputRequest


def _body(text: str) -> dict[str, str]:
    return {"text": text, "source": "home_input"}


def test_상한은_500자다() -> None:
    # 값을 바꾸면 웹 INPUT_TEXT_MAX_LENGTH 와 근거 문서(docs/web/chat-run-v1.md)를 같이 고친다
    assert INPUT_TEXT_MAX_LENGTH == 500


def test_상한까지는_받는다() -> None:
    assert len(CreateInputRequest.model_validate(_body("가" * 500)).text) == 500


def test_상한을_넘으면_거절한다() -> None:
    with pytest.raises(ValidationError):
        CreateInputRequest.model_validate(_body("가" * 501))


def test_앞뒤_공백은_세지_않는다() -> None:
    body = _body("  " + "가" * 500 + "\n")
    assert len(CreateInputRequest.model_validate(body).text) == 500
