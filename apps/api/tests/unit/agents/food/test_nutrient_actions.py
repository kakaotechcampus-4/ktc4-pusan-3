"""구간 → 식품군 안내 표와 알레르기 식품 빼기 — `영양소_계산_설계.md` §6.

달걀 알레르기 아이가 단백질이 적게 나오면 "고기·생선·달걀·콩 반찬 늘리기" 가 그대로 나가던
구멍을 막는다. 표의 식품을 메뉴 행처럼 만들어 `filter_food_safety` 에 그대로 통과시키고,
남은 것만 안내한다 — 알레르기 판정을 여기서 새로 짜지 않는다.

기대 문구는 설계 문서 표에서 옮겼다. 알레르기 이름은 실제 아이의 정보가 아니라 시나리오다.
"""

import pytest

from app.agents.food.nutrient_actions import (
    ACTIONS,
    advice_for,
    check_summary,
    mentions_removed,
)
from app.agents.food.store import SafetyEntry

TODDLER = {"stage": "toddler", "months": 24}
OLDER = {"stage": "preschool", "months": 60}  # 영아 규칙 · 떡 · 질식 규칙이 끼지 않는 월령


def allergy(label: str, *, kind: str = "allergy", status: str = "active") -> SafetyEntry:
    return SafetyEntry(kind=kind, label=label, status=status)  # type: ignore[arg-type]


def text_of(nutrient: str, band: str, *entries: SafetyEntry) -> str | None:
    advice = advice_for(nutrient, band, entries, **TODDLER)  # type: ignore[arg-type]
    assert advice is not None
    return advice.text


# ── 표: 설계 §6 의 다섯 줄 그대로 ─────────────────────────────────────
@pytest.mark.parametrize(
    ("nutrient", "band", "expected"),
    [
        ("protein", "low", "고기·생선·달걀·콩 반찬 늘리기"),
        ("calcium", "low", "우유·유제품"),
        ("iron", "low", "붉은 고기·달걀노른자·콩"),
        ("carbohydrate", "high", "밥·빵·면 줄이고 반찬 한 가지"),
        ("sodium", "high", "국물·절임 줄이기"),
    ],
)
def test_알레르기가_없으면_표_문구_그대로(nutrient: str, band: str, expected: str) -> None:
    assert text_of(nutrient, band) == expected


def test_표에_없는_조합은_안내가_없다() -> None:
    # ok 이거나 표에 행동이 없는 구간. 모델이 항목을 더하지 않는다
    assert advice_for("protein", "ok", (), **TODDLER) is None  # type: ignore[arg-type]
    assert advice_for("calcium", "high", (), **TODDLER) is None  # type: ignore[arg-type]
    assert advice_for("sugars", "high", (), **TODDLER) is None  # type: ignore[arg-type]


def test_표는_다섯_줄이다() -> None:
    assert len(ACTIONS) == 5


# ── 알레르기 식품을 뺀다 ──────────────────────────────────────────────
def test_달걀_알레르기_아이는_단백질_안내에서_달걀이_빠진다() -> None:
    assert text_of("protein", "low", allergy("달걀")) == "고기·생선·콩 반찬 늘리기"


def test_달걀_알레르기_아이는_철_안내에서_달걀노른자가_빠진다() -> None:
    assert text_of("iron", "low", allergy("달걀")) == "붉은 고기·콩"


@pytest.mark.parametrize("label", ["계란", "난류", "달걀 알레르기", "Egg"])
def test_달걀을_다른_이름으로_등록해도_빠진다(label: str) -> None:
    assert text_of("protein", "low", allergy(label)) == "고기·생선·콩 반찬 늘리기"


@pytest.mark.parametrize(
    ("label", "protein", "iron"),
    [
        ("우유", "고기·생선·달걀·콩 반찬 늘리기", "붉은 고기·달걀노른자·콩"),
        ("대두", "고기·생선·달걀 반찬 늘리기", "붉은 고기·달걀노른자"),
        ("콩", "고기·생선·달걀 반찬 늘리기", "붉은 고기·달걀노른자"),
        ("돼지고기", "생선·달걀·콩 반찬 늘리기", "달걀노른자·콩"),
        ("쇠고기", "생선·달걀·콩 반찬 늘리기", "달걀노른자·콩"),
        # 닭고기는 붉은 고기가 아니다 — 철 안내는 그대로, 단백질의 "고기" 는 빠진다(더 막는 쪽)
        ("닭고기", "생선·달걀·콩 반찬 늘리기", "붉은 고기·달걀노른자·콩"),
        ("고등어", "고기·달걀·콩 반찬 늘리기", "붉은 고기·달걀노른자·콩"),
        ("새우", "고기·생선·달걀·콩 반찬 늘리기", "붉은 고기·달걀노른자·콩"),
    ],
)
def test_19종_알레르기별로_해당_식품만_빠진다(label: str, protein: str, iron: str) -> None:
    assert text_of("protein", "low", allergy(label)) == protein
    assert text_of("iron", "low", allergy(label)) == iron


