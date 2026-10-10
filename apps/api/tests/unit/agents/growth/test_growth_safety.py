"""Growth 안전 필터 검증.

- 음식 용어: 교육 · 루틴 후보에 나오면 **아이 · 월령 · 동의와 무관하게** 뺀다.
- 등록한 이름: 교육만, `active` 행만. 대응표는 없고 식품 사전에 없는 알레르기 이름(꽃가루 ·
  라텍스)에는 확인 문구가 붙는다. `environmental`(고소공포 같은 것)에는 붙지 않는다.
- 위험 용어: 차단은 뺀다. 경고는 후보와 함께 나간다 — **0–17개월은 경고도 차단이다**
  (Activity 와 같다).
- 사전을 못 읽으면 빈 사전으로 통과시키지 않는다.

앞쪽은 가짜 매처로 판정 흐름만 본다(매처는 주입된다). 뒤쪽 `TestDefaultRules` 는 실제 사전과
공용 판정(`read_label` · `level_at`)으로 만든 `load_safety_rules()` 를 본다.
"""

import pytest

from app.agents.growth.store.ports import SafetyEntry
from app.agents.growth.tools.safety import (
    HazardHit,
    SafetyRules,
    SafetyRulesUnavailable,
    SafetyVerdict,
    allergy_cautions,
    check_learning_candidate,
    check_routine_candidate,
    load_safety_rules,
)
from app.rules.term_match import Term


def fake_rules(
    *,
    food: tuple[str, ...] = (),
    hazards: dict[str, HazardHit] | None = None,
    log: list[str] | None = None,
) -> SafetyRules:
    """`food` 에 든 낱말이 나오면 걸리고, `hazards` 의 키가 나오면 그 위험이 걸린다."""
    hazards = hazards or {}

    def scan_food(text: str) -> tuple[str, ...]:
        if log is not None:
            log.append(f"food:{text}")
        return tuple(word for word in food if word in text)

    def scan_hazard(text: str, months: int) -> tuple[HazardHit, ...]:
        if log is not None:
            log.append(f"hazard:{text}")
        return tuple(hit for word, hit in hazards.items() if word in text)

    def scan_names(text: str, entries):
        if log is not None:
            log.append(f"names:{text}")
        return tuple(entry for entry in entries if entry.label in text)

    return SafetyRules(
        food_terms=scan_food,
        hazards=scan_hazard,
        registered_names=scan_names,
    )


BLOCK = HazardHit(label="구슬", axis="small_parts", level="block", warning_text=None)
WARN = HazardHit(label="물놀이", axis="water", level="warn", warning_text="물 조심")
WARN_SAME = HazardHit(label="수영장", axis="water", level="warn", warning_text="물 조심")
ACTIVE_LATEX = SafetyEntry("allergy", "라텍스", "active")


def learn(texts, *, entries=(), months=30, rules=None) -> SafetyVerdict:
    return check_learning_candidate(
        texts, months=months, entries=entries, rules=rules or fake_rules()
    )


