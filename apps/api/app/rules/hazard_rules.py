"""위험 물질 사전 — 무엇을 거는가. 어떻게 거는가는 `hazard.py` 에 있다.

여기는 이름 · 섭취 동사 · 예외 표기(`aliases` · `guards`)만 둔다. 새 표현이 나오면 이 파일의
값만 늘리면 되고 판정 코드는 그대로다. 반대로 값이 바뀔 때 판정도 바뀌어야 하는 것(마디 나누기 ·
부정 · 어른 주어 판별 · 거리 제한)은 `hazard.py` 에 둔다. 어른 · 아이를 가리키는 낱말 목록이
거기 남아 있는 것도 같은 이유다 — 주어 판별 방법의 일부다.

`reference/hazard_terms.yaml` 은 월령별 놀이 · 활동 안전 사전이라 이것과 다르다.

순수 데이터. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

from dataclasses import dataclass
from typing import Literal

from app.rules.term_match import Term, normalize

HazardKind = Literal["drug", "alcohol", "tobacco", "chemical", "object"]
# `find_hazards` 가 돌려주는 순서
HAZARD_KINDS: tuple[HazardKind, ...] = ("drug", "alcohol", "tobacco", "chemical", "object")

_DRUG = Term(
    "drug",
    (
        "필로폰",
        "히로뽕",
        "메스암페타민",
        "메트암페타민",
        "코카인",
        "헤로인",
        "엑스터시",
        "케타민",
        "펜타닐",
        "대마초",
        "마리화나",
        "마약을",
        "마약했",
        "마약하",
        "마약해",
    ),
)
# 마약 이름은 놀이 · 배움 기록의 대상 이름에도 나오면 안 된다. 예방 교육은 남긴다
_DRUG_NAME = Term(
    "drug",
    (*_DRUG.aliases, "마약"),
    guards=(
        "마약예방",
        "마약퇴치",
        "마약김밥",
        "마약떡볶이",
        "마약옥수수",
        "마약계란",
        "마약달걀",
        "마약토스트",
        "마약빵",
        "마약소스",
        "마약치킨",
        "마약만두",
        "마약핫도그",
        "마약간장",
    ),
)
_ALCOHOL = Term(
    "alcohol",
    ("술", "소주", "맥주", "와인", "막걸리", "양주", "위스키", "보드카", "고량주", "사케"),
    guards=(
        "술래",
        "술빵",
        "술떡",
        "미술",
        "기술",
        "마술",
        "수술",
        "예술",
        "요술",
        "무술",
        "학술",
        "술술",
        "단술",
        "맛술",
        "입술",
        "사케동",
        "술안주",
        "무알콜맥주",
        "무알콜와인",
        # 숟가락 수. 띄어쓰기를 지우고 보므로 "두 술" 도 "두술" 이다
        "한술",
        "두술",
        "세술",
        "네술",
        "몇술",
        "반술",
        "첫술",
        "다섯술",
        "여섯술",
        "일곱술",
        "여덟술",
        "아홉술",
        "열술",
        "서너술",
        "여러술",
        "두어술",
        "댓술",  # 네댓 · 너댓
    ),
)
_TOBACCO = Term("tobacco", ("담배", "담뱃", "꽁초", "니코틴"))
_CHEMICAL = Term(
    "chemical",
    (
        "세제",
        "락스",
        "표백제",
        "세정제",
        "유연제",
        "농약",
        "살충제",
        "제초제",
        "쥐약",
        "모기약",
        "부동액",
        "소독제",
        "방향제",
        "탈취제",
        "아세톤",
        "리무버",
        "휘발유",
        "등유",
        "신너",
        "시너",
    ),
    guards=("세제곱", "등유치원", "무농약", "저농약"),
)
_OBJECT = Term(
    "object",
    (
        "건전지",
        "단추전지",
        "코인전지",
        "배터리",
        "동전",
        "자석",
        "구슬",
        "바둑알",
        "압정",
        "옷핀",
        "비비탄",
        "워터비즈",
        "개구리알",
        "레고",
        "단추",
    ),
)

_INTAKE = Term(
    "intake",
    (
        "먹",
        "마시",
        "마셨",
        "마신",
        "마셔",
        "마심",
        "맛보",
        "맛봤",
        "맛본",
        "맛을보",
        "맛을봤",
        "맛을본",
        "핥",
        "삼키",
        "삼켰",
        "삼킨",
        "삼켜",
        "삼킴",
        "씹",
        "빨아",
        "빨았",
        "피우",
        "피웠",
        "피운",
        "피움",
        "흡입",
        "들이마",
        "입에넣",
        "입에물",
        "입에대",
    ),
    guards=("마시멜로", "먹물", "먹구름"),
)
_SWALLOW = Term("swallow", ("삼키", "삼켰", "삼킨", "삼켜", "삼킴", "입에넣", "입에물", "목에걸"))

# 저장 게이트가 관찰 대상 이름에서 보는 것. 물건은 놀잇감 이름이기도 해서 보지 않는다
FOOD_NAME_TERMS = (_DRUG_NAME, _ALCOHOL, _TOBACCO, _CHEMICAL)
PLAY_NAME_TERMS = (_DRUG_NAME,)


@dataclass(frozen=True)
class HazardRules:
    """사전 검사가 보는 사전 묶음. 판정 방법은 `hazard.find_hazards` 에 있다.

    named        이름만 나와도 거는 것 (마약류)
    with_intake  같은 마디에서 이름 뒤 `INTAKE_WORDS` 낱말 안에 `intake` 동사가 와야 거는 것
    with_swallow 같은 마디에서 이름 뒤에 `swallow` 동사가 와야 거는 것

    비었거나, 글자 없는 별칭이 있거나, 모르는 종류 키가 들었거나, 종류가 빠진 묶음은 만들지 못한다 —
    그대로 쓰면 검사가 조용히 빠진다.
    """

    named: tuple[Term, ...]
    with_intake: tuple[Term, ...]
    with_swallow: tuple[Term, ...]
    intake: Term
    swallow: Term

    def __post_init__(self) -> None:
        for field_name in ("named", "with_intake", "with_swallow"):
            if not getattr(self, field_name):
                raise ValueError(f"위험 물질 규칙의 {field_name} 가 비었다")
        for term in (*self.substances, self.intake, self.swallow):
            if not term.aliases:
                raise ValueError(f"위험 물질 규칙 {term.key!r} 에 별칭이 없다")
            if any(not normalize(alias) for alias in term.aliases):
                raise ValueError(f"위험 물질 규칙 {term.key!r} 에 글자가 없는 별칭이 있다")
        kinds = {t.key for t in self.substances}
        unknown = sorted(kinds - set(HAZARD_KINDS))
        if unknown:
            raise ValueError(f"위험 물질 규칙에 모르는 종류가 있다: {unknown}")
        missing = [kind for kind in HAZARD_KINDS if kind not in kinds]
        if missing:
            raise ValueError(f"위험 물질 규칙에 빠진 종류가 있다: {missing}")

    @property
    def substances(self) -> tuple[Term, ...]:
        return (*self.named, *self.with_intake, *self.with_swallow)


DEFAULT_RULES = HazardRules(
    named=(_DRUG,),
    with_intake=(_ALCOHOL, _TOBACCO, _CHEMICAL),
    with_swallow=(_OBJECT,),
    intake=_INTAKE,
    swallow=_SWALLOW,
)
