def test_앞에서_보여_준_추천은_다시_내지_말라고_붙인다() -> None:
    """ "다른 행사 추천해줘" — 앞 턴들의 추천 이름을 요청 뒤에 싣는다 (#852)."""
    from app.agents.activity.prompt import request_message

    assert request_message(["다른 행사 추천해줘"]) == "다른 행사 추천해줘"
    message = request_message(["다른 행사 추천해줘"], shown=["가상 국화축제 구경하기", " "])
    assert message.startswith("다른 행사 추천해줘\n\n")
    assert "다시 내지 않고" in message
    assert message.endswith("\n- 가상 국화축제 구경하기")


def test_구성은_요청을_먼저_따르고_막연할_때만_섞는다() -> None:
    """ "애랑 할만한 거" 에 3개 모두 확정 관심(야구 · 자동차) 집 놀이로 채우던 것 (11-09 운영).
    고정 배치가 아니라 요청 단서를 먼저 따르고, 단서가 없을 때만 섞는다 (#856 리뷰)."""
    from app.agents.activity.prompt import _SECTIONS

    text = "\n".join(_SECTIONS)
    assert "3개의 구성은 보호자의 이번 요청이 정한다" in text
    assert "새롭거나 색다른 것" in text
    assert "단서가 없는 막연한 요청" in text
    assert "확정 관심이 있으면 1개는 그 관심을 살린다" in text
    assert "관심을 상관없는 후보에 억지로 잇지 않는다" in text
    assert "2개까지" not in text  # 관심 상한은 뺐다 — 요청이 관심이면 여러 개여도 된다
    assert "search_events" not in text  # 행사 조회가 닫혀도 실리는 고정 구획이다
