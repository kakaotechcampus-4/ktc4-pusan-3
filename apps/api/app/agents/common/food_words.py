"""글에 든 음식 낱말을 아이의 등록 알레르기 · 연령 규칙과 대조한다 (#582).

Food 와 Growth 가 이 판정 하나를 같이 쓰고 Activity `foods` 칸도 쓸 예정이다. Food 는 섭취
확인("땅콩소스 먹여도 될까?")과 식단 추천의 요청 글 안내, 추천 이유(reason)에 막힌 음식이 들어갔는지
보는 사후 검사에 쓴다. 모델을 부르지 않는다 — 음식 낱말도 사전으로 찾는다. 등록 행은 `SafetyRow`
모양이면 되고 Agent 의 포트를 import 하지 않는다(docs/agents/README.md §6). 보호자에게 하는 말
(문구)은 Food 의 `food_check` 가 만든다.

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
- 쓰는 곳마다 다른 것은 `WordRules` 로 받는다. 보호자의 질문 · 추천 이유(`QUESTION` · `REASON`)는
  띄어쓰기를 넘고 먹이는 말을 단서로 쓴다. 모델이 쓴 문장(`SENTENCE` · `SCAN`)은 띄어쓰기를 넘지
  않아("장난감 자동차" 가 감자가 아니다) 놀이 문장용 별칭 규칙(`common/allergy.for_sentences`)을
  따른다. 항목 하나(`ITEM`)는 그 음식이라 헷갈리는 낱말도 곁의 말 없이 본다.
- 🚨 못 찾았다는 것은 괜찮다는 뜻이 아니다. 사전에 없는 음식(마라탕)은 낱말이 안 잡혀 빈 결과가
  나온다 — 그것은 "확인 못 함" 이다. 부르는 쪽이 그렇게 다룬다.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import cache, lru_cache
from types import MappingProxyType

from app.agents.common.allergy import (
    AMBIGUOUS_ALIASES,
    MISSPELLED_ALIASES,
    OTHER_MEANING_ALIASES,
    QUESTION_GUARDS,
    SCAN_WEAK_ALIASES,
    SENTENCE_GUARDS,
    SINGLE_CHAR_NAMES,
    SafetyRow,
    SentenceScope,
    allergy_related_table,
    for_sentences,
    read_label,
    split_label,
    split_particle,
)
from app.agents.common.food_safety import (
    INACTIVE_STATUSES,
    SINGLE_CHAR_FOODS,
    age_rules_by_key,
    filter_food_safety,
    food_book,
    resolve_safety,
)
from app.agents.common.reference import (
    AgeRule,
    allergen_terms,
    chronic_restriction_terms,
    food_safety_terms,
    known_foods,
)
from app.rules.age import Stage
from app.rules.term_match import Term, TermMatcher, match_fields, normalize, prepare_fields

# 헷갈리는 낱말 뒤에 붙어도 음식으로 쓰인 것으로 보는 조사(`split_particle` 이 받침을 본다).
# 이 · 가는 시간 · 장소 뜻이 많아("밤이 늦었는데") 바로 다음 어절이 먹이는 말일 때만 본다.
# 하고 · 에서 · 에 · 로는 넣지 않는다 — "안심하고" · "구미에" · "대구에서" 가 음식이 된다
_WEAK_PARTICLES = frozenset({"은", "는", "을", "를", "과", "와", "랑", "이랑", "이", "가"})
_SUBJECT_PARTICLES = frozenset({"이", "가"})
# 음식으로 쓰였다는 단서 — 곁(뒤 두 어절)에 이런 말이 있어야 한다
FEEDING_WORDS = (
    "먹", "주", "줘", "줄", "드", "넣", "들어", "섞", "곁들", "얹", "뿌", "추천", "사용",
    "만들", "만든",
)  # fmt: skip
# 모델이 쓴 놀이 · 교육 문장에서는 먹이는 말 말고도 음식을 다루는 동사가 단서다 — "대구로 전 부쳐
# 보기" · "밤을 구워 보기". 어간만 적는다(startswith). 먹이는 말 중 주 · 줘 · 줄 · 드 · 들어 ·
# 추천 · 사용은 뺀다 — "나눠 주기" · "드러내기" · "들어 올리기" 처럼 문장에서는 다른 뜻이 더 많다
# (시드의 "낮과 밤 — … 나눠 주기" 가 밤으로 걸렸다)
HANDLING_WORDS = (
    "먹", "넣", "섞", "곁들", "얹", "뿌", "만들", "만든",
    "부치", "부쳐", "굽", "구워", "구운", "바르", "발라", "묻혀", "묻히", "빚", "반죽", "썰", "깎",
    "꽂", "꿰", "맛보", "볶", "데쳐", "올려", "담아", "말아", "만지", "만져",
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


@dataclass(frozen=True)
class WordRules:
    """글에서 음식 낱말을 어떻게 찾을지 — 쓰는 곳마다 다르다 (#582).

    같은 사전이라도 보호자가 묻는 말, 추천 이유, 모델이 쓴 놀이 문장은 낱말이 다른 뜻으로 나오는
    자리가 다르다. 한쪽 규칙을 다른 쪽에 그대로 쓰면 덜 막거나(헷갈리는 낱말을 먹이는 말 없이 놓침)
    더 막는다(띄어쓰기를 넘어 "장난감 자동차" 가 감자로 걸림).
    """

    cross_spaces: bool  # 낱말이 띄어쓰기를 넘어도 찾는가
    # None 이면 사전 그대로 + `QUESTION_GUARDS`. 값이 있으면 별칭을 `for_sentences(scope=)` 로
    # 거르고 문장용 guard 를 쓴다
    scope: SentenceScope | None
    context: tuple[str, ...]  # 헷갈리는 낱말 곁에 있어야 음식으로 보는 말. 비면 곁의 말 없이 본다
    unnamed: bool  # 사전 낱말이 없는 어절을 먹이는 말 앞에서 찾는가("전복죽 먹여도 돼?")
    every_mention: bool  # 빼 달라는 말("땅콩 대신")에 나온 음식도 판정하는가
    # 글이 `known_foods`(사전이 모르는 흔한 재료)의 이름과 통째로 같으면 겹침 없는 판정을 내는가
    known: bool = False

    @property
    def scan_key(self) -> tuple[bool, SentenceScope | None]:
        """사전 훑기 도구를 만드는 데 쓰는 칸만 — 캐시 key."""
        return (self.cross_spaces, self.scope)


# 아이와 상관없이 빼는 스캔(`SCAN`)의 단서 — 먹고 익히는 말만이다. 만들기 · 부치기 · 담기 · 말기 ·
# 올리기는 가장 흔한 공작 동사라 뺀다("종이로 물고기 만들기" · "사과 편지 부치기"). "먹이"(동물에게
# 주는 것)도 아니라서 "먹" 이 아니라 먹는 꼴만 적는다
SCAN_CONTEXT_WORDS = (
    "먹기", "먹고", "먹어", "먹여", "먹는", "먹을", "먹은", "먹으", "먹었",
    "구워", "구운", "굽기", "굽고", "볶", "데쳐", "삶아", "삶은", "튀겨", "튀기", "끓여", "끓이",
    "맛보", "반죽", "요리",
)  # fmt: skip

# 보호자가 묻는 말 · 요청 글 — 섭취 확인("땅콩소스 먹여도 돼?") · 식단 추천 요청 안내
QUESTION = WordRules(True, None, FEEDING_WORDS, True, False)
# 추천 이유 사후 검사 — "땅콩 대신" 이어도 화면에 그 이름이 나간다
REASON = WordRules(True, None, FEEDING_WORDS, True, True)
# 모델이 적은 음식 항목 하나("어묵" · "밤") — 항목은 그 음식이라 곁의 말을 기다리지 않는다.
# 흔한 재료(귤 · 오이)는 사전이 몰라 빈 튜플이 되던 것이라 이름이 통째로 같을 때만 겹침 없는
# 판정을 낸다
ITEM = WordRules(False, None, (), False, True, known=True)
# 모델이 쓴 음식 활동 문장(Activity). 놀이 문장용 별칭 규칙을 따른다
SENTENCE = WordRules(False, "content", HANDLING_WORDS, False, True)
# 아이와 상관없이 문장의 음식 용어를 빼는 스캔(Growth). 오탐이 모든 아이에게 가서 더 거른다
SCAN = WordRules(False, "scan", SCAN_CONTEXT_WORDS, False, True)


@dataclass(frozen=True)
class Conflict:
    """등록한 알레르기 · 질환 한 행과 겹친 것."""

    entry: SafetyRow  # 겹친 등록 행 — 문구가 label · kind 를 쓴다
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
    entries: Sequence[SafetyRow],
    *,
    stage: Stage,
    months: int,
    rules: WordRules = QUESTION,
) -> tuple[FoodVerdict, ...]:
    """글에서 찾은 음식 낱말마다 판정한다. 글에 나온 순서이고, 낱말을 못 찾았으면 빈 튜플이다.

    `entries` 는 `build_gate` 가 run state 에 담은 health_safety 행이다. `retracted` · `none` 은
    읽지 않는다. 동의가 없어 비어 있어도 연령 규칙은 그대로 본다. 낱말을 어떻게 찾을지는 `rules`
    다(`WordRules`). 기본인 `QUESTION` 은 빼 달라는 말 · 알레르기를 말하는 말에만 나온 음식을
    판정하지 않는다 — 보호자의 질문 · 요청을 읽을 때다.

    🚨 빈 튜플은 "괜찮다" 가 아니라 "확인 못 함" 이다 — 사전에 없는 음식(마라탕)은 낱말이 안 잡힌다.
    """
    book = food_book()
    active = tuple(e for e in entries if e.status not in INACTIVE_STATUSES)
    registered = _registered(active, rules.scan_key)
    tokens = _tokens(text)

    verdicts: list[FoodVerdict] = []
    words = _words(text, registered.scan, rules)
    whole = normalize(text)
    if rules.known and whole in _known_names() and whole not in words and _readable(active):
        # 이름이 통째로 같을 때만 — 미역국의 미역 · 김치의 김으로는 판정하지 않는다
        words = [*words, whole]
    for word in words:
        if not rules.every_mention and not _mentioned(tokens, word):
            continue
        asked = book.names.get(word)
        codes = asked.codes if asked is not None else frozenset()
        aliases = (
            word,
            *(alias for term in (asked.terms if asked else ()) for alias in term.aliases),
        )
        prepared = prepare_fields(aliases)
        phrase = phrase_of(text, word)
        conflicts = tuple(
            Conflict(entry, frozenset(codes & registered_codes) | (menu or frozenset()))
            for entry, registered_codes, matcher in registered.rows
            if (menu := _menu_conflict(phrase, entry, stage, months)) is not None
            or codes & registered_codes
            or matcher.match(prepared)
        )
        verdicts.append(_with_age(word, phrase, conflicts, stage, months))

    for phrase in _unnamed_foods(tokens, words) if rules.unnamed else ():
        word = normalize(phrase)
        if not rules.every_mention and not _mentioned(tokens, word):
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
    rules = age_rules_by_key()
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
    entries: Sequence[SafetyRow],
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
        for v in check_foods(text, entries, stage=stage, months=months, rules=REASON)
    )


def preload(rules: WordRules) -> None:
    """`rules` 가 쓰는 사전을 미리 읽는다. 읽지 못하면 예외를 그대로 올린다.

    사전은 처음 찾을 때 느리게 만들어진다. 부르는 쪽이 사전을 못 읽은 것을 한 곳에서 잡아야 할 때
    (Growth 안전 게이트) 시작할 때 한 번 부른다 — 빈 사전으로 통과시키지 않는다.
    """
    _static_scan(*rules.scan_key)
    if rules.known:
        _known_names()


def find_food_words(text: str, rules: WordRules) -> tuple[str, ...]:
    """글에서 사전 음식 낱말만 찾는다 — 등록 행도 판정도 없이. 글에 나온 순서다.

    아이와 상관없이 음식 용어를 빼는 쪽(Growth 스캔)이 쓴다. 돌려주는 것은 사전 낱말뿐이고 글
    원문이 아니다.
    """
    tokens = _tokens(text)
    return tuple(
        word
        for word in _words(text, _NO_REGISTERED, rules)
        if rules.every_mention or _mentioned(tokens, word)
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


_NO_REGISTERED = _Scan(None, MappingProxyType({}))


@dataclass(frozen=True)
class _Registered:
    """등록 행으로 미리 만들어 두는 것 — 겹침 대조 매처와 사전 밖 등록 이름의 훑기 도구."""

    rows: tuple[tuple[SafetyRow, frozenset[int], TermMatcher], ...]
    scan: _Scan


@lru_cache(maxsize=256)
def _registered(
    active: tuple[SafetyRow, ...], key: tuple[bool, SentenceScope | None]
) -> _Registered:
    """등록 행이 같으면 다시 만들지 않는다 — 추천 하나에서 후보 이유마다 같은 행으로 부른다."""
    book = food_book()
    rows = tuple(
        (entry, read_label(entry.label, book)[0].codes, TermMatcher(resolve_safety([entry]).terms))
        for entry in active
    )
    return _Registered(rows, _registered_scan(active, *key))


def _words(text: str, registered: _Scan, rules: WordRules) -> list[str]:
    """글에서 음식 낱말을 사전으로 찾는다. 글에 나온 순서이고, 다른 낱말 안에 든 낱말은 뺀다."""
    for name in _chronic_names():
        text = text.replace(name, " ")  # "유당불내증인데" 의 "유당" 이 음식으로 잡히지 않게
    prepared = prepare_fields((text,))
    found: set[str] = set()
    for scan in (_static_scan(*rules.scan_key), registered):
        if scan.matcher is not None:
            found |= set(scan.matcher.match(prepared))
        found |= _weak_words(text, scan.weak, rules.context)
    kept = [word for word in found if not any(word != other and word in other for other in found)]
    dense = normalize(text)
    return sorted(kept, key=lambda word: (dense.find(word) if word in dense else len(dense), word))


@cache
def _ambiguous_aliases(scope: SentenceScope | None) -> frozenset[str]:
    """헷갈리는 낱말 — 곁의 말이 있을 때만 음식으로 본다. 놀이 문장 규칙(scope 가 있다)은 문장용
    guard 가 다른 뜻을 덮는 낱말(대구 · 은행 · 조기 · 방어)과 한 글자 허용 목록(깨)을 뺀다 —
    그것들은 #556 이 정한 대로 강하게 찾는다."""
    if scope is None:
        return AMBIGUOUS_ALIASES
    if scope == "scan":
        # 아이와 상관없이 빼는 스캔은 오탐이 모든 아이에게 간다. 등록한 이름을 찾는 쪽이 강하게
        # 보는 낱말(대구 · 은행 · 조기 · 방어)도 여기서는 곁에 다루는 말이 있을 때만 본다 —
        # "은행 저금 놀이" · "대구 여행". 사과 · 물고기도 같다(`SCAN_WEAK_ALIASES`). 한 글자(게 ·
        # 밀 · 밤 · 굴)와 다른 뜻이 더 흔한 별칭(포크 · 에그 …)은 다루는 말이 있어도 보지 않는다
        # ("종이로 게 만들기"). 강하게 찾는 쪽으로 넘어가도 `for_sentences` 가 뺀다
        # 19종 별칭(돼지 · 전복 · 대하 …)은 19종 스캔이 guard 와 함께 강하게 잡는다 — 여기서 약한
        # 낱말로 한 번 더 보면 guard 를 못 읽어 "돼지 저금통 만들기" 가 걸린다
        weak = (AMBIGUOUS_ALIASES | SCAN_WEAK_ALIASES) - _allergen_alias_names()
        return frozenset(w for w in weak if len(w) > 1 and w not in OTHER_MEANING_ALIASES)
    return AMBIGUOUS_ALIASES.difference(SENTENCE_GUARDS, SINGLE_CHAR_NAMES)


def _readable(active: Sequence[SafetyRow]) -> bool:
    """등록한 알레르기를 사전이 모두 읽을 수 있는가 — 흔한 재료에 막히지 않았다고 말해도 되는 조건.

    "과일" · "채소" · "해조류" 는 보호자가 넓게 막으려고 쓰는 묶음 이름인데 사전은 읽지 못한다.
    그런 알레르기가 있으면 귤이 그 묶음에 드는지 알 수 없으니, 전처럼 "확인 못 함" 으로 둔다.
    사전이 아는 이름이거나 흔한 재료 하나의 이름("배" · "귤")이면 읽은 것이다 — 그 재료만 막고
    다른 재료로는 넓히지 않는다. 식품 아닌 알레르기 표(`allergy_related_terms.yaml` — 꽃가루 ·
    라텍스 · 동물털)에 있는 이름도 음식 묶음 이름이 아니라서 건너뛴다. Activity 가 음식 알레르기를
    가를 때 쓰는 표와 같은 기준이다. 알레르기가 아닌 등록(질환 · 환경)은 보지 않는다.
    """
    book = food_book()
    known = _known_names()
    non_food = tuple(entry.names for entry in allergy_related_table())
    for entry in active:
        if entry.kind != "allergy":
            continue
        for piece in split_label(entry.label):
            _, rest = read_label(piece, book)
            if all(normalize(word) in known for word in rest):
                continue
            if not match_fields((piece,), non_food):
                return False
    return True


@cache
def _known_names() -> frozenset[str]:
    """`known_foods` 의 이름(정규화). 항목 하나가 이 이름과 통째로 같을 때만 쓴다."""
    return frozenset(normalize(name) for name in known_foods())


@cache
def _allergen_alias_names() -> frozenset[str]:
    """19종 사전의 별칭(정규화). 아이와 상관없는 스캔이 따로 보는 낱말이다."""
    return frozenset(normalize(alias) for term in allergen_terms() for alias in term.aliases)


@cache
def _static_scan(cross_spaces: bool, scope: SentenceScope | None) -> _Scan:
    """사전의 음식 이름 · 묶음 식품 · 연령 규칙 별칭. 앞에서 넣은 낱말은 다시 넣지 않는다.

    이름 사전의 guard 를 지키려고다 — 묶음 식품 "콩" 을 따로 넣으면 "땅콩호박" 이 콩으로 걸린다.
    질환 이름(유당불내증)은 뺀다. 등록할 때 읽는 이름이지 먹는 음식이 아니다. 헷갈리는 별칭
    (`_ambiguous_aliases`)은 `weak` 으로 따로 두고, 나머지에는 `QUESTION_GUARDS` 를 더한다.
    `scope` 가 있으면 그 대신 `for_sentences` 로 거른다 — 문장용 guard 를 더하고 다른 뜻이 흔한
    별칭 · 한 글자를 뺀다.
    """
    chronic = {normalize(name) for name in chronic_restriction_terms()}
    ambiguous = _ambiguous_aliases(scope)
    weak: dict[str, str] = {}
    terms: list[Term] = []

    def keep(key: str, aliases: Sequence[str], guards: Sequence[str]) -> None:
        strong = []
        for alias in aliases:
            name = normalize(alias)
            if name in MISSPELLED_ALIASES:
                continue
            if name in ambiguous:
                weak[name] = key
            else:
                strong.append(alias)
        if not strong:
            return
        if scope is None:
            extra = (g for alias in strong for g in QUESTION_GUARDS.get(normalize(alias), ()))
            terms.append(Term(key=key, aliases=tuple(strong), guards=(*guards, *extra)))
        else:
            # 일상어 합성어 guard("새싹반" · "소주제")도 쓴다. 문장용 guard 가 따로 있는 낱말
            # (사과 · 빵)은 그쪽이 정한 대로 — 조사가 붙은 꼴("사과하고")을 덮지 않는다
            everyday = (
                g
                for alias in strong
                if normalize(alias) not in SENTENCE_GUARDS
                for g in QUESTION_GUARDS.get(normalize(alias), ())
            )
            term = Term(key=key, aliases=tuple(strong), guards=(*guards, *everyday))
            if sentence := for_sentences(term, scope=scope):
                terms.append(sentence)

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
    return _Scan(TermMatcher(terms, cross_spaces=cross_spaces), MappingProxyType(weak))


def _registered_scan(
    active: Sequence[SafetyRow], cross_spaces: bool, scope: SentenceScope | None
) -> _Scan:
    """보호자가 등록한 알레르기 중 사전 밖 낱말 — "용과 알레르기" 를 물으면 용과가 잡히게.

    알레르기(`kind=allergy`)만 본다. 질환 · 환경 · 기타 건강 정보의 이름(아토피 · 꽃가루)은 음식이
    아니다. 한 글자 이름은 `SINGLE_CHAR_FOODS` 에 있을 때만 받고 어절 전체일 때만 본다.
    """
    book = food_book()
    ambiguous = _ambiguous_aliases(scope)
    terms: list[Term] = []
    weak: dict[str, str] = {}
    for entry in active:
        if entry.kind != "allergy":
            continue
        for word in read_label(entry.label, book)[1]:
            if len(word) == 1 and word not in SINGLE_CHAR_FOODS:
                continue
            if len(word) == 1 or word in ambiguous:
                weak[word] = word
                continue
            term = Term(key=word, aliases=(word,))
            if scope is not None:
                term = for_sentences(term, scope=scope)
            if term is not None:
                terms.append(term)
    matcher = TermMatcher(terms, cross_spaces=cross_spaces) if terms else None
    return _Scan(matcher, MappingProxyType(weak))


def _weak_words(text: str, weak: Mapping[str, str], context: tuple[str, ...]) -> set[str]:
    """어절 전체(조사만 붙은 것)이고 `context` 의 말이 곁에 있는 헷갈리는 낱말. `context` 가 비면
    곁의 말을 기다리지 않는다(항목 하나).

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
        near = tokens[index + 1 : index + 1 + window]
        if not context or any(after.startswith(context) for after in near):
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


@lru_cache(maxsize=4096)
def _age_hits(token: str, stage: Stage, months: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """어절 하나를 메뉴 이름으로 둔 연령 판정 — (막는 규칙 key, 주의 규칙 key).
    어절 guard 를 지킨다."""
    verdict = filter_food_safety([_menu_row(token)], (), stage, months=months)
    return tuple(verdict.hits.get(token, ())), tuple(verdict.cautions.get(token, ()))


@lru_cache(maxsize=4096)
def _menu_conflict(
    phrase: str, entry: SafetyRow, stage: Stage, months: int
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


def _unnamed_foods(tokens: Sequence[str], words: Sequence[str]) -> list[str]:
    """사전 낱말이 하나도 없는데 먹이는 음식 자리에 온 어절(조사를 뗀 것) — "전복죽 먹여도 돼?".

    먹이는 말 바로 앞 어절과, 그 앞에 "랑 · 하고 · 와" 로 이어진 어절이다. 부사 · 의문사 ·
    가리키는 말 · 어미로 끝나는 어절("으깨서" · "어떻게")은 뺀다. 한 글자는 보지 않는다.
    """
    found: list[str] = []
    for index, token in enumerate(tokens):
        if not index or not token.startswith(FEEDING_WORDS):
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
        and not token.startswith(FEEDING_WORDS)
        and stem not in _GENERIC_FOODS
        and not ending.endswith(_VERB_ENDINGS)
        and not any(word in normalize(token) for word in words)
    )


def _joins(token: str) -> bool:
    return any(particle in _JOINING for _, particle in split_particle(token))


def _stem(token: str) -> str:
    """어절에서 조사를 뗀 것 — `phrase_of` 와 같은 순서. 조사 "이" 는 떼지 않는다."""
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


def phrase_of(text: str, word: str) -> str:
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


def points_at_food(text: str) -> bool:
    """ "이거" · "이 간식" 처럼 음식을 가리키기만 하고 이름은 말하지 않았는가."""
    tokens = _tokens(text)
    for index, token in enumerate(tokens):
        if token.startswith(_POINTING) or token in ("거", "것"):
            return True
        if token in _DETERMINERS and index + 1 < len(tokens):
            if tokens[index + 1].startswith(_GENERIC_FOODS):
                return True
    return False


@dataclass(frozen=True)
class _PhraseRow:
    """어절 하나를 메뉴 행처럼 둔 것 — `filter_food_safety` 가 읽는 칸만(`MenuRow`)."""

    menu_key: str
    display_name: str
    ingredients: tuple[str, ...]
    allergen_codes: frozenset[int] = frozenset()
    resolved: bool = True


def _menu_row(name: str) -> _PhraseRow:
    return _PhraseRow(menu_key=name, display_name=name, ingredients=(name,))
