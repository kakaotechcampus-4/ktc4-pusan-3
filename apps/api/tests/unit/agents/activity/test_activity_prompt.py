def test_앞에서_보여_준_추천은_다시_내지_말라고_붙인다() -> None:
    """ "다른 행사 추천해줘" — 앞 턴들의 추천 이름을 요청 뒤에 싣는다 (#852)."""
    from app.agents.activity.prompt import request_message

    assert request_message(["다른 행사 추천해줘"]) == "다른 행사 추천해줘"
    message = request_message(["다른 행사 추천해줘"], shown=["가상 국화축제 구경하기", " "])
    assert message.startswith("다른 행사 추천해줘\n\n")
    assert "다시 내지 않고" in message
    assert message.endswith("\n- 가상 국화축제 구경하기")
