"""프롬프트 지문의 해시 규칙 — 줄바꿈이 달라도, 키 순서가 달라도 같은 값이어야 한다."""

import re

from app.core.prompt_hash import canonical_json, fingerprint, short


def test_같은_입력은_같은_지문이다():
    assert fingerprint("가", "나") == fingerprint("가", "나")


def test_지문은_sha256_16진수_64자다():
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint("가"))


def test_줄바꿈이_CRLF_여도_같은_지문이다():
    assert fingerprint("가\n나\n") == fingerprint("가\r\n나\r\n")
    assert fingerprint("가\n나") == fingerprint("가\r나")


def test_한_글자가_바뀌면_지문이_바뀐다():
    assert fingerprint("프롬프트") != fingerprint("프롬프투")


def test_조각_경계가_다르면_지문이_다르다():
    # ("ab", "c") 와 ("a", "bc") 가 같은 글이 되면 tool spec 이 프롬프트 끝에 붙어도 못 가른다
    assert fingerprint("ab", "c") != fingerprint("a", "bc")


def test_조각_순서가_바뀌면_지문이_바뀐다():
    assert fingerprint("가", "나") != fingerprint("나", "가")


def test_canonical_json_은_키_순서와_무관하다():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})


def test_canonical_json_은_한글을_이스케이프하지_않는다():
    assert "한글" in canonical_json({"a": "한글"})


def test_short_는_앞_12자다():
    assert short("0123456789abcdef" * 4) == "0123456789ab"