class TestLearning:
    def test_아무것도_안_걸리면_그대로_통과한다(self):
        assert learn(["블록 쌓기", "높이 비교하기"]) == SafetyVerdict(allowed=True)

    def test_음식_용어는_건강정보와_상관없이_뺀다(self):
        verdict = learn(["콩 세기"], rules=fake_rules(food=("콩",)))
        assert verdict == SafetyVerdict(allowed=False, removed_by="food_term")

    def test_음식_용어는_등록한_알레르기가_없어도_동의가_없어도_뺀다(self):
        """entries 는 동의가 없으면 비어 있다 — 그래도 음식 용어는 돈다."""
        verdict = learn(["밀가루 반죽"], entries=(), rules=fake_rules(food=("밀가루",)))
        assert verdict.removed_by == "food_term"

    def test_후보의_필드를_이어_붙이지_않고_따로_대조한다(self):
        log: list[str] = []
        learn(["밀", "가루", "점토"], rules=fake_rules(food=("밀가루",), log=log))
        assert [line for line in log if line.startswith("food:")] == [
            "food:밀",
            "food:가루",
            "food:점토",
        ]

    def test_등록한_이름이_문장에_나오면_뺀다(self):
        verdict = learn(["라텍스 장갑 끼기"], entries=[ACTIVE_LATEX])
        assert verdict == SafetyVerdict(allowed=False, removed_by="allergy_name")

    def test_이름이_다르면_걸리지_않는다_대응표는_없다(self):
        """라텍스로 등록했어도 "풍선" 은 이름이 달라 코드가 막지 않는다 (#261 리뷰 4번)."""
        assert learn(["풍선으로 숫자 세기"], entries=[ACTIVE_LATEX]).allowed

    @pytest.mark.parametrize("status", ["retracted", "none"])
    def test_active_가_아닌_행은_막지_않는다(self, status):
        entry = SafetyEntry("allergy", "라텍스", status)
        assert learn(["라텍스 장갑 끼기"], entries=[entry]).allowed

    def test_식품이_아닌_알레르기도_이름으로_대조한다(self):
        entry = SafetyEntry("allergy", "꽃가루", "active")
        assert learn(["꽃가루 모으기"], entries=[entry]).removed_by == "allergy_name"

    def test_차단_월령이면_뺀다(self):
        verdict = learn(["구슬 꿰기"], rules=fake_rules(hazards={"구슬": BLOCK}))
        assert verdict == SafetyVerdict(allowed=False, removed_by="hazard_block")

    def test_경고는_후보와_함께_나간다(self):
        verdict = learn(["물놀이 하기"], rules=fake_rules(hazards={"물놀이": WARN}))
        assert verdict == SafetyVerdict(allowed=True, cautions=("물 조심",))

    def test_같은_경고_문구는_한_번만_붙는다(self):
        rules = fake_rules(hazards={"물놀이": WARN, "수영장": WARN_SAME})
        assert learn(["물놀이", "수영장 가기"], rules=rules).cautions == ("물 조심",)

    def test_경고와_차단이_같이_걸리면_차단이_이긴다(self):
        rules = fake_rules(hazards={"물놀이": WARN, "구슬": BLOCK})
        assert learn(["물놀이 하기", "구슬 꿰기"], rules=rules).removed_by == "hazard_block"

    def test_음식_용어가_먼저_뺀다(self):
        rules = fake_rules(food=("콩",), hazards={"콩": BLOCK})
        assert learn(["콩 세기"], rules=rules).removed_by == "food_term"

    def test_위험_용어_판정에_월령을_그대로_넘긴다(self):
        seen: list[int] = []

        def scan_hazard(text: str, months: int):
            seen.append(months)
            return ()

        rules = SafetyRules(
            food_terms=lambda text: (),
            hazards=scan_hazard,
            registered_names=lambda text, entries: (),
        )
        learn(["블록 쌓기"], months=15, rules=rules)
        assert seen == [15]  # 승격은 주입된 위험 판정이 한다 — 이 함수는 월령을 그대로 넘긴다


class TestRoutine:
    def test_음식_용어만_본다(self):
        log: list[str] = []
        rules = fake_rules(food=("콩",), hazards={"콩": BLOCK}, log=log)
        assert check_routine_candidate(["콩 먹기 연습"], rules=rules).removed_by == "food_term"
        assert not any(line.startswith(("hazard:", "names:")) for line in log)

    def test_음식이_없으면_위험_용어가_있어도_통과한다(self):
        """루틴은 건강정보도 위험 용어도 읽지 않는다."""
        rules = fake_rules(hazards={"구슬": BLOCK})
        assert check_routine_candidate(["구슬 정리하기"], rules=rules).allowed


class TestVerdict:
    def test_뺐으면_이유가_있다(self):
        with pytest.raises(ValueError):
            SafetyVerdict(allowed=False)

    def test_통과했는데_이유가_있으면_거부한다(self):
        with pytest.raises(ValueError):
            SafetyVerdict(allowed=True, removed_by="food_term")


