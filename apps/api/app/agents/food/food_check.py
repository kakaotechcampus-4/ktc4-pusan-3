"""글에 든 음식 낱말을 아이의 등록 알레르기 · 연령 규칙과 대조한다 — 섭취 확인의 판정.

세 곳이 이 판정 하나를 같이 쓴다. "땅콩소스 먹여도 될까?" 에 답하는 `food_check`, 식단 추천의 요청
글에 알레르기 음식이 있을 때의 안내, 추천 이유(reason)에 막힌 음식이 들어갔는지 보는 사후 검사.
모델을 부르지 않는다 — 음식 낱말도 사전으로 찾는다.

- 겹침은 두 길로 보고 합친다. ① 낱말을 보호자가 등록한 이름의 사전(`read_label`)으로 펼쳐서
  등록 알레르기와 대조한다. "견과류" 를 등록했으면 땅콩 · 호두 · 잣이 겹치고, 땅콩을 등록했으면
  "견과류" 를 물을 때 겹친다. 메뉴를 거르는 `filter_food_safety` 에 이름만 넣으면 이 펼치기가 없어서
  "견과류" 가 통과한다. ② 낱말이 든 어절(조사를 뗀 것)을 메뉴 행처럼 `filter_food_safety` 에 등록
  행과 같이 건다. 낱말 찾기는 더 긴 낱말 안에 든 낱말을 빼고 헷갈리는 낱말은 합성어 안에서 안
  잡아서, ①만 보면 흰우유의 우유 · 호두과자의 과자 · 에그타르트의 에그 · 굴국밥의 굴을 놓친다
  (#575 리뷰). 제품마다 다른 성분으로만 막힌 것(`needs_check`)은 겹침으로 보지 않는다 — 재료
  확인 안내가 그 몫이다.
- 연령 규칙은 낱말마다 본다. 낱말이 든 어절(조사를 뗀 것 — "우유를" 은 생우유 규칙이 못 본다)을
  `filter_food_safety` 에 넣어 guard("땅콩소스" 는 갈아 만든 것이라 질식 주의가 아니다)를 지키되,
  그 낱말에 해당하는 규칙만 받는다 — "꿀떡" 의 꿀이 떡의 48개월 규칙을 물려받지 않게.
- 사전 낱말이 하나도 없는 음식(전복죽)은 먹이는 말 바로 앞의 어절을 같은 메뉴 필터에 건다.
  일상어("어떻게" 의 게)가 걸리지 않게 그 자리 · 부사 · 어미로 거르고, 막힐 때만 판정으로 남긴다 —
  안 막히면 지금처럼 알아보지 못했다고 말한다.
- 글에서 음식 낱말을 찾을 때는 문장에 맞게 거른다. 메뉴 이름을 거르는 사전을 그대로 쓰면 "어떻게"
  의 "게", "오늘 밤" 의 "밤", "배탈" 의 "배", "새싹반" 의 "새싹" 이 음식으로 잡혀 엉뚱한 "겹쳐요"
  가 나오고 추천 이유가 거절된다. 그래서 두 가지로 막는다 — 헷갈리는 낱말(`AMBIGUOUS_ALIASES`)은
  어절 전체(조사만 붙은 것)이고 먹이는 말이 곁에 있을 때만 음식으로 보고, 그 밖의 낱말은 일상어
  합성어 guard(`QUESTION_GUARDS`)를 더해 훑는다. 사전을 일상어로 전수 검사할 방법은 없다 — 두
  목록은 찾은 만큼 늘린다.
- 빼 달라는 말("견과류 빼고" · "땅콩 없는" · "우유 대신")과 알레르기를 말하는 말("복숭아 알레르기
  있는데")에 나온 음식은 물은 음식이 아니다. 같은 음식이 다른 자리에서 다시 나오면 본다.
- 🚨 못 찾았다는 것은 괜찮다는 뜻이 아니다. 사전에 없는 음식(마라탕)과 여러 재료가 든 음식
  (치킨의 튀김옷)은 낱말이 안 잡히거나 재료를 모른다. 낱말을 하나도 못 찾으면 확인하지 못했다고
  말하고, 찾았어도 재료 확인 안내를 늘 붙인다.
- 질식 주의(`caution`)는 섭취 확인 답에만 한 줄 낸다(#575 PM 결정) — 보호자가 그 음식을 콕 집어
  묻는 곳이라, 말하지 않으면 "알레르기만 못 봤고 나머지는 괜찮다" 로 읽힌다. 추천 목록 · 급식 ·
  요청 안내에는 내지 않는다(10-13 결정 그대로).
- 문구의 조사는 받침에 맞춘다("땅콩소스는 · 견과류 알레르기와"). 한글로 끝나지 않으면 받침을
  몰라 "은(는)" 처럼 괄호로 둔다.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache, lru_cache
from types import MappingProxyType

from app.agents.common.allergy import read_label, split_particle
from app.agents.common.readout import Readout
from app.agents.common.reference import (
    AgeRule,
    allergen_terms,
    chronic_restriction_terms,
    food_safety_terms,
)
from app.agents.food.readouts import (
    CHECK_AGE_BLOCK,
    CHECK_ASK_FOOD,
    CHECK_CHOKING,
    CHECK_CONFLICT,
    CHECK_INGREDIENTS_CAVEAT,
    CHECK_NO_CONSENT,
    CHECK_NONE_REGISTERED,
    CHECK_NOT_FOUND,
    CHECK_PEANUT_IN_NUTS,
    CHECK_UNKNOWN_FOOD,
    CHECK_UNKNOWN_FOOD_PLAIN,
    MEAL_REQUEST_AGE_BLOCK,
    MEAL_REQUEST_CONFLICT,
    READOUTS,
)
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import (
    INACTIVE_STATUSES,
    SINGLE_CHAR_FOODS,
    filter_food_safety,
    food_book,
    resolve_safety,
)
from app.rules.age import Stage
from app.rules.particle import has_final, topic_particle
from app.rules.term_match import Term, TermMatcher, normalize, prepare_fields

# 이름만으로는 음식인지 알 수 없는 낱말 — 어절 전체이고 먹이는 말이 곁에 있을 때만 음식으로 본다.
# 문장에서 어떻게 · 먹게 · 비밀 · 깨고 · 오늘 밤 · 대하여 · 안심하고 · 구미에 · 대게(대개) 가
# 음식으로 잡힌 낱말들이다. 보호자가 등록한 한 글자 이름(배 · 감 · 마 …)도 같은 규칙을 받는다.
# 밥 · 빵 · 떡 · 닭 · 쌀 같은 한 글자 *음식* 은 넣지 않는다 — 식빵 · 꿀떡 · 닭강정처럼 합성어에서
# 겹침을 잡는 쪽이 더 안전하다. 그 대신 일상어 합성어는 `QUESTION_GUARDS` 가 막는다
AMBIGUOUS_ALIASES = frozenset(
    {
        "게", "밀", "깨", "밤", "굴",
        "구미", "대하", "미소", "사태", "조기", "포크", "에그", "밀크", "피치", "은행", "전복",
        "돼지", "자장", "중하", "안심", "대구", "방어", "전란", "대합", "대게",
    }
)  # fmt: skip
# 철자가 틀린 별칭 — 문장에서는 늘 "잦은" 으로 읽힌다
EXCLUDED_ALIASES = frozenset({"잦"})
# 낱말 그대로 훑는 별칭에 더하는 guard — 일상어 합성어. guard 는 그 자리를 덮을 때만 막는다
QUESTION_GUARDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "꿀": (
            "꿀잠", "꿀팁", "꿀맛", "꿀템", "꿀벌", "꿀꿀", "꿀잼", "꿀피부", "꿀조합", "꿀알바",
        ),
        "쌀": ("쌀쌀",),
        "껌": ("껌딱지", "껌값"),
        "새싹": ("새싹반", "새싹어린이집", "새싹유치원", "새싹교실"),
        "소주": ("소주제",),
        "사과": ("사과했", "사과하", "사과해", "사과문", "사과드", "사과받"),
        "닭": ("닭살",),
        "잣": ("잣대",),
        "떡": ("떡잎",),
        "빵": ("빵빵", "빵점", "빵터"),
        "콘": ("콘센트", "콘서트", "콘텐츠", "리모콘", "콘테스트"),
        "햄": ("햄스터",),
        "램": ("램프",),
        "백미": ("백미러",),
        "콩": ("콩닥",),
    }
)  # fmt: skip

# 헷갈리는 낱말 뒤에 붙어도 음식으로 쓰인 것으로 보는 조사(`split_particle` 이 받침을 본다).
# 이 · 가는 시간 · 장소 뜻이 많아("밤이 늦었는데") 바로 다음 어절이 먹이는 말일 때만 본다.
# 하고 · 에서 · 에 · 로는 넣지 않는다 — "안심하고" · "구미에" · "대구에서" 가 음식이 된다
_WEAK_PARTICLES = frozenset({"은", "는", "을", "를", "과", "와", "랑", "이랑", "이", "가"})
_SUBJECT_PARTICLES = frozenset({"이", "가"})
# 음식으로 쓰였다는 단서 — 곁(뒤 두 어절)에 이런 말이 있어야 한다
_FEEDING = (
    "먹", "주", "줘", "줄", "드", "넣", "들어", "섞", "곁들", "얹", "뿌", "추천", "사용",
    "만들", "만든",
)  # fmt: skip
# 앞 어절이 시간이면 음식이 아니다 ("오늘 밤 우유 먹여도 돼?")
_TIME_BEFORE = frozenset(
    {
        "오늘", "어제", "내일", "어젯", "엊그제", "그제", "지난", "이번", "매일",
        "늦은", "깊은", "이른", "새벽",
    }
)  # fmt: skip
# 이 말이 음식 뒤에 오면 그 음식은 빼 달라는 것이거나 알레르기를 말하는 것이다. "빼" 는 어미까지
# 본다 — "땅콩빼빼로" 의 빼빼로는 빼 달라는 말이 아니다. "빼" 한 글자만 따로 쓴 것
# ("땅콩 빼 주세요")도 빼 달라는 말이다(`_excluding`)
_EXCLUDING = (
    "빼고", "빼서", "빼면", "빼줘", "빼주", "빼달", "빼야", "빼는", "뺀", "뺄",
    "말고", "없", "제외", "대신", "알레르기", "알러지", "피해", "피하",
)  # fmt: skip
# 이 말이 음식 바로 뒤에 붙으면 빗댄 말이다("꿀처럼 달콤한") — 추천 이유에서 연령 규칙으로는
# 막지 않는다
_LIKENING = ("처럼", "같이", "같은", "만큼")
# 사전 낱말이 없는 어절을 메뉴 필터로 다시 볼 때 — 먹이는 말 앞에 와도 음식이 아닌 말. 부사 ·
# 의문사 · 때를 가리키는 말이다("이거 어떻게 먹여도 돼?" 의 "어떻게" 는 게 알레르기와 겹치지 않는다)
_NOT_FOOD = frozenset(
    {
        "좀", "조금", "많이", "같이", "한번", "한", "또", "더", "다", "잘", "혹시", "그냥", "자주",
        "계속", "바로", "먼저", "이제", "지금", "아직", "벌써", "어떻게", "얼마나", "얼만큼",
        "언제", "왜", "몇", "어떤", "무슨", "아무거나", "아무",
    }
)  # fmt: skip
# 이 어미로 끝나는 어절은 음식이 아니다 — "으깨서" · "밀어서" · "먹게" · "어떻게"
_VERB_ENDINGS = ("서", "고", "게", "히", "니까", "는데", "도록", "려고", "지만")
# 앞 어절과 음식을 잇는 조사 — "전복죽이랑 사과 먹여도 돼?" 의 전복죽도 먹이는 음식이다
_JOINING = frozenset({"랑", "이랑", "하고", "와", "과", "나", "이나", "에다"})
_PUNCT = "?!.,~…\"'()"
# 받침과 상관없이 붙는 보조사 — 문구의 어절에서만 뗀다("붕어빵도" → "붕어빵"). 음식 낱말을 찾는
# 데는 쓰지 않는다 — "포도" · "만두" 처럼 이 글자로 끝나거나 시작하는 음식이 있다
_AUXILIARY = ("까지", "부터", "조차", "마저", "도", "만")

# 글이 음식을 가리키는 말만 하고 이름은 안 말한 경우("이거 먹여도 돼?" · "이 간식")
_POINTING = ("이거", "그거", "저거", "이것", "그것", "저것", "이건", "그건", "저건", "요거")
_DETERMINERS = frozenset({"이", "그", "저", "요"})
_GENERIC_FOODS = ("음식", "간식", "메뉴", "반찬", "과자", "것", "거", "밥", "죽")
_PEANUT_CODE = "4"
# 등록 이름 뒤에 보호자가 붙여 적은 꼬리말 — 문구가 "알레르기" 를 다시 붙인다
_LABEL_TAIL = re.compile(r"\s*(?:알레르기|알러지|allergy)\s*$", re.IGNORECASE)

# 판정에 쓰는 가짜 메뉴 행의 시각. 판정은 이 값을 읽지 않는다
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Conflict:
    """등록한 알레르기 · 질환 한 행과 겹친 것."""

    entry: SafetyEntry  # 겹친 등록 행 — 문구가 label · kind 를 쓴다
    codes: frozenset[int]  # 겹친 19종 코드. 글자로만 겹쳤으면 빈 집합


@dataclass(frozen=True)
class FoodVerdict:
    """글에서 찾은 음식 낱말 하나의 판정."""

    word: str  # 글에서 찾은 음식 낱말(사전 키)
    conflicts: tuple[Conflict, ...] = ()
    age_block: AgeRule | None = None  # 막는 연령 규칙
    caution: str | None = None  # 주의 규칙 label. 막히면 None

    @property
    def blocked(self) -> bool:
        """겹치는 등록이 있거나 연령 규칙이 막는다. 주의(`caution`)는 막힌 것이 아니다."""
        return bool(self.conflicts) or self.age_block is not None


def check_foods(
    text: str,
    entries: Sequence[SafetyEntry],
    *,
    stage: Stage,
    months: int,
    every_mention: bool = False,
) -> tuple[FoodVerdict, ...]:
    """글에서 찾은 음식 낱말마다 판정한다. 글에 나온 순서이고, 낱말을 못 찾았으면 빈 튜플이다.

    `entries` 는 `build_gate` 가 run state 에 담은 health_safety 행이다. `retracted` · `none` 은
    읽지 않는다. 동의가 없어 비어 있어도 연령 규칙은 그대로 본다. 빼 달라는 말 · 알레르기를 말하는
    말에만 나온 음식은 판정하지 않는다 — 보호자의 질문 · 요청을 읽을 때다. `every_mention` 이면
    그런 말에 나온 음식도 판정한다. 추천 이유는 "땅콩 대신" 이어도 화면에 그 이름이 나간다.
    """
    book = food_book()
    registered = _registered(tuple(e for e in entries if e.status not in INACTIVE_STATUSES))
    tokens = _tokens(text)

    verdicts: list[FoodVerdict] = []
    words = _words(text, registered.scan)
    for word in words:
        if not every_mention and not _mentioned(tokens, word):
            continue
        asked = book.names.get(word)
        codes = asked.codes if asked is not None else frozenset()
        aliases = (
            word,
            *(alias for term in (asked.terms if asked else ()) for alias in term.aliases),
        )
        prepared = prepare_fields(aliases)
        phrase = _phrase_of(text, word)
        conflicts = tuple(
            Conflict(entry, frozenset(codes & registered_codes) | (menu or frozenset()))
            for entry, registered_codes, matcher in registered.rows
            if (menu := _menu_conflict(phrase, entry, stage, months)) is not None
            or codes & registered_codes
            or matcher.match(prepared)
        )
        verdicts.append(_with_age(word, phrase, conflicts, stage, months))

    for phrase in _unnamed_foods(tokens, words):
        word = normalize(phrase)
        if not every_mention and not _mentioned(tokens, word):
            continue
        conflicts = tuple(
            Conflict(entry, menu)
            for entry, _, _ in registered.rows
            if (menu := _menu_conflict(phrase, entry, stage, months)) is not None
        )
        verdict = _with_age(word, phrase, conflicts, stage, months)
        if verdict.blocked:
            verdicts.append(verdict)

    dense = normalize(text)
    return tuple(
        sorted(verdicts, key=lambda v: dense.find(v.word) if v.word in dense else len(dense))
    )


def _with_age(
    word: str, phrase: str, conflicts: tuple[Conflict, ...], stage: Stage, months: int
) -> FoodVerdict:
    """겹침에 연령 판정을 더한다 — 그 낱말에 해당하는 규칙만. 막히면 주의는 남기지 않는다."""
    rules = _age_rules()
    hits, cautions = _age_hits(phrase, stage, months)
    fitting = [rules[key] for key in hits if key in rules and _rule_fits(rules[key], word)]
    age_block = next((rule for rule in fitting if rule.action == "block"), None)
    caution = None
    if not conflicts and age_block is None:
        caution = next(
            (key for key in cautions if key in rules and _rule_fits(rules[key], word)), None
        )
    return FoodVerdict(word, conflicts, age_block, caution)


def mentions_blocked_food(
    text: str,
    entries: Sequence[SafetyEntry],
    *,
    stage: Stage,
    months: int,
    allow: Collection[str] = (),
) -> bool:
    """글이 아이에게 막힌 음식(등록 알레르기와 겹침 · 연령 규칙이 막음)을 말하는가. 추천 이유를
    검사한다 — 빼 달라는 말("땅콩 대신")에 나온 음식도 센다.

    `allow` 는 말해도 되는 음식이다 — 기피 근거를 인용한 추천 이유는 피한 음식을 적어야 한다
    (`build()` 의 기피 근거 규칙). 그 음식이 알레르기여도 이유에 쓰는 것은 막지 않는다.

    빗댄 말("꿀처럼 달콤한" · "떡처럼 쫀득하지 않아")은 연령 규칙으로는 막지 않는다 — 먹이라는
    말이 아니다. 등록한 알레르기와 겹치는 이름은 빗댄 말이어도 막는다. 화면에 그 이름이 나간다.
    """
    allowed = [name for name in map(normalize, allow) if name]
    tokens = _tokens(text)
    return any(
        (v.conflicts or (v.age_block is not None and not _likened(tokens, v.word)))
        and not any(name in v.word or v.word in name for name in allowed)
        for v in check_foods(text, entries, stage=stage, months=months, every_mention=True)
    )


def _likened(tokens: Sequence[str], word: str) -> bool:
    """낱말이 나온 어절마다 바로 뒤에 "처럼" · "같이" 가 붙었는가."""
    rests = [name[name.find(word) + len(word) :] for name in map(normalize, tokens) if word in name]
    return bool(rests) and all(rest.startswith(_LIKENING) for rest in rests)


@dataclass(frozen=True)
class _Scan:
    """사전으로 훑는 도구 — 낱말 그대로 훑는 매처와, 어절 전체일 때만 보는 낱말."""

    matcher: TermMatcher | None
    weak: Mapping[str, str]  # 정규화한 별칭 → 사전 키


@dataclass(frozen=True)
class _Registered:
    """등록 행으로 미리 만들어 두는 것 — 겹침 대조 매처와 사전 밖 등록 이름의 훑기 도구."""

    rows: tuple[tuple[SafetyEntry, frozenset[int], TermMatcher], ...]
    scan: _Scan


@lru_cache(maxsize=256)
def _registered(active: tuple[SafetyEntry, ...]) -> _Registered:
    """등록 행이 같으면 다시 만들지 않는다 — 추천 하나에서 후보 이유마다 같은 행으로 부른다."""
    book = food_book()
    rows = tuple(
        (entry, read_label(entry.label, book)[0].codes, TermMatcher(resolve_safety([entry]).terms))
        for entry in active
    )
    return _Registered(rows, _registered_scan(active))


def _words(text: str, registered: _Scan) -> list[str]:
    """글에서 음식 낱말을 사전으로 찾는다. 글에 나온 순서이고, 다른 낱말 안에 든 낱말은 뺀다."""
    for name in _chronic_names():
        text = text.replace(name, " ")  # "유당불내증인데" 의 "유당" 이 음식으로 잡히지 않게
    prepared = prepare_fields((text,))
    found: set[str] = set()
    for scan in (_static_scan(), registered):
        if scan.matcher is not None:
            found |= set(scan.matcher.match(prepared))
        found |= _weak_words(text, scan.weak)
    kept = [word for word in found if not any(word != other and word in other for other in found)]
    dense = normalize(text)
    return sorted(kept, key=lambda word: (dense.find(word) if word in dense else len(dense), word))


@cache
def _static_scan() -> _Scan:
    """사전의 음식 이름 · 묶음 식품 · 연령 규칙 별칭. 앞에서 넣은 낱말은 다시 넣지 않는다.

    이름 사전의 guard 를 지키려고다 — 묶음 식품 "콩" 을 따로 넣으면 "땅콩호박" 이 콩으로 걸린다.
    질환 이름(유당불내증)은 뺀다. 등록할 때 읽는 이름이지 먹는 음식이 아니다. 헷갈리는 별칭
    (`AMBIGUOUS_ALIASES`)은 `weak` 으로 따로 두고, 나머지에는 `QUESTION_GUARDS` 를 더한다.
    """
    chronic = {normalize(name) for name in chronic_restriction_terms()}
    weak: dict[str, str] = {}
    terms: list[Term] = []

    def keep(key: str, aliases: Sequence[str], guards: Sequence[str]) -> None:
        strong = []
        for alias in aliases:
            name = normalize(alias)
            if name in EXCLUDED_ALIASES:
                continue
            if name in AMBIGUOUS_ALIASES:
                weak[name] = key
            else:
                strong.append(alias)
        if strong:
            extra = (g for alias in strong for g in QUESTION_GUARDS.get(normalize(alias), ()))
            terms.append(Term(key=key, aliases=tuple(strong), guards=(*guards, *extra)))

    book_terms = food_book().scan_terms
    for term in book_terms:
        if term.key not in chronic:
            keep(term.key, term.aliases, term.guards)
    seen = {term.key for term in book_terms} | chronic
    food = food_safety_terms()
    extra = (
        *((alias, group.guards) for group in food.group_foods.values() for alias in group.aliases),
        *((alias, rule.term.guards) for rule in food.age_rules for alias in rule.term.aliases),
    )
    for alias, guards in extra:
        key = normalize(alias)
        if key and key not in seen:
            seen.add(key)
            keep(key, (alias,), guards)
    return _Scan(TermMatcher(terms), MappingProxyType(weak))


def _registered_scan(active: Sequence[SafetyEntry]) -> _Scan:
    """보호자가 등록한 알레르기 중 사전 밖 낱말 — "용과 알레르기" 를 물으면 용과가 잡히게.

    알레르기(`kind=allergy`)만 본다. 질환 · 환경 · 기타 건강 정보의 이름(아토피 · 꽃가루)은 음식이
    아니다. 한 글자 이름은 `SINGLE_CHAR_FOODS` 에 있을 때만 받고 어절 전체일 때만 본다.
    """
    book = food_book()
    terms: list[Term] = []
    weak: dict[str, str] = {}
    for entry in active:
        if entry.kind != "allergy":
            continue
        for word in read_label(entry.label, book)[1]:
            if len(word) == 1 and word not in SINGLE_CHAR_FOODS:
                continue
            if len(word) == 1 or word in AMBIGUOUS_ALIASES:
                weak[word] = word
            else:
                terms.append(Term(key=word, aliases=(word,)))
    return _Scan(TermMatcher(terms) if terms else None, MappingProxyType(weak))


def _weak_words(text: str, weak: Mapping[str, str]) -> set[str]:
    """어절 전체(조사만 붙은 것)이고 먹이는 말이 곁에 있는 헷갈리는 낱말.

    "게 먹여도 돼?" · "밤을 넣어" 는 음식이다. "어떻게" · "먹게" · "배탈" · "오늘 밤" ·
    "밤이 늦었는데" · "은행 갔다 와서" 는 아니다.
    """
    if not weak:
        return set()
    tokens = _tokens(text)
    found: set[str] = set()
    for index, token in enumerate(tokens):
        match = _whole_word(normalize(token), weak)
        if match is None:
            continue
        key, particle = match
        if index and tokens[index - 1] in _TIME_BEFORE:
            continue
        window = 1 if particle in _SUBJECT_PARTICLES else 2
        if any(after.startswith(_FEEDING) for after in tokens[index + 1 : index + 1 + window]):
            found.add(key)
    return found


def _whole_word(name: str, weak: Mapping[str, str]) -> tuple[str, str] | None:
    """어절이 헷갈리는 낱말이거나 낱말 + 조사면 (사전 키, 붙은 조사). 아니면 None."""
    if name in weak:
        return weak[name], ""
    for stem, particle in split_particle(name):
        if particle in _WEAK_PARTICLES and stem in weak:
            return weak[stem], particle
    return None


def _mentioned(tokens: Sequence[str], word: str) -> bool:
    """낱말이 빼 달라는 말 · 알레르기를 말하는 말 밖에서 한 번이라도 나왔는가.

    "견과류 빼고" · "땅콩 안 들어간" · "우유 대신" · "복숭아 알레르기 있는데" 의 음식은 물은 음식이
    아니다. 어절로 찾을 수 없으면(낱말이 띄어쓰기를 넘는다) 나온 것으로 본다 — 덜 막지 않게.
    """
    seen = False
    for index, token in enumerate(tokens):
        name = normalize(token)
        at = name.find(word)
        if at < 0:
            continue
        seen = True
        after = tokens[index + 1] if index + 1 < len(tokens) else ""
        later = tokens[index + 2] if index + 2 < len(tokens) else ""
        excluded = (
            _excluding(name[at + len(word) :].lstrip("은는을를이가도"))
            or _excluding(after)
            or (after == "안" and later.startswith(("들어", "넣")))
        )
        if not excluded:
            return True
    return not seen


def _excluding(rest: str) -> bool:
    return rest == "빼" or rest.startswith(_EXCLUDING)


@cache
def _age_rules() -> Mapping[str, AgeRule]:
    return MappingProxyType({rule.term.key: rule for rule in food_safety_terms().age_rules})


@lru_cache(maxsize=4096)
def _age_hits(token: str, stage: Stage, months: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """어절 하나를 메뉴 이름으로 둔 연령 판정 — (막는 규칙 key, 주의 규칙 key).
    어절 guard 를 지킨다."""
    verdict = filter_food_safety([_menu_row(token)], (), stage, months=months)
    return tuple(verdict.hits.get(token, ())), tuple(verdict.cautions.get(token, ()))


@lru_cache(maxsize=4096)
def _menu_conflict(
    phrase: str, entry: SafetyEntry, stage: Stage, months: int
) -> frozenset[int] | None:
    """어절을 메뉴 행처럼 두고 등록 한 행으로 거른 결과 — 막으면 겹친 19종 코드(글자로만 막았으면
    빈 집합), 안 막으면 None. 메뉴를 거르는 필터와 같은 답을 내려고 쓴다.

    연령 규칙이 건 것은 겹침이 아니다. 제품마다 다른 성분으로만 막힌 것(`needs_check` — 카레의
    밀)도 겹침으로 말하지 않는다. 그 몫은 재료 확인 안내다.
    """
    verdict = filter_food_safety([_menu_row(phrase)], [entry], stage, months=months)
    if verdict.needs_check:
        return None
    age = set(_age_hits(phrase, stage, months)[0])
    hits = [hit for hit in verdict.hits.get(phrase, ()) if hit not in age]
    if not hits:
        return None
    return frozenset(int(hit) for hit in hits if hit.isdigit())


def _menu_row(name: str) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=name,
        display_name=name,
        source="manual",
        resolved=True,
        synced_at=_EPOCH,
        ingredients=(name,),
    )


def _unnamed_foods(tokens: Sequence[str], words: Sequence[str]) -> list[str]:
    """사전 낱말이 하나도 없는데 먹이는 음식 자리에 온 어절(조사를 뗀 것) — "전복죽 먹여도 돼?".

    먹이는 말 바로 앞 어절과, 그 앞에 "랑 · 하고 · 와" 로 이어진 어절이다. 부사 · 의문사 ·
    가리키는 말 · 어미로 끝나는 어절("으깨서" · "어떻게")은 뺀다. 한 글자는 보지 않는다.
    """
    found: list[str] = []
    for index, token in enumerate(tokens):
        if not index or not token.startswith(_FEEDING):
            continue
        at = index - 1
        while at >= 0 and tokens[at] in _NOT_FOOD:
            at -= 1
        joined = False
        while at >= 0:
            stem = _stem(tokens[at])
            # 바로 앞 어절은 조사를 떼기 전에 어미를 본다 — "안심하고 먹여도" 의 하고는 어미다
            ending = stem if joined else tokens[at]
            if _could_be_food(tokens[at], stem, ending, words) and stem not in found:
                found.append(stem)
            if at == 0 or not _joins(tokens[at - 1]):
                break
            at -= 1
            joined = True
    return found


def _could_be_food(token: str, stem: str, ending: str, words: Sequence[str]) -> bool:
    name = normalize(stem)
    return (
        len(name) > 1
        and token not in _NOT_FOOD
        and token not in _TIME_BEFORE
        and not token.startswith(_POINTING)
        and not token.startswith(_FEEDING)
        and stem not in _GENERIC_FOODS
        and not ending.endswith(_VERB_ENDINGS)
        and not any(word in normalize(token) for word in words)
    )


def _joins(token: str) -> bool:
    return any(particle in _JOINING for _, particle in split_particle(token))


def _stem(token: str) -> str:
    """어절에서 조사를 뗀 것 — `_phrase_of` 와 같은 순서. 조사 "이" 는 떼지 않는다."""
    for tail in _AUXILIARY:
        if token.endswith(tail) and len(token) > len(tail) + 1:
            return token[: -len(tail)]
    for stem, particle in split_particle(token):
        if particle != "이":
            return stem
    return token


def _rule_fits(rule: AgeRule, word: str) -> bool:
    """그 연령 규칙이 이 낱말 때문에 걸렸는가 — 같은 어절의 다른 낱말이 건 규칙은 받지 않는다."""
    if word in rule.exact or word in rule.ends:
        return True
    return any(name and name in word for name in map(normalize, rule.term.aliases))


@cache
def _chronic_names() -> tuple[str, ...]:
    return tuple(sorted(chronic_restriction_terms(), key=len, reverse=True))


def _tokens(text: str) -> list[str]:
    return [token.strip(_PUNCT) for token in text.split()]


def _phrase_of(text: str, word: str) -> str:
    """보호자가 쓴 어절에서 조사를 뗀 것 — "김밥은" → "김밥". 문구가 사전 낱말의 조각("밥" ·
    "크림")이 아니라 보호자가 말한 음식("김밥" · "아이스크림")을 가리키게 한다. 조사 "이" 는
    떼지 않는다 — 계란말이 · 떡볶이 · 오이처럼 "이" 로 끝나는 음식이 많다."""
    token = _token_of(text, word).strip(_PUNCT)
    for tail in _AUXILIARY:
        if token.endswith(tail) and word in normalize(token[: -len(tail)]):
            return token[: -len(tail)]
    for stem, particle in split_particle(token):
        if particle != "이" and word in normalize(stem):
            return stem
    return token if word in normalize(token) else word


def _token_of(text: str, word: str) -> str:
    """낱말이 처음 든 어절. 연령 규칙은 어절 하나를 메뉴 이름으로 보고 판정한다."""
    for token in text.split():
        if word in normalize(token):
            return token
    return word


def label_text(entry: SafetyEntry) -> str:
    """등록 행을 문구에 넣는 이름 — 보호자가 적은 그대로. 알레르기면 "알레르기" 를 붙인다."""
    name = _LABEL_TAIL.sub("", entry.label.strip()) or entry.label.strip()
    return f"{name} 알레르기" if entry.kind == "allergy" else name


def _joined(word: str) -> str:
    """받침에 맞는 "과" · "와". 한글로 끝나지 않으면 받침을 몰라 "와(과)" 다."""
    final = has_final(word)
    return "와(과)" if final is None else ("과" if final else "와")


def when_text(rule: AgeRule) -> str:
    """연령 규칙이 막는 때 — 월령 기준이 없는 규칙(날음식 · 카페인 · 술)은 모든 아이다."""
    return f"{rule.below_month}개월 전에는" if rule.below_month is not None else "아이에게는"


def answer_food_check(
    text: str, entries: Sequence[SafetyEntry], *, consent: bool, stage: Stage, months: int
) -> tuple[tuple[Readout, ...], str | None]:
    """섭취 확인의 답 — (화면 문구들, 되묻는 한 줄 또는 None). 전부 코드 문구다.

    🚨 허가 표현(먹여도 돼요 · 괜찮아요)을 쓰지 않는다. 겹치는 걸 못 찾았다는 것은 괜찮다는 뜻이
    아니다 — 사전에 없는 음식과 조합 음식의 재료는 못 본다. 그래서 못 찾음에는 재료 확인을 붙이고,
    음식을 알아보지 못했으면 등록 · 동의와 상관없이 그렇다고 말한다. 문구는 보호자가 쓴 어절로
    말하고, 어절 하나에는 한 가지만 말한다 — 겹치는 어절에 못 찾음을 같이 말하지 않는다.
    """
    verdicts = check_foods(text, entries, stage=stage, months=months)
    if not verdicts and _points_at_something(text):
        return (), READOUTS.render(CHECK_ASK_FOOD).body

    active = [entry for entry in entries if entry.status not in INACTIVE_STATUSES]
    food_entries = [entry for entry in active if _food_related(entry)]
    labels = " · ".join(dict.fromkeys(label_text(entry) for entry in food_entries))

    readouts: list[Readout] = []
    said: set[tuple[str, str]] = set()
    for verdict in verdicts:
        phrase = _phrase_of(text, verdict.word)
        for conflict in verdict.conflicts:
            label = label_text(conflict.entry)
            if (phrase, label) not in said:
                said.add((phrase, label))
                readouts.append(
                    READOUTS.render(
                        CHECK_CONFLICT,
                        food=phrase,
                        은는=topic_particle(phrase),
                        label=label,
                        와과=_joined(label),
                    )
                )
    for phrase, rule in _strictest_age_blocks(text, verdicts).items():
        readouts.append(
            READOUTS.render(
                CHECK_AGE_BLOCK, food=phrase, 은는=topic_particle(phrase), when=when_text(rule)
            )
        )
    if any(_peanut_in_nuts(verdict) for verdict in verdicts):
        readouts.append(READOUTS.render(CHECK_PEANUT_IN_NUTS))
    for phrase, rule in _cautions(text, verdicts).items():
        readouts.append(
            READOUTS.render(
                CHECK_CHOKING, food=phrase, 은는=topic_particle(phrase), when=when_text(rule)
            )
        )

    if not verdicts:
        # 음식 이름을 알아보지 못했다 — 겹치지 않는다고 말하면 확인한 것처럼 읽힌다
        if consent and food_entries:
            readouts.append(
                READOUTS.render(CHECK_UNKNOWN_FOOD, labels=labels, 와과=_joined(labels))
            )
        else:
            readouts.append(READOUTS.render(CHECK_UNKNOWN_FOOD_PLAIN))
    if not consent:
        readouts.append(READOUTS.render(CHECK_NO_CONSENT))
    elif not food_entries:
        readouts.append(READOUTS.render(CHECK_NONE_REGISTERED))
    elif verdicts:
        blocked = {_phrase_of(text, v.word) for v in verdicts if v.blocked}
        unblocked = dict.fromkeys(
            phrase
            for phrase in (_phrase_of(text, v.word) for v in verdicts if not v.blocked)
            if phrase not in blocked
        )
        for phrase in unblocked:
            readouts.append(
                READOUTS.render(CHECK_NOT_FOUND, food=phrase, labels=labels, 와과=_joined(labels))
            )
        if unblocked:
            readouts.append(READOUTS.render(CHECK_INGREDIENTS_CAVEAT))
    return tuple(readouts), None


def request_notices(
    texts: Sequence[str], entries: Sequence[SafetyEntry], *, stage: Stage, months: int
) -> tuple[Readout, ...]:
    """식단 추천 요청 글에 막힌 음식이 있으면 빼고 골랐다는 안내. 같은 어절은 한 번만 말한다.

    보호자가 쓴 어절만 싣고 등록한 이름은 싣지 않는다 — 추천 결과에는 등록한 알레르기 이름이
    없어야 한다. 이 안내는 모델 입력에도 가지 않는다. 모델이 후보로 보는 풀은 이미 걸러져 있다.
    빼 달라고 한 음식("견과류 빼고")은 안내하지 않는다.
    """
    notices: list[Readout] = []
    said: set[str] = set()
    for text in texts:
        verdicts = check_foods(text, entries, stage=stage, months=months)
        for verdict in verdicts:
            phrase = _phrase_of(text, verdict.word)
            if not verdict.conflicts or phrase in said:
                continue
            said.add(phrase)
            what = (
                "알레르기"
                if any(c.entry.kind == "allergy" for c in verdict.conflicts)
                else "건강 정보"
            )
            notices.append(READOUTS.render(MEAL_REQUEST_CONFLICT, food=phrase, what=what))
        for phrase, rule in _strictest_age_blocks(text, verdicts).items():
            if phrase not in said:
                said.add(phrase)
                notices.append(
                    READOUTS.render(
                        MEAL_REQUEST_AGE_BLOCK,
                        food=phrase,
                        은는=topic_particle(phrase),
                        when=when_text(rule),
                    )
                )
    return tuple(notices)


def _strictest_age_blocks(text: str, verdicts: Sequence[FoodVerdict]) -> dict[str, AgeRule]:
    """어절마다 가장 오래 막는 연령 규칙 하나 — "꿀떡" 은 꿀(12개월)이 아니라 떡(48개월)으로 말한다.
    월령 기준이 없는 규칙(날음식 · 카페인 · 술)이 가장 길다."""
    strictest: dict[str, AgeRule] = {}
    for verdict in verdicts:
        if verdict.age_block is None:
            continue
        phrase = _phrase_of(text, verdict.word)
        held = strictest.get(phrase)
        if held is None or _lasts(verdict.age_block) > _lasts(held):
            strictest[phrase] = verdict.age_block
    return strictest


def _cautions(text: str, verdicts: Sequence[FoodVerdict]) -> dict[str, AgeRule]:
    """어절마다 주의 규칙 하나 — 섭취 확인 답에만 쓴다. 같은 어절이 막혔으면 말하지 않는다."""
    rules = _age_rules()
    blocked = {_phrase_of(text, v.word) for v in verdicts if v.blocked}
    found: dict[str, AgeRule] = {}
    for verdict in verdicts:
        phrase = _phrase_of(text, verdict.word)
        if verdict.caution is not None and phrase not in blocked:
            found.setdefault(phrase, rules[verdict.caution])
    return found


def _lasts(rule: AgeRule) -> float:
    return float("inf") if rule.below_month is None else rule.below_month


def _food_related(entry: SafetyEntry) -> bool:
    """음식을 막는 등록인가 — 알레르기이거나, 질환이어도 막을 식품이 매핑돼 있다(유당불내증).
    천식 · 꽃가루 같은 등록을 "겹치는 걸 못 찾았다" 의 비교 대상으로 말하지 않는다."""
    if entry.kind == "allergy":
        return True
    found = read_label(entry.label, food_book())[0]
    return bool(found.codes or found.terms)


def _points_at_something(text: str) -> bool:
    """ "이거" · "이 간식" 처럼 음식을 가리키기만 하고 이름은 말하지 않았는가."""
    tokens = _tokens(text)
    for index, token in enumerate(tokens):
        if token.startswith(_POINTING) or token in ("거", "것"):
            return True
        if token in _DETERMINERS and index + 1 < len(tokens):
            if tokens[index + 1].startswith(_GENERIC_FOODS):
                return True
    return False


def _peanut_in_nuts(verdict: FoodVerdict) -> bool:
    """땅콩이 견과류와 한 묶음으로 막혀서 겹친 경우 — 낱말과 등록 이름 중 한쪽만 땅콩 이름이다."""
    peanut = _peanut_matcher()
    word_is_peanut = bool(peanut.match(prepare_fields((verdict.word,))))
    return any(
        4 in conflict.codes
        and word_is_peanut != bool(peanut.match(prepare_fields((conflict.entry.label,))))
        for conflict in verdict.conflicts
    )


@cache
def _peanut_matcher() -> TermMatcher:
    return TermMatcher([term for term in allergen_terms() if term.key == _PEANUT_CODE])
