"""임계값 실험 데이터 파일 규칙. API 를 부르지 않아 기본 테스트(make test)에서 돈다.

데이터를 고칠 때 실수로 규칙을 깨면 여기서 잡는다.
    - 형식 · 정답 값 · 도메인이 맞다 (읽기만 해도 DataError 로 드러난다)
    - 같은 쌍이 두 번 나오지 않고, 한 쌍의 두 subject 가 같지 않다
    - 🚨 선정용과 검증용(쌍 · 시나리오)은 subject 가 겹치지 않는다 — 겹치면 검증이 선정에 새어 든다
    - 한 쌍이 두 파일에 다른 정답으로 들어가지 않는다 (판단이 갈리는 쌍 포함)
    - food subject 에 끼니 이름이 없다 — Memory Agent 가 subject 로 받지 않는 값이다
    - 🚨 실험 5 최종 확인용(pairs_final · scenarios_final)은 지금까지 쓴 모든 데이터와 subject 가
      겹치지 않는다 — 한 번이라도 본 이름이면 최종 확인이 아니다
    - scale_tune 의 기대값은 profiles 에 있거나 none 이고, ask 는 profiles 와 이름이 같지 않다
"""

from collections import Counter

from app.agents.curator.embedding.link_step import same_name_key
from app.agents.memory.tools.observation import MEAL_SLOTS
from tests.eval.agents.curator.pairs import (
    FINAL_PATH,
    HOLDOUT_PATH,
    LABELS,
    SCENARIOS_FINAL_PATH,
    SCENARIOS_PATH,
    TUNE_PATH,
    load_ambiguous,
    load_pairs,
    load_scale,
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


# 실험 5


def _used_before_final() -> set[str]:
    """실험 5 최종 확인 전에 한 번이라도 쓴 이름. 5a 의 scale_tune 도 포함한다."""
    used = subjects(load_pairs(TUNE_PATH)) | subjects(load_pairs(HOLDOUT_PATH))
    used |= {s for p in load_ambiguous() for s in (p.a, p.b)}
    used |= {st.subject for sc in load_scenarios(SCENARIOS_PATH) for st in sc.steps}
    used |= {name for sc in load_scale() for name in (*sc.profiles, *(a.subject for a in sc.asks))}
    return {same_name_key(s) for s in used}


def _final_subjects() -> set[str]:
    names = subjects(load_pairs(FINAL_PATH))
    names |= {st.subject for sc in load_scenarios(SCENARIOS_FINAL_PATH) for st in sc.steps}
    return names


def test_실험5_최종_확인용은_지금까지_쓴_이름과_겹치지_않는다() -> None:
    """띄어쓰기만 다른 이름도 겹침으로 본다 — 이름 비교가 같은 것으로 잡기 때문이다."""
    overlap = sorted(s for s in _final_subjects() if same_name_key(s) in _used_before_final())
    assert not overlap, f"이미 쓴 이름: {overlap}"


def test_실험5_최종_확인용_쌍의_규칙() -> None:
    pairs = load_pairs(FINAL_PATH)
    assert all(p.a != p.b for p in pairs)
    counts = Counter(_key(p.domain, p.a, p.b) for p in pairs)
    assert not [k for k, n in counts.items() if n > 1]
    for domain in {p.domain for p in pairs}:
        assert {p.label for p in pairs if p.domain == domain} == set(LABELS)
    food = {s for p in pairs if p.domain == "food" for s in (p.a, p.b)}
    assert not food & MEAL_SLOTS


def test_scale_tune_기대값은_profiles_에_있거나_none_이다() -> None:
    for sc in load_scale():
        names = {same_name_key(p) for p in sc.profiles}
        assert len(names) == len(sc.profiles), f"{sc.id} profiles 에 같은 이름이 있다"
        for ask in sc.asks:
            assert ask.expected == "none" or ask.expected in sc.profiles, f"{sc.id} {ask}"
            # 이름이 같으면 판정기를 부르지 않는다 — 판정을 보려는 ask 가 아니다
            assert same_name_key(ask.subject) not in names, f"{sc.id} {ask.subject}"


def test_scale_tune_은_추리기_경로를_한_번_이상_탄다() -> None:
    from app.agents.curator.embedding.link_step import MAX_CANDIDATES

    assert any(len(sc.profiles) > MAX_CANDIDATES for sc in load_scale())


def test_exp5_v3_예시는_실험_데이터에_없는_이름이다() -> None:
    """데이터에 있는 이름을 예시로 쓰면 v3 조건만 정답 힌트를 보고 푸는 셈이 된다."""
    from tests.eval.agents.curator.run_exp5 import V3_EXAMPLES

    used = _used_before_final() | {same_name_key(s) for s in _final_subjects()}
    leaked = sorted(e for e in V3_EXAMPLES if same_name_key(e) in used)
    assert not leaked, f"데이터에 있는 예시: {leaked}"
