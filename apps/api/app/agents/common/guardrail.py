"""위험 물질 검사를 바꿔 끼우는 자리 (#731).

안전 사전검사(`pipeline._safety_precheck`)와 Memory 저장 게이트(`memory.agent._hazard`)가 이
Protocol 하나로 검사한다. 지금 구현은 코드 규칙(`app/rules/hazard.py`)을 감싼 `StaticRuleGuardrail`
하나다. 두 곳은 run 마다 같은 인스턴스(`memory.context.AgentContext.guardrail`)를 쓴다 — 한쪽만
바뀌어 사전검사와 저장 게이트가 다른 기준으로 거르는 일이 없게.

🚨 fail-closed — 검사를 하지 못하면 빈 결과("걸린 것 없음")를 돌려주지 않고
   `GuardrailUnavailableError` 를 올린다. 부르는 쪽은 이 예외를 받으면 그 run 을 진행하지 않는다.
   지금은 아무도 잡지 않는다 — 러너가 run 을 실패로 끝내고, Memory commit 전이라 저장도 남지 않는다.
   실패를 `GuardResult` 의 칸으로 두지 않는 것은 부르는 곳마다 "실패면 막는다" 를 챙기지 않아도
   되게 하려는 것이다. 한 곳만 빠뜨려도 검사가 조용히 빠진다.

`GuardResult` 는 걸린 종류만 싣는다.
- 걸린 낱말은 싣지 않는다. 원문 조각이라 로그에 찍히기 쉽다 (로그에 원문 금지, 루트 §2)
- 화면 문구도 싣지 않는다. 걸렸을 때 문구는 pipeline 이(`HAZARD_GUIDANCE`), 검사를 못 했을 때
  문구는 실패 종류가 정한다

`app/rules/` 에 두지 않은 것은 규칙 레이어가 표준 라이브러리만 쓰기 때문이다. 다음 구현(모더레이션
API 등)의 실패 예외는 Agent 쪽 실패 종류와 같은 계통에 묶여야 한다 — `SafetyLookupError` 가
`common/ports.py` 에 있는 것과 같은 자리다.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.rules.hazard import (
    FOOD_NAME_TERMS,
    PLAY_NAME_TERMS,
    HazardKind,
    HazardRules,
    find_hazards,
    hazard_names,
)
from app.rules.hazard_rules import DEFAULT_RULES
from app.rules.term_match import Term

# 무엇을 검사하는가.
#   input      보호자가 보낸 한 줄 전체 (안전 사전검사)
#   food_name  음식 · 생활 관찰의 대상 이름 칸 (저장 게이트) — 술 · 담배 · 화학제품 · 마약류
#   play_name  놀이 · 배움 관찰의 대상 이름 칸 (저장 게이트) — 마약류만
GuardContext = Literal["input", "food_name", "play_name"]


@dataclass(frozen=True)
class GuardResult:
    """걸린 위험 종류. 순서는 `HAZARD_KINDS`. 없으면 빈 튜플이다."""

    kinds: tuple[HazardKind, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.kinds)


class GuardrailUnavailableError(Exception):
    """검사를 하지 못함. 걸린 것이 없다(빈 `GuardResult`)와 다르다."""


class Guardrail(Protocol):
    async def check(self, text: str, context: GuardContext) -> GuardResult:
        """`text` 에서 걸린 위험 종류.

        검사를 하지 못하면 빈 결과 대신 `GuardrailUnavailableError` 를 올린다. 부르는 쪽은 이 예외를
        받으면 그 run 을 진행하지 않는다 (fail-closed).
        """
        ...


_NAME_TERMS: dict[str, tuple[Term, ...]] = {
    "food_name": FOOD_NAME_TERMS,
    "play_name": PLAY_NAME_TERMS,
}


class StaticRuleGuardrail:
    """코드 규칙(`app/rules/hazard.py`)으로 검사한다. 외부 I/O 가 없어 검사를 못 하는 경우가 없다.

    `rules` 는 `input` 검사의 사전 묶음이다. 이름 칸 묶음(`FOOD_NAME_TERMS` · `PLAY_NAME_TERMS`)은
    아직 `HazardRules` 밖이라 바꿔 끼우지 않는다.
    """

    def __init__(self, rules: HazardRules = DEFAULT_RULES) -> None:
        self._rules = rules

    async def check(self, text: str, context: GuardContext) -> GuardResult:
        if context == "input":
            return GuardResult(find_hazards(text, rules=self._rules))
        # 칸 하나씩 본다. 칸끼리 이어 붙이지 않는 것은 `hazard_names` 와 같다
        return GuardResult(hazard_names([text], _NAME_TERMS[context]))