@pytest.mark.parametrize(
    ("label", "protein"),
    [
        ("육류", "생선·달걀·콩 반찬 늘리기"),
        ("생선", "고기·달걀·콩 반찬 늘리기"),
        ("콩류", "고기·생선·달걀 반찬 늘리기"),
        ("해산물", "고기·달걀·콩 반찬 늘리기"),
    ],
)
def test_묶음_이름으로_등록해도_그_식품이_빠진다(label: str, protein: str) -> None:
    assert text_of("protein", "low", allergy(label)) == protein


def test_땅콩_알레르기는_콩_안내를_막지_않는다() -> None:
    # 땅콩은 대두가 아니다. 보호자가 콩류 전체를 뜻하면 "콩류" 로 등록한다
    assert text_of("protein", "low", allergy("땅콩")) == "고기·생선·달걀·콩 반찬 늘리기"


def test_알레르기가_여럿이면_모두_뺀다() -> None:
    entries = (allergy("달걀"), allergy("콩"), allergy("우유"))
    assert text_of("protein", "low", *entries) == "고기·생선 반찬 늘리기"


def test_알레르기를_쉼표로_묶어_적어도_모두_뺀다() -> None:
    assert text_of("protein", "low", allergy("달걀, 콩")) == "고기·생선 반찬 늘리기"


def test_표의_식품이_다_빠지면_식품_안내_없이_구간만_말한다() -> None:
    # 칼슘이 적은데 우유 알레르기 — 다른 급원 식품을 표에 더하려면 섭취기준 원문 근거가 필요하다
    advice = advice_for("calcium", "low", [allergy("우유")], **TODDLER)  # type: ignore[arg-type]

    assert advice is not None
    assert advice.text is None
    assert advice.removed == ("우유", "유제품")


def test_단백질_식품이_전부_빠져도_안내만_없고_구간은_남는다() -> None:
    entries = [allergy(label) for label in ("달걀", "콩", "돼지고기", "고등어")]

    advice = advice_for("protein", "low", entries, **TODDLER)  # type: ignore[arg-type]

    assert advice is not None and advice.text is None
    assert set(advice.removed) == {"고기", "생선", "달걀", "콩"}


def test_빠진_식품_이름을_알려준다() -> None:
    advice = advice_for("protein", "low", [allergy("달걀")], **TODDLER)  # type: ignore[arg-type]

    assert advice is not None
    assert advice.removed == ("달걀",)


def test_줄이는_안내는_알레르기와_상관없이_그대로다() -> None:
    # "밥·빵·면 줄이고" 는 먹이라는 말이 아니다. 밀 알레르기 아이에게도 같은 말이 나간다
    entries = [allergy("밀"), allergy("대두")]
    assert text_of("carbohydrate", "high", *entries) == "밥·빵·면 줄이고 반찬 한 가지"
    assert text_of("sodium", "high", *entries) == "국물·절임 줄이기"


# ── 알레르기로 읽지 않는 것 ───────────────────────────────────────────
def test_등록을_철회했거나_없다고_답한_알레르기는_빼지_않는다() -> None:
    entries = [allergy("달걀", status="retracted"), allergy("우유", status="none")]

    assert text_of("protein", "low", *entries) == "고기·생선·달걀·콩 반찬 늘리기"


def test_환경_알레르기는_식품을_빼지_않는다() -> None:
    assert text_of("protein", "low", allergy("꽃가루")) == "고기·생선·달걀·콩 반찬 늘리기"


# ── 질환으로 막는 식품도 뺀다 ─────────────────────────────────────────
def test_유당불내증은_우유_안내를_뺀다() -> None:
    entries = [allergy("유당불내증", kind="chronic_disease")]

    assert text_of("calcium", "low", *entries) is None


def test_알레르기_칸에_적은_질환_이름도_같다() -> None:
    assert text_of("calcium", "low", allergy("유당불내증")) is None


def test_사전에_없는_질환은_식품을_빼지_않는다() -> None:
    entries = [allergy("당뇨", kind="chronic_disease")]

    assert text_of("protein", "low", *entries) == "고기·생선·달걀·콩 반찬 늘리기"


