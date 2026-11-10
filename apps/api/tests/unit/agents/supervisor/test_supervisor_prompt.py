"""Supervisor 프롬프트 검증. LLM 을 부르지 않는다 — 경계 예시가 프롬프트에 실렸는지만 본다."""

from app.agents.supervisor.prompt import build_system_prompt


def test_food_check_라벨과_경계_예시가_있다() -> None:
    prompt = build_system_prompt()
    assert "food로 보내는 건 다섯 가지다." in prompt
    assert '"땅콩소스 먹여도 될까?" → request/food/food_check' in prompt
    assert '"해열제 더 먹여도 돼?" → guarded/diagnosis' in prompt


def test_급식_조회와_갱신의_경계_예시가_있다() -> None:
    prompt = build_system_prompt()
    assert "daycare_lookup" in prompt
    assert '"내일 급식 메뉴 좀 바꿔줘" → request/food/daycare_meal' in prompt
    assert '"내일 어린이집 점심 뭐 나와?" → request/food/daycare_lookup' in prompt
    # 기관 신호 없는 기록 보기는 급식 조회가 아니다
    assert '"지난주에 먹은 것 좀 보여줘." → record/lookup_edit' in prompt


def test_선생님_알림장이_전한_급식_먹은_양은_급식_갱신이다() -> None:
    """선생님 · 알림장이 전한 점심 · 간식의 먹은 양은 급식 행의 양을 고친다 (#798).
    기관 신호가 어린이집 · 유치원 · 급식뿐이면 record 로 가서 Memory 가 메뉴를 되묻는다."""
    prompt = build_system_prompt()
    assert "선생님 · 알림장이 전한 점심 · 간식" in prompt
    assert '"선생님이 점심은 반만 먹었대" → request/food/daycare_meal' in prompt
    assert '"알림장에 간식 싹 비웠다고 써 있네" → request/food/daycare_meal' in prompt
    # 기관 끼니가 아닌 집 식사는 그대로 기록이다
    assert '"아침에 밥 반 공기 먹고 등원했어" → record/observe' in prompt


def test_기관이_전해도_무엇을_먹었는지는_기록이다() -> None:
    """양만 전한 말만 급식 갱신이다. "딸기는 잘 먹었대" 처럼 무엇을 먹었는지 말하면 아이의
    관찰이라 record 로 가서 선호 근거로 쌓인다 — 급식 갱신은 관찰을 남기지 않는다 (#841 리뷰)."""
    prompt = build_system_prompt()
    assert '양만 전한 말("반만 · 싹 비웠다")만이다' in prompt
    assert '"어린이집에서 계란말이 엄청 많이 먹었대" → record/observe' in prompt
    assert '"어린이집에서 계란말이 엄청 많이 먹었대" → request/food/daycare_meal' not in prompt


def test_무엇을_먹일지_없이_시점만_묻는_말은_food_가_아니다() -> None:
    prompt = build_system_prompt()
    assert '"지금 또 먹여도 돼?" → request/health' in prompt


def test_앞_대화_절이_다시_나눌_때_절_뒤에_있다() -> None:
    prompt = build_system_prompt(with_context=True)

    assert "[앞 대화가 올 때]" in prompt and "resolved_request" in prompt
    assert prompt.index("[다시 나눌 때]") < prompt.index("[앞 대화가 올 때]")


def test_앞_대화_절은_조각과_kind_를_이번_입력으로_묶는다() -> None:
    section = build_system_prompt(with_context=True).split("[앞 대화가 올 때]")[1]

    assert "이번 입력에서만 자른다" in section
    assert "다시 record 로 보내지 않는다" in section
    assert "이번 입력의 말로 정한다" in section
    assert "unclear" in section


def test_프롬프트_예시는_후속_eval_문장을_옮기지_않는다() -> None:
    from tests.eval.agents.supervisor.followup_cases import FU_CASES

    prompt = build_system_prompt(with_context=True)

    assert not [case.case_id for case in FU_CASES if case.text in prompt]


def test_앞_대화가_없는_호출의_프롬프트에는_앞_대화_절이_없다() -> None:
    """context=None 호출은 앞 대화 기능이 생기기 전과 같은 글을 모델에 보낸다."""
    default = build_system_prompt()

    assert "[앞 대화가 올 때]" not in default and "resolved_request" not in default
    assert build_system_prompt(with_context=True).startswith(default)


def test_재촉하는_말투와_서술어_없는_간식_요청_예시가_있다() -> None:
    prompt = build_system_prompt()

    assert '"야 밥 추천 해달라고" → request/food/meal_recommendation' in prompt
    assert '"김밥 말고 다른 거 추천해달라니까" → request/food/meal_recommendation' in prompt
    assert '"어린이집 끝나고 간식" → request/food/meal_recommendation' in prompt
    request = prompt.split("[request")[1].split("[guarded")[0]
    assert "재촉하는 말투" in request


def test_다른_집_얘기와_또래_추천_예시가_있다() -> None:
    prompt = build_system_prompt()
    assert '"다른 엄마들은 뭐 먹여?" → out_of_scope' in prompt
    assert '"또래 애들한테 좋은 아침 메뉴 추천해줘" → request/food/meal_recommendation' in prompt
    assert '"친구네 집에서 피자 먹었어" → record/observe' in prompt
    else_part = prompt.split("[out_of_scope · unclear]")[1].split("[예시]")[0]
    assert "다른 집" in else_part
