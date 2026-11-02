"""Supervisor 프롬프트 검증. LLM 을 부르지 않는다 — 경계 예시가 프롬프트에 실렸는지만 본다."""

from app.agents.supervisor.prompt import build_system_prompt


def test_food_check_라벨과_경계_예시가_있다() -> None:
    prompt = build_system_prompt()
    assert "food로 보내는 건 네 가지다." in prompt
    assert '"땅콩소스 먹여도 될까?" → request/food/food_check' in prompt
    assert '"해열제 더 먹여도 돼?" → guarded/diagnosis' in prompt


def test_무엇을_먹일지_없이_시점만_묻는_말은_food_가_아니다() -> None:
    prompt = build_system_prompt()
    assert '"지금 또 먹여도 돼?" → request/health' in prompt
