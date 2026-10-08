"""Growth 안전 필터 검증.

- 음식 용어: 교육 · 루틴 후보에 나오면 **아이 · 월령 · 동의와 무관하게** 뺀다.
- 등록한 이름: 교육만, `active` 행만. 대응표는 없고 환경 알레르기에는 확인 문구가 붙는다.
- 위험 용어: 차단은 뺀다. 경고는 후보와 함께 나간다 — **18개월 미만 승격은 하지 않는다.**
- 사전을 못 읽으면 빈 사전으로 통과시키지 않는다.

앞쪽은 가짜 매처로 판정 흐름만 본다(매처는 주입된다). 뒤쪽 `TestDefaultRules` 는 develop 의 실제
사전으로 만든 임시 구현이고, #264 · #261 이 들어가 공통 함수로 바꾸면 그쪽 케이스를 더한다.
"""

import pytest

from app.agents.growth.store.ports import SafetyEntry
from app.agents.growth.tools.safety import (
    HazardHit,
    SafetyRules,
    SafetyRulesUnavailable,
    SafetyVerdict,
    check_learning_candidate,
    check_routine_candidate,
    environmental_cautions,
    load_safety_rules,
)


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

    return SafetyRules(food_terms=scan_food, hazards=scan_hazard, registered_names=scan_names)


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

    def test_환경_알레르기도_이름으로_대조한다(self):
        entry = SafetyEntry("environmental", "꽃가루", "active")
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
        assert seen == [15]  # 승격은 판정 함수 안에도 없다 — 월령 인자 하나뿐이다


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


class TestEnvironmentalCaution:
    def test_active_환경_알레르기에_확인_문구를_낸다(self):
        entries = [SafetyEntry("environmental", "꽃가루", "active")]
        assert environmental_cautions(entries) == (
            "등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요.",
        )

    def test_식품_알레르기_취소한_행은_문구를_내지_않는다(self):
        entries = [
            SafetyEntry("allergy", "라텍스", "active"),
            SafetyEntry("environmental", "동물털", "retracted"),
            SafetyEntry("environmental", "먼지", "none"),
        ]
        assert environmental_cautions(entries) == ()

    def test_같은_이름은_한_번만_낸다(self):
        entries = [
            SafetyEntry("environmental", "꽃가루", "active"),
            SafetyEntry("environmental", "꽃 가루", "active"),
            SafetyEntry("environmental", "동물털", "active"),
        ]
        assert len(environmental_cautions(entries)) == 2

    def test_어디가_위험한지는_말하지_않는다(self):
        entries = [SafetyEntry("environmental", "꽃가루", "active")]
        (text,) = environmental_cautions(entries)
        assert "공원" not in text and "위험" not in text


class TestDefaultRules:
    """develop 의 사전(`allergen_terms.yaml` · `hazard_terms.yaml`)으로 만든 임시 구현."""

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

    def test_18개월_미만에도_경고는_경고다_승격하지_않는다(self, rules):
        hits = rules.hazards("풍선으로 숫자 세기", 15)
        assert [(h.axis, h.level) for h in hits] == [("suffocation_film", "warn")]
        assert hits[0].warning_text

    def test_작은_부품은_36개월_미만이_차단_이상이_경고다(self, rules):
        assert [h.level for h in rules.hazards("구슬 꿰기", 20)] == ["block"]
        (warn,) = rules.hazards("구슬 꿰기", 40)
        assert warn.level == "warn" and warn.warning_text

    def test_15개월_물놀이는_차단되지_않고_경고와_함께_남는다(self, rules):
        verdict = check_learning_candidate(
            ["대야에 물 붓기 놀이"], months=15, entries=[], rules=rules
        )
        assert verdict.allowed
        assert verdict.cautions and "물놀이" in verdict.cautions[0]

    def test_위험_용어가_없으면_빈_튜플이다(self, rules):
        assert rules.hazards("블록 쌓기", 15) == ()

    ENTRIES = (
        SafetyEntry("allergy", "밀", "active"),
        SafetyEntry("allergy", "라텍스 알레르기", "active"),
        SafetyEntry("allergy", "쑥", "active"),
        SafetyEntry("environmental", "꽃가루", "active"),
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

    def test_원인을_잃지_않는다(self, monkeypatch):
        monkeypatch.setattr(
            "app.agents.growth.tools.safety.hazard_terms",
            lambda: (_ for _ in ()).throw(ValueError("깨짐")),
        )
        with pytest.raises(SafetyRulesUnavailable) as caught:
            load_safety_rules()
        assert isinstance(caught.value.__cause__, ValueError)
