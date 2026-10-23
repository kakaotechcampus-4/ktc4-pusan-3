"""의료 처치 · 증상 닫힘 — 사람이 정한 목록 두 개로 코드가 루틴 요청을 닫는다.

목록은 `reference/growth_closures.yaml` 이다 (Growth_Tool_명세.md §2 "루틴에서 닫는 것").

    medical_routine  약 먹이기 · 안약 · 연고 · 흡입기 … → `closed.medical_routine`
    symptom_habit    눈 깜빡임 · 킁킁거림 · 머리 박기 … → `closed.symptom_habit`

걸리면 모델을 부르지 않고 코드 문구로 끝낸다(모델 0회). 진단명은 말하지 않고, Health 로 보내지도
않는다 — Health 는 기록만 하고 조언하지 않아서 보호자가 답을 못 받는다.

문장은 **하나씩 따로** 대조한다. 요청 문장이 둘이면 이어 붙이지 않는다.
guard 는 띄어쓰기를 넘지 않는다(`match_fields`) —
"약 한 숟가락" 의 "약 한" 은 guard "약한" 이 아니다.
띄어 쓴 guard("약 올")는 붙여 쓴 것도 덮는다. "약 30분" · "약 3살" 처럼 숫자와 시간 · 나이 단위
앞의 "약" 은 '대략' 이라 지우고 본다(`_APPROXIMATE`). 오탐 · 놓침은 지어낸 문장으로 잰다 —
tests/unit/agents/growth/test_growth_closure_cases.py.
모델 출력에 걸린 후보는 빼고 재호출 규칙을 따른다 — 출력 쪽 연결은 모델 경로가 한다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.agents.growth.readouts import CLOSED_MEDICAL_ROUTINE, CLOSED_SYMPTOM_HABIT
from app.rules.term_match import Term, match_fields, normalize

_LIST_KEYS = frozenset({"medical_routine", "symptom_habit"})
_TERM_KEYS = frozenset({"label", "aliases", "guards"})
# "약 30분 걸려" · "약 3살부터" 의 약은 '대략' 이다. 숫자 + 시간 · 나이 단위 앞의 약만 지운다.
# "약 4시간마다" 는 먹이는 간격이라 남긴다. 일 · 주 · 개월은 먹이는 기간("약 6개월째")일 수 있어
# 넣지 않는다
_APPROXIMATE = re.compile(r"약\s*\d+(?:\.\d+)?\s*(?:분|시간|초|살|세)(?!\s*마다)")


@dataclass(frozen=True)
class ClosureTerms:
    medical: tuple[Term, ...]
    symptom: tuple[Term, ...]


@cache
def closure_terms() -> ClosureTerms:
    """`growth_closures.yaml` 을 읽는다. 읽지 못하거나 모양이 틀리면 예외를 그대로 올린다.

    빈 목록으로 통과시키면 약 · 증상 요청이 조용히 코칭된다.
    """
    return parse_closure_terms(load_reference("growth_closures.yaml"))


def parse_closure_terms(data: dict[str, Any]) -> ClosureTerms:
    missing = _LIST_KEYS - set(data)
    if missing:
        raise ValueError(f"growth_closures.yaml 에 목록이 없다: {sorted(missing)}")
    seen: set[str] = set()
    parsed: dict[str, tuple[Term, ...]] = {}
    for name in sorted(_LIST_KEYS):
        terms: list[Term] = []
        for raw in data[name] or []:
            if extra := set(raw) - _TERM_KEYS:
                raise ValueError(
                    f"growth_closures.yaml {name} 에 정해지지 않은 칸이 있다: {sorted(extra)}"
                )
            label = raw.get("label")
            aliases = tuple(raw.get("aliases") or ())
            guards = tuple(raw.get("guards") or ())
            if not label or not aliases:
                raise ValueError(
                    f"growth_closures.yaml {name} 의 항목에 label 이나 aliases 가 비었다"
                )
            if label in seen:
                raise ValueError(f"growth_closures.yaml 에 label 이 겹친다: {label!r}")
            seen.add(label)
            for alias in aliases:
                if len(normalize(alias)) < 2 and not guards:
                    raise ValueError(
                        f"growth_closures.yaml {label!r} 의 한 글자 별칭 {alias!r} 에 "
                        "guard 가 없다 — 과차단"
                    )
            terms.append(Term(key=label, aliases=aliases, guards=guards))
        if not terms:
            raise ValueError(f"growth_closures.yaml {name} 가 비었다")
        parsed[name] = tuple(terms)
    return ClosureTerms(medical=parsed["medical_routine"], symptom=parsed["symptom_habit"])


def closure_key(texts: Sequence[str], *, terms: ClosureTerms | None = None) -> str | None:
    """요청 문장(또는 인용한 관찰의 subject)이 닫히는가. 닫히면 readout 키, 아니면 None.

    의료 처치가 먼저다 — "약 먹을 때 눈을 깜빡여" 는 처치 쪽 문구가 맞다.
    """
    terms = terms or closure_terms()
    fields = [_APPROXIMATE.sub(" ", text) for text in texts]
    if any(match_fields((text,), terms.medical) for text in fields):
        return CLOSED_MEDICAL_ROUTINE
    if any(match_fields((text,), terms.symptom) for text in fields):
        return CLOSED_SYMPTOM_HABIT
    return None