class TestNonFoodAllergyCaution:
    """식품 사전에 없는 알레르기 이름에 확인 문구를 붙인다 (#282 리뷰).

    식품 알레르기는 이름 대조와 음식 용어 스캔이 뺀다. 식품이 아닌 것(꽃가루 · 라텍스)은 뺄 활동을
    코드가 못 정해서, 보호자가 적은 이름을 보여 주고 확인하게 한다. 어느 행에 붙일지는 Activity 와
    같은 공용 판정(`non_food_allergies`)이 고른다 — 판정 사례는
    tests/unit/agents/common/test_allergy.py.
    """

    POLLEN = "등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요."

    def test_식품_사전에_없는_알레르기에_확인_문구를_낸다(self):
        assert allergy_cautions([SafetyEntry("allergy", "꽃가루", "active")]) == (self.POLLEN,)

    def test_environmental_은_알레르기가_아니라_문구를_내지_않는다(self):
        # data_model.md — environmental 은 고소공포 같은 것이다.
        # "등록된 알레르기(고소공포)" 가 되면 안 된다
        assert allergy_cautions([SafetyEntry("environmental", "고소공포", "active")]) == ()

    def test_식품이_아닌_알레르기에만_적은_이름_그대로_붙인다(self):
        entries = [
            SafetyEntry("environmental", "고소공포", "active"),
            SafetyEntry("allergy", "꽃가루", "active"),
            SafetyEntry("allergy", "우유", "active"),
            SafetyEntry("allergy", "라텍스 알레르기", "active"),
        ]
        assert allergy_cautions(entries) == (
            self.POLLEN,
            "등록된 알레르기(라텍스 알레르기)가 있어요. 장소랑 재료를 한 번 확인해 주세요.",
        )

    def test_어디가_위험한지는_말하지_않는다(self):
        (text,) = allergy_cautions([SafetyEntry("allergy", "꽃가루", "active")])
        assert "공원" not in text and "위험" not in text


