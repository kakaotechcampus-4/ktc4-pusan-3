"""보호자가 이번 요청에서 정한 말이 모델이 적은 조건에 있는가 — Activity · Food 가 같이 쓰는 규칙.

모델이 막연한 요청에 조건을 지어 넣으면 근거 없는 일반 추천이 통과한다(#597 리뷰). 핵심 규칙은
공통이고 일반 낱말 · 접미사만 Agent 마다 넘긴다.
"""

from app.agents.common.request_words import request_stems, said_in_request

GENERIC = frozenset("음식 반찬 추천 들어간 저녁".split())


def said(requested: str | None, *texts: str, suffix: str = "") -> bool:
    return said_in_request(requested, texts, generic=GENERIC, suffix=suffix)


def test_보호자_말에_있으면_인정한다() -> None:
    assert said("파프리카", "파프리카 들어간 반찬 추천해줘")


def test_일반_낱말만_남으면_막연한_요청이다() -> None:
    assert not said("반찬", "반찬 추천해줘")
    assert not said("음식 추천", "음식 추천해줘")


def test_일반_낱말은_빼고_남은_낱말을_본다() -> None:
    assert said("파프리카 들어간 반찬", "파프리카 들어간 반찬 추천해줘")


def test_남은_낱말_모두_보호자_말에_있어야_한다() -> None:
    assert not said("파프리카 오이", "파프리카 들어간 반찬 추천해줘")


def test_조사가_붙은_일반_낱말도_일반으로_본다() -> None:
    assert not said("저녁에", "저녁에 뭐 먹지")


def test_두_글자_이상은_말_안에_들어_있으면_된다() -> None:
    assert said("태국", "태국음식 추천해줘")


def test_한_글자는_낱말이_같아야_한다() -> None:
    assert said("무", "무 들어간 반찬")
    assert not said("무", "무릎 아프니까 반찬")


def test_모델이_지어낸_조건은_인정하지_않는다() -> None:
    assert not said("파프리카", "저녁 뭐 먹일까?")


def test_비어_있으면_인정하지_않는다() -> None:
    assert not said(None, "파프리카 반찬")
    assert not said("", "파프리카 반찬")


def test_접미사를_떼고_본다() -> None:
    assert said("공놀이", "공 가지고 놀 거 추천", suffix="놀이")
    assert not said("놀이", "놀이 추천", suffix="놀이")


def test_조건_낱말은_일반_낱말과_조사를_뺀_줄기다() -> None:
    assert request_stems("파프리카 들어간 반찬", generic=GENERIC) == ["파프리카"]
    assert request_stems("태국음식", generic=GENERIC) == ["태국음식"]
    assert request_stems("반찬", generic=GENERIC) == []
