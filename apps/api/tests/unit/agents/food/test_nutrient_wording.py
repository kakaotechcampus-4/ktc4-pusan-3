"""영양소 분석 문장 검사 (#357 리뷰 1 — 진단처럼 읽히는 문장).

화면 문장의 주어는 먹은 식사 · 기록이다. 아이 몸 상태 · 수치 · 진단 · 처방으로 읽히면 거절한다.
거절된 문장은 모델이 다시 쓰거나 코드 문장 틀로 바뀐다 — 오탐은 안전 쪽으로만 틀린다.
"""

import pytest

from app.agents.food.kdri import refs_for
from app.agents.food.nutrient_actions import ACTIONS, advice_for
from app.agents.food.nutrient_wording import NUTRIENT_LABELS, fallback_readouts, wording_problem
from app.agents.food.nutrition_eval import NutrientVerdict
from app.agents.food.readouts import NUTRIENT_INSUFFICIENT, READOUTS
from app.agents.food.schemas.nutrition import FINDING_EXAMPLE, NutrientReportArgs

MUST_REJECT = [
    ("최근 식단에서 칼슘이 120mg 이에요", "number"),
    ("최근 식단에서 칼슘 120밀리그램", "number"),  # PM 이 통과시킨 예
    ("최근 식단에서 칼슘 백이십 밀리그램", "number"),  # 숫자를 한글로 써도 단위가 남는다
    ("최근 식단에서 칼슘 １２０ｍｇ", "number"),  # 전각 — NFKC
    ("기록된 식사를 보면 칼슘이 2배 많아요", "number"),
    ("기록된 식사를 보면 칼슘이 두 배 많아요", "number"),  # 수를 한글로 쓴 배수
    ("기록된 식사를 보면 칼슘이 이십 프로 적어요", "number"),
    ("기록된 식사를 보면 칼슘 식품이 절반 정도예요", "number"),
    ("기록된 식사를 보면 우유를 하루 200ml 마셨어요", "number"),
    ("최근 식단에서 칼슘 120밀리그람", "number"),  # 맞춤법이 틀린 단위어
    ("기록된 식사를 보면 철분 결핍이 의심돼요", "prescription"),
    ("기록된 식사를 보면 칼슘 식품을 챙겨 주세요", "prescription"),
    ("기록된 식사를 보면 아이 몸에 칼슘이 모자란 편이에요", "body"),  # PM 이 통과시킨 예
    ("최근 식단에서 칼슘이 부족해요", "body"),
    ("기록된 식사를 보면 철분 상태가 걱정돼요", "body"),
    ("기록된 식사를 보면 아이 칼슘이 적은 편이에요", "child_subject"),
    ("아이 칼슘이 적은 편이에요", "child_subject"),  # PM 이 통과시킨 예
    ("칼슘 식품이 적은 편이에요", "no_meal_subject"),
    # 식사 주어를 앞에 붙여도 뒤에 건강 예언 · 또래 비교 · 병원 권유가 오면 거절한다 (최종 리뷰)
    ("기록된 식사를 보면 칼슘 식품이 적은 편이라 뼈가 약해질 수 있어요", "body"),
    ("기록된 식사를 보면 당류 많은 음식이 자주 보여서 충치가 생길 수 있어요", "body"),
    ("기록된 식사를 보면 나트륨 많은 음식이 자주 보여 혈압에 좋지 않아요", "body"),
    ("기록된 식사를 보면 철분 식품이 적은 편이라 면역력이 떨어질 수 있어요", "body"),
    ("기록된 식사를 보면 지방 많은 음식이 자주 보여 비만으로 이어질 수 있어요", "body"),
    ("기록된 식사를 보면 칼슘 식품이 적은 편이라 키가 덜 자랄 수 있어요", "body"),
    (
        "기록된 식사를 보면 또래보다 칼슘 식품이 적은 편이에요",
        "body",
    ),  # 루트 CLAUDE.md §2 또래 비교
    ("기록된 식사를 보면 칼슘 식품이 적은 편이니 소아과 상담을 받아 보세요", "body"),
    ("기록된 식사를 보면 칼슘 식품이 적은 편이니 병원에 가 보세요", "body"),
    # 알려진 오탐 — 아이스크림의 "아이". 걸리면 모델이 다시 쓰거나 코드 문장으로 가서
    # 안전한 쪽으로만 틀린다
    ("기록된 식사를 보면 아이스크림 당류가 많은 편이에요", "child_subject"),
]

MUST_PASS = [
    "최근 식단에서 칼슘 식품이 적은 편이에요",
    "기록된 식사 9건을 보면 나트륨 많은 음식이 자주 보여요",  # 기록 건수는 숫자여도 된다
    "식사 기록을 보면 단백질 반찬은 적당한 편이에요",
    # 아이스크림이 주어 뒤에 있고 아이가 영양소의 주인이 아니다
    "기록된 식사를 보면 아이스크림·과자 같은 당류 많은 간식이 자주 보여요",
    # 배수 검사가 과일 "배" · "배추" 를 잡지 않는다
    "기록된 식사를 보면 사과·배 같은 과일이 자주 보여요",
    "기록된 식사를 보면 배추김치 같은 짠 반찬이 자주 보여요",
]