# ── 요약 문장에 빠진 식품이 남지 않는다 ───────────────────────────────
def test_요약에_빠진_식품_이름이_있으면_찾아낸다() -> None:
    found = mentions_removed("단백질이 부족한 편이에요. 달걀 반찬을 늘려 보세요.", ("달걀",))

    assert found == ("달걀",)


@pytest.mark.parametrize(
    ("removed", "sentence"),
    [
        (("달걀",), "계란 한 알을 더 챙겨 보세요"),  # 같은 식품의 다른 이름
        (("달걀",), "노른자를 더 챙겨 보세요"),
        (("달걀노른자",), "달걀 노른자가 좋아요"),
        (("우유", "유제품"), "치즈나 요거트도 좋아요"),
        (("콩",), "두부나 두유를 늘려 보세요"),
        (("콩",), "강낭콩을 늘려 보세요"),
        (("고기",), "쇠고기나 닭고기를 늘려 보세요"),
        (("생선",), "고등어를 늘려 보세요"),
        (("달걀",), "달 걀을 늘려 보세요"),  # 띄어쓰기
    ],
)
def test_같은_식품의_다른_이름도_찾아낸다(removed: tuple[str, ...], sentence: str) -> None:
    assert mentions_removed(sentence, removed)


@pytest.mark.parametrize(
    "sentence",
    ["철분 보충을 위해 달걀을 챙겨 주세요", "아침에는 계란찜을 곁들여 보세요"],
)
def test_달걀노른자가_빠지면_달걀_계란도_찾아낸다(sentence: str) -> None:
    # 철 low 에서 빠지는 식품 이름은 "달걀노른자" 다. 별칭이 "노른자" 뿐이면 달걀 알레르기 아이의
    # 요약에 "달걀" · "계란" 이 남아도 지나간다 (10-11 라이브 eval test_nutrient_advice)
    assert mentions_removed(sentence, ("달걀노른자",)) == ("달걀노른자",)


def test_남아_있는_식품은_요약에_써도_된다() -> None:
    assert mentions_removed("고기와 생선 반찬을 늘려 보세요", ("달걀",)) == ()


def test_빠진_식품이_없으면_아무것도_찾지_않는다() -> None:
    assert mentions_removed("달걀 반찬을 늘려 보세요", ()) == ()


def test_빠진_식품과_상관없는_말은_걸리지_않는다() -> None:
    # "적게" 의 "게" 같은 한 글자 별칭이 문장에 걸리지 않는다 — 표에 있는 식품 이름만 본다
    assert mentions_removed("짜게 먹지 않도록 국물을 적게 드세요", ("달걀", "우유")) == ()


# ── 요약 검사 — 별칭 + 안전 필터 사전 (#326 리뷰, 영양소_계산_설계 N-14) ─────────────
# 별칭(mentions_removed)은 표에 적은 이름만 본다. 라이브 eval 에서 모델이 알레르기 음식을 권한
# 문장 74개 중 28개가 별칭을 빠져나갔다(삼겹살 · 돈육 · 삼계탕 · 에그샌드위치 · 밀크 …).
# 그래서 같은 문장에 안전 필터 사전도 건다 — 잘못 막으면 요약 하나를 거절할 뿐이다.
#
# 아래는 그 eval 에서 모델이 "그 성분이 거의 늘 든 음식" 으로 쓴 이름이다. 모델 문장은 매번 달라서
# 같은 구멍을 다시 잡는다는 보장이 없어, 이름을 고정해 사전을 고칠 때마다 확인한다.
# 뺀 것: 김(해조류 — 모델 실수) · 새우튀김(튀김옷 달걀은 집 · 제품마다 다르다 — PM 확인 대기)
SUMMARY_FOODS: list[tuple[str, str, str]] = [
    *(
        ("난류", "allergy", food)
        for food in "메추리알 돈가스 참치마요주먹밥 달걀찜 계란말이 삶은계란 에그샌드위치 "
        "카스텔라".split()
    ),
    *(
        ("대두", "allergy", food)
        for food in "두부부침 콩나물무침 유부초밥 순두부 검은콩밥 된장국 콩비지 두유 두부 검은콩 "
        "콩비지찌개".split()
    ),
    *(
        ("돼지고기", "allergy", food)
        for food in "삼겹살 돼지불고기 앞다릿살 돈육장조림 돼지갈비찜 목살".split()
    ),
    *(
        ("닭고기", "allergy", food)
        for food in "닭고기안심 계육 닭다리살구이 닭가슴살야채죽 닭고기완자 삼계탕".split()
    ),
    *(
        ("쇠고기", "allergy", food)
        for food in "불고기 한우장조림 소고기 쇠고기안심 갈비찜 쇠고기미역국 소고기장조림 "
        "한우다짐육동그랑땡 양지간장불고기".split()
    ),
    *(
        ("고등어", "allergy", food)
        for food in "고등어구이 고등어무조림 등푸른생선 고등어살주먹밥 고등어통조림 고등어캔 "
        "고등어살생선전".split()
    ),
    *(("해산물", "allergy", food) for food in "새우 오징어 문어 참치 멸치볶음".split()),
    *(
        ("우유", "allergy", food)
        for food in "흰우유 밀크 저지방우유 단호박수프 프렌치토스트 치즈스틱 마시는요구르트".split()
    ),
    *(
        ("유당불내증", "chronic_disease", food)
        for food in "우유 흰우유 치즈 달걀치즈말이 플레인요구르트 우유시리얼 크림수프".split()
    ),
]


