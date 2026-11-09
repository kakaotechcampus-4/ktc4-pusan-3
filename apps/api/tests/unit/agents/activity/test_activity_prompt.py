def test_앞에서_보여_준_추천은_다시_내지_말라고_붙인다() -> None:
    """ "다른 행사 추천해줘" — 앞 턴들의 추천 이름을 요청 뒤에 싣는다 (#852)."""
    from app.agents.activity.prompt import request_message

    assert request_message(["다른 행사 추천해줘"]) == "다른 행사 추천해줘"
    message = request_message(["다른 행사 추천해줘"], shown=["가상 국화축제 구경하기", " "])
    assert message.startswith("다른 행사 추천해줘\n\n")
    assert "다시 내지 않고" in message
    assert message.endswith("\n- 가상 국화축제 구경하기")


def test_막연한_요청은_행사_장소_집_놀이를_섞고_관심은_두_개까지다() -> None:
    """ "애랑 할만한 거" 에 3개 모두 확정 관심(야구 · 자동차) 집 놀이로 채우던 것 (11-09 운영)."""
    from app.agents.activity.prompt import _SECTIONS

    text = "\n".join(_SECTIONS)
    assert "행사를 먼저 찾아 본다" in text
    assert "1개는 장소가 붙은 활동" in text
    assert "search_events" not in text  # 행사 조회가 닫혀도 실리는 고정 구획이다
    assert "확정 관심에 맞춘 후보는 3개 중 2개까지" in text
    assert "관심을 행사 · 장소에 억지로 잇지 않는다" in text
    assert "집에서 할 놀이 3개여도 된다" in text