@pytest.mark.parametrize(("finding", "problem"), MUST_REJECT)
def test_거절할_문장(finding, problem):
    assert wording_problem(finding) == problem


@pytest.mark.parametrize("finding", MUST_PASS)
def test_통과할_문장(finding):
    assert wording_problem(finding) is None


# ── 모델이 끝내 못 쓰면 코드 문장 틀로 낸다 (#357 리뷰 1) ──────────────
def _verdict(bands, *, records=9, insufficient=False):
    return NutrientVerdict(
        bands=bands,
        unscored=(),
        records=records,
        resolved_ratio=1.0,
        insufficient=insufficient,
        food_groups=frozenset(),
    )


def test_대체_문장은_식사를_주어로_구간과_안내를_말한다():
    (readout,) = fallback_readouts(
        _verdict({"calcium": "low", "sodium": "high", "protein": "ok"}),
        {"calcium": "우유·유제품", "sodium": "국물·절임 줄이기"},
    )
    assert readout.authored_by == "code"
    assert readout.body.splitlines() == [
        "기록된 식사 9건을 보면 칼슘 식품이 적은 편이에요 — 우유·유제품",
        "기록된 식사 9건을 보면 나트륨 많은 음식이 자주 보여요 — 국물·절임 줄이기",
    ]


def test_안내가_다_빠진_항목은_구간만_말한다():
    """칼슘 부족 + 우유 알레르기면 안내 식품이 다 빠진다 (#321 합의 6번)."""
    (readout,) = fallback_readouts(_verdict({"calcium": "low"}), {})
    assert readout.body == "기록된 식사 9건을 보면 칼슘 식품이 적은 편이에요."


def test_모두_적당하면_한_문장():
    (readout,) = fallback_readouts(_verdict({"calcium": "ok", "iron": "ok"}), {})
    assert readout.body == "기록된 식사 9건을 보면 살펴본 영양소가 고르게 담겨 있어요."


def test_기록이_부족하면_insufficient_문구():
    readouts = fallback_readouts(_verdict({}, records=2, insufficient=True), {})
    assert readouts == (READOUTS.render(NUTRIENT_INSUFFICIENT, recorded=2),)


@pytest.mark.parametrize("band", ["low", "high"])
@pytest.mark.parametrize("nutrient", sorted(NUTRIENT_LABELS))
@pytest.mark.parametrize("action", [None, "밥·빵·면 줄이고 반찬 한 가지"])
def test_대체_문장은_자기_검사를_통과한다(band, nutrient, action):
    """코드 문장이 모델 문장 검사에 걸리면 같은 화면에 기준이 둘이 된다."""
    actions = {} if action is None else {nutrient: action}
    (readout,) = fallback_readouts(_verdict({nutrient: band}), actions)
    for line in readout.body.splitlines():
        assert wording_problem(line) is None


@pytest.mark.parametrize("entry", ACTIONS, ids=lambda entry: f"{entry.nutrient}-{entry.band}")
def test_실제_식품군_안내를_붙여도_자기_검사를_통과한다(entry):
    """위 표는 지어낸 안내 하나만 본다. 표(nutrient_actions.yaml)의 안내가 바뀌어도 걸리지 않는지
    실제 값으로 본다 — 알레르기 없는 아이 기준으로 안내가 가장 길게 나온다."""
    advice = advice_for(entry.nutrient, entry.band, (), stage="toddler", months=24)
    assert advice is not None and advice.text
    (readout,) = fallback_readouts(
        _verdict({entry.nutrient: entry.band}), {entry.nutrient: advice.text}
    )
    for line in readout.body.splitlines():
        assert wording_problem(line) is None


@pytest.mark.parametrize("group", ["1-2y", "3-5y"])
def test_판정하는_영양소는_전부_화면_이름이_있다(group):
    """energy 는 판정에서 뺀다 (nutrition_eval — 권장 열량 계산이 붙을 때 쓴다)."""
    assert set(refs_for(group)) - {"energy"} <= set(NUTRIENT_LABELS)


def test_모델에게_보여주는_findings_예문도_문장_규칙을_통과한다():
    """스키마 설명의 예문이 규칙에 걸리면 모델이 예문을 따라 쓰고 거절당한다 (#357 리뷰 마무리)."""
    assert wording_problem(FINDING_EXAMPLE) is None
    assert FINDING_EXAMPLE in NutrientReportArgs.model_fields["findings"].description


def test_판정한_항목이_하나도_없으면_고르다고_말하지_않는다():
    """기록은 충분한데 모든 항목이 빈 칸(unscored)이면 bands 가 빈다. 이때 "고르게 담겨 있어요" 는
    근거 없는 안심이다 — 살펴보지 못했다고 말한다 (#357 최종 리뷰)."""
    readouts = fallback_readouts(_verdict({}), {})
    assert readouts == (READOUTS.render("nutrient.unscored", recorded=9),)


@pytest.mark.parametrize("bands", [{"calcium": "ok"}, {"calcium": "low"}, {"sodium": "high"}])
def test_구간을_말하는_코드_문장은_모델_문장과_같은_kind_다(bands):
    """화면 · eval 이 kind 로 영양 문장을 고른다. all_ok 만 다른 kind 면 문장 검사에서 빠진다."""
    (readout,) = fallback_readouts(_verdict(bands), {})
    assert readout.kind == "nutrient_analysis"