@pytest.mark.parametrize(
    ("label", "kind", "food"),
    SUMMARY_FOODS,
    ids=[f"{label}-{food}" for label, _, food in SUMMARY_FOODS],
)
def test_요약에_알레르기_음식이_있으면_사전이_막는다(label: str, kind: str, food: str) -> None:
    sentence = f"{food} 반찬을 자주 먹여 보세요."

    # 빠진 식품 이름 없이 사전만으로 막혀야 한다 — 그리고 그 이유가 이 아이의 알레르기여야 한다
    assert check_summary(sentence, (), [allergy(label, kind=kind)], **OLDER).blocked
    assert not check_summary(sentence, (), [], **OLDER).blocked


def test_요약_검사는_빠진_식품의_별칭도_본다() -> None:
    result = check_summary("철분 보충을 위해 달걀을 챙겨 주세요", ("달걀노른자",), [], **OLDER)

    assert result.removed == ("달걀노른자",)
    assert not result.ok


def test_남은_식품만_권하는_요약은_통과한다() -> None:
    result = check_summary(
        "고기·생선·콩 반찬을 조금씩 늘려 보세요.", ("달걀",), [allergy("난류")], **OLDER
    )

    assert result.ok


@pytest.mark.parametrize(
    "sentence",
    [
        "안심하고 먹여도 돼요.",
        "식단이 골고루라 안심되는 한 주였어요.",
        "안심해도 괜찮아요.",
        "보호자를 안심시키는 결과예요.",
        "양파와 양념은 조금만 넣어 주세요.",
        "우선 지금처럼 골고루 먹여 보세요.",  # 띄어쓰기를 지우면 "선지" 가 나온다
    ],
)
@pytest.mark.parametrize("label", ["쇠고기", "돼지고기"])
def test_고기_부위와_소리가_같은_말은_막지_않는다(label: str, sentence: str) -> None:
    # "안심" 은 소 · 돼지 부위라 사전에 넣었고, 동사로 쓴 "안심하고" 같은 말은 guard 가 뺀다.
    # 소의 위(양)는 한 글자라 사전에 넣지 않았다 — 양파 · 양념에 걸린다
    assert check_summary(sentence, (), [allergy(label)], **OLDER).ok


@pytest.mark.parametrize(
    "sentence",
    ["안심을 잘게 다져 주세요.", "안심이 부드러워 먹기 좋아요.", "이 정도면 안심이 돼요."],
)
@pytest.mark.parametrize("label", ["쇠고기", "돼지고기"])
def test_조사가_붙은_안심은_고기로_보고_막는다(label: str, sentence: str) -> None:
    # "안심을 · 안심이" 는 고기 안심에도 붙는다. guard 로 빼면 "안심을 잘게 다져" 가 새서, 마지막
    # 문장처럼 고기가 아닌 뜻도 막는 쪽으로 둔다 — 잘못 막으면 요약 하나를 거절할 뿐이다
    assert check_summary(sentence, (), [allergy(label)], **OLDER).blocked


def test_요약_검사는_연령_금지_식품도_막는다() -> None:
    # 30개월 전에는 떡을 막는다 — 요약이 권해도 같다
    assert check_summary("떡국을 먹여 보세요.", (), [], **TODDLER).blocked


def test_알레르기가_없고_흔한_음식이면_통과한다() -> None:
    assert check_summary("시금치나물과 현미밥을 곁들여 보세요.", (), [], **OLDER).ok