class TestDefaultRules:
    """실제 사전(`allergen_terms.yaml` · `hazard_terms.yaml`)과 공용 판정으로 만든 규칙."""

    @pytest.fixture
    def rules(self) -> SafetyRules:
        return load_safety_rules()

    @pytest.mark.parametrize(
        "text",
        ["밀가루 점토 만들기", "계란 껍질 붙이기", "콩나물 기르기", "우유팩 쌓기", "팝콘 던지기"],
    )
    def test_음식_용어를_찾는다(self, rules, text):
        assert rules.food_terms(text)

    @pytest.mark.parametrize("text", ["공 밀기", "카드 게임", "블록 쌓기", "비밀 상자 열기"])
    def test_한_글자_별칭은_놀이_문장에서_쓰지_않는다(self, rules, text):
        assert rules.food_terms(text) == ()

    def test_18개월_미만은_경고도_차단이다(self, rules):
        hits = rules.hazards("풍선으로 숫자 세기", 15)
        assert [(h.axis, h.level) for h in hits] == [("suffocation_film", "block")]
        assert hits[0].warning_text is None  # 뺄 후보라 경고 문구가 따라 나가지 않는다

    @pytest.mark.parametrize(("months", "level"), [(17, "block"), (18, "warn")])
    def test_승격은_17개월까지다(self, rules, months, level):
        assert [h.level for h in rules.hazards("대야에 물 붓기 놀이", months)] == [level]

    def test_작은_부품은_36개월_미만이_차단_이상이_경고다(self, rules):
        assert [h.level for h in rules.hazards("구슬 꿰기", 20)] == ["block"]
        (warn,) = rules.hazards("구슬 꿰기", 40)
        assert warn.level == "warn" and warn.warning_text

    def test_15개월_물놀이는_뺀다(self, rules):
        # 같은 아이에게 놀이 탭은 막고 교육 탭은 내보내지 않는다 — 선은 Activity 와 하나다
        # (#282 리뷰)
        verdict = check_learning_candidate(
            ["대야에 물 붓기 놀이"], months=15, entries=[], rules=rules
        )
        assert verdict == SafetyVerdict(allowed=False, removed_by="hazard_block")

    def test_18개월부터_물놀이는_경고와_함께_남는다(self, rules):
        verdict = check_learning_candidate(
            ["대야에 물 붓기 놀이"], months=18, entries=[], rules=rules
        )
        assert verdict.allowed
        assert verdict.cautions and "물놀이" in verdict.cautions[0]

    def test_위험_용어가_없으면_빈_튜플이다(self, rules):
        assert rules.hazards("블록 쌓기", 15) == ()

    ENTRIES = (
        SafetyEntry("allergy", "밀", "active"),
        SafetyEntry("allergy", "라텍스 알레르기", "active"),
        SafetyEntry("allergy", "쑥", "active"),
        SafetyEntry("allergy", "꽃가루", "active"),
    )

    @pytest.mark.parametrize(
        ("text", "label"),
        [
            ("밀가루 점토 만들기", "밀"),  # 19종은 사전 별칭으로 넓힌다
            ("라텍스 장갑 끼기", "라텍스 알레르기"),  # 꼬리말을 뗀 이름으로 찾고, 행은 적은 그대로
            ("쑥 캐기", "쑥"),  # 19종 밖은 한 글자여도 적은 그대로
            ("꽃가루 모으기", "꽃가루"),
        ],
    )
    def test_등록한_이름이_나오면_그_행이_걸린다(self, rules, text, label):
        assert [e.label for e in rules.registered_names(text, self.ENTRIES)] == [label]

    @pytest.mark.parametrize("text", ["공 밀기", "풍선 불기", "꽃 이름 익히기", "블록 쌓기"])
    def test_이름이_없으면_걸리지_않는다(self, rules, text):
        assert rules.registered_names(text, self.ENTRIES) == ()

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("키위, 꽃가루", "꽃가루 관찰하기"),
            ("라텍스/꽃가루", "라텍스 장갑 끼기"),
            ("키위 알레르기, 꽃가루 알레르기", "꽃가루 관찰하기"),
        ],
    )
    def test_한_칸에_적은_이름을_조각마다_대조한다(self, rules, label, text):
        """칸 전체를 이름 하나로 보면 "키위꽃가루" 를 찾게 되어 꽃가루 놀이가 그대로 나간다
        (#283 5번 · 공용 `read_label`)."""
        verdict = check_learning_candidate(
            [text], months=30, entries=[SafetyEntry("allergy", label, "active")], rules=rules
        )
        assert verdict == SafetyVerdict(allowed=False, removed_by="allergy_name")

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("갑각류", "대게 그림 맞추기"),  # 묶음 이름은 19종 여럿(게 · 새우)으로 펼친다
            ("갑각류", "갑각류 그림 카드"),  # 묶음 이름 그 글자로도 본다
            ("우유, 계란", "메추리알 그림 맞추기"),  # 19종 둘을 한 칸에
        ],
    )
    def test_묶음_이름과_19종_여럿도_사전으로_읽는다(self, rules, label, text):
        entries = [SafetyEntry("allergy", label, "active")]
        assert [e.label for e in rules.registered_names(text, entries)] == [label]

    def test_띄어_쓴_이름은_낱말로도_대조한다(self, rules):
        """ "고양이 털" 로 등록한 아이에게 고양이 쓰다듬기가 나가면 안 된다 — 칸 전체만 보면
        놓친다."""
        entries = [SafetyEntry("allergy", "고양이 털", "active")]
        matched = rules.registered_names("고양이 쓰다듬기", entries)
        assert [e.label for e in matched] == ["고양이 털"]

    @pytest.mark.parametrize(
        ("kind", "label", "text"),
        [
            ("allergy", "고양이 털", "털실 공 굴리기"),
            ("environmental", "높은 곳", "곳곳에 숨긴 카드 찾기"),
        ],
    )
    def test_띄어_쓴_이름에서_떨어진_한_글자로는_막지_않는다(self, rules, kind, label, text):
        """한 글자는 보호자가 그 글자만 적었을 때(쑥)만 본다. 털 · 곳으로 막으면 과차단이다 —
        한 글자 판정은 #305 에서 Food · Activity 와 맞춘다."""
        assert rules.registered_names(text, [SafetyEntry(kind, label, "active")]) == ()

    def test_밀_등록_아이의_공_밀기는_통과한다(self, rules):
        """한 글자 별칭 정책 — #261. "밀" 은 사전의 두 글자 이상 별칭(밀가루 …)으로만 잡는다."""
        verdict = check_learning_candidate(
            ["공 밀기"], months=30, entries=[SafetyEntry("allergy", "밀", "active")], rules=rules
        )
        assert verdict.allowed

    def test_잣_등록_아이의_잣_까기는_통과한다_알려진_비대칭이다(self, rules):
        """#261 에서 열어 둔 질문 — 한 글자만 쓴 문장은 놓친다. 두 글자 이상 별칭은 잡는다."""
        entries = [SafetyEntry("allergy", "잣", "active")]
        assert rules.registered_names("잣 까기", entries) == ()
        assert rules.registered_names("잣가루 뿌리기", entries)

    @pytest.mark.parametrize(
        "text",
        [
            "포크로 찍어 먹기",
            "에그 셰이커 흔들기",
            "크랩 워크로 옆으로 걷기",
            "피치 높낮이 따라 부르기",
            "비프 소리 흉내 내기",
        ],
    )
    def test_외래어_별칭은_문장에서_쓰지_않는다(self, rules, text):
        """#264 가 공용 사전에 넣은 외래어 별칭이 Growth 문장에서 다른 뜻으로 걸리지 않게 한다."""
        assert rules.food_terms(text) == ()


