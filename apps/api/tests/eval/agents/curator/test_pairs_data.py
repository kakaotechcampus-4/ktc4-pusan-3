"""임계값 실험 데이터 파일 규칙. API 를 부르지 않아 기본 테스트(make test)에서 돈다.

데이터를 고칠 때 실수로 규칙을 깨면 여기서 잡는다.
    - 형식 · 정답 값 · 도메인이 맞다 (읽기만 해도 DataError 로 드러난다)
    - 같은 쌍이 두 번 나오지 않고, 한 쌍의 두 subject 가 같지 않다
    - 🚨 선정용과 검증용(쌍 · 시나리오)은 subject 가 겹치지 않는다 — 겹치면 검증이 선정에 새어 든다
    - 한 쌍이 두 파일에 다른 정답으로 들어가지 않는다 (애매 목록 포함)
    - food subject 에 끼니 이름이 없다 — Memory Agent 가 subject 로 받지 않는 값이다
"""

from collections import Counter

from app.agents.memory.tools.observation import MEAL_SLOTS
from tests.eval.agents.curator.pairs import (
    HOLDOUT_PATH,
    LABELS,
    TUNE_PATH,
    load_ambiguous,
    load_pairs,
    load_scenarios,
    subjects,
)


def _key(domain: str, a: str, b: str) -> tuple[str, frozenset[str]]:
    return (domain, frozenset((a, b)))


def test_파일을_모두_읽을_수_있다() -> None:
    assert load_pairs(TUNE_PATH)
    assert load_pairs(HOLDOUT_PATH)
    assert load_scenarios()
    assert load_ambiguous()


def test_같은_쌍이_두_번_나오지_않고_두_subject_가_다르다() -> None:
    for path in (TUNE_PATH, HOLDOUT_PATH):
        pairs = load_pairs(path)
        same_subject = [p.line for p in pairs if p.a == p.b]
        assert not same_subject, f"{path.name} 두 subject 가 같은 줄: {same_subject}"
        counts = Counter(_key(p.domain, p.a, p.b) for p in pairs)
        repeated = [key for key, n in counts.items() if n > 1]
        assert not repeated, f"{path.name} 두 번 나온 쌍: {repeated}"


def test_선정용과_검증용은_subject_가_겹치지_않는다() -> None:
    tune = subjects(load_pairs(TUNE_PATH))
    holdout = subjects(load_pairs(HOLDOUT_PATH)) | {
        step.subject for scenario in load_scenarios() for step in scenario.steps
    }

    assert not tune & holdout, f"겹치는 subject: {sorted(tune & holdout)}"


def test_한_쌍이_여러_파일에_다른_정답으로_들어가지_않는다() -> None:
    seen: dict[tuple[str, frozenset[str]], str] = {}
    sources = [
        *((_key(p.domain, p.a, p.b), f"{TUNE_PATH.name}:{p.label}") for p in load_pairs(TUNE_PATH)),
        *(
            (_key(p.domain, p.a, p.b), f"{HOLDOUT_PATH.name}:{p.label}")
            for p in load_pairs(HOLDOUT_PATH)
        ),
        *((_key(p.domain, p.a, p.b), f"ambiguous:{p.kind}") for p in load_ambiguous()),
    ]
    for key, source in sources:
        assert key not in seen or seen[key] == source, f"{sorted(key[1])}: {seen[key]} · {source}"
        seen[key] = source


def test_도메인마다_세_가지_정답이_모두_있다() -> None:
    for path in (TUNE_PATH, HOLDOUT_PATH):
        pairs = load_pairs(path)
        for domain in {p.domain for p in pairs}:
            labels = {p.label for p in pairs if p.domain == domain}
            assert labels == set(LABELS), f"{path.name} {domain}: {sorted(labels)}"


def test_food_subject_에_끼니_이름이_없다() -> None:
    """밥 · 점심 · 간식 등은 create_observation_food 가 거절한다. 실제로 생길 수 없는 쌍이다."""
    food = {
        s
        for p in (*load_pairs(TUNE_PATH), *load_pairs(HOLDOUT_PATH), *load_ambiguous())
        if p.domain == "food"
        for s in (p.a, p.b)
    }
    food |= {step.subject for sc in load_scenarios() if sc.domain == "food" for step in sc.steps}

    slots = sorted(food & MEAL_SLOTS)
    assert not slots, f"끼니 이름: {slots}"