# 공용 사전이 검사지 이름을 읽으려고 둔 외래어 별칭(#264)과 같은 모양의 가짜 항목
PORK = Term(key="10", aliases=("돼지고기", "돈육", "포크", "pork"))


class TestLoanwordAliases:
    """외래어 별칭은 Growth 의 문장 스캔에서만 뺀다 — 공용 사전은 그대로다."""

    @pytest.fixture
    def rules(self, monkeypatch) -> SafetyRules:
        monkeypatch.setattr("app.agents.growth.tools.safety.allergen_terms", lambda: (PORK,))
        return load_safety_rules()

    @pytest.mark.parametrize("text", ["포크로 찍어 먹기", "Pork 라고 적힌 카드 고르기"])
    def test_음식_용어_스캔에서_뺀다(self, rules, text):
        assert rules.food_terms(text) == ()

    def test_한국어_이름은_그대로_찾는다(self, rules):
        assert rules.food_terms("돈육 그림 카드") == ("10",)

    def test_외래어로_등록한_이름도_한국어_별칭으로_찾는다(self, rules):
        """보호자가 검사지 영어 이름(pork)으로 적었으면 그 항목의 한국어 별칭으로 본다."""
        entries = [SafetyEntry("allergy", "pork", "active")]
        assert [e.label for e in rules.registered_names("돼지고기 그림 카드", entries)] == ["pork"]
        assert rules.registered_names("포크로 찍어 먹기", entries) == ()


class TestRulesUnavailable:
    def test_위험_사전을_못_읽으면_빈_사전으로_통과시키지_않는다(self, monkeypatch):
        def broken():
            raise ValueError("hazard_terms.yaml 깨짐")

        monkeypatch.setattr("app.agents.growth.tools.safety.hazard_terms", broken)
        with pytest.raises(SafetyRulesUnavailable):
            load_safety_rules()

    def test_알레르기_사전을_못_읽어도_같다(self, monkeypatch):
        def broken():
            raise OSError("allergen_terms.yaml 없음")

        monkeypatch.setattr("app.agents.growth.tools.safety.allergen_terms", broken)
        with pytest.raises(SafetyRulesUnavailable):
            load_safety_rules()

    def test_이름_사전을_못_읽어도_같다(self, monkeypatch):
        """확인 문구를 고르는 이름 사전(`shared_book`)도 게이트에서 먼저 읽는다 — 못 읽으면 문구
        없이 넘기지 않는다."""

        def broken():
            raise ValueError("allergen_groups.yaml 깨짐")

        monkeypatch.setattr("app.agents.growth.tools.safety.shared_book", broken)
        with pytest.raises(SafetyRulesUnavailable):
            load_safety_rules()

    def test_원인을_잃지_않는다(self, monkeypatch):
        monkeypatch.setattr(
            "app.agents.growth.tools.safety.hazard_terms",
            lambda: (_ for _ in ()).throw(ValueError("깨짐")),
        )
        with pytest.raises(SafetyRulesUnavailable) as caught:
            load_safety_rules()
        assert isinstance(caught.value.__cause__, ValueError)
