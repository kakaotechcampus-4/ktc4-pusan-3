"""진료 준비 타임라인의 사후 검사 — 모델이 쓴 줄을 코드가 거른다.

모델은 정리만 한다. 아래는 모델에게 묻지 않고 코드가 정한다.

- 근거 없는 줄은 지운다. 줄의 `ref` 가 이번 run 의 조회 tool 이 돌려준 id(`state.seen_refs`)가
  아니거나, 구획과 그 id 의 종류가 맞지 않거나(식사 줄에 복약 코스 id), 약 줄인데 약 이름 원문을
  모르면 그 줄만 조용히 지운다 — 나머지로 정리는 계속된다
- 거절은 tool 실패다. 약 이름 · 용량이 등록 원문과 글자 단위로 다르거나, 약 줄이 체크 안 된
  회차를 "안 먹였다" 로 읽히게 썼거나(`FORBIDDEN_DOSE_GUESS`), 해석 표현이 든 줄이 하나라도 있으면
  아무것도 내지 않고 사유를 돌려줘 모델이 그 줄을 한 번 고쳐 쓰게 한다
- 보호자가 말한 병 이름(중이염 · 감기 등)을 그 말 그대로 인용한 줄은 `~염 · ~증` · 병 이름 규칙에서
  면제다 — 그 줄의 `ref` 원문에 같은 말이 있을 때만. 다른 줄의 원문으로는 면제받지 못한다

- 용량 · 다른 기록의 약 이름 · "안 먹였다" 단정은 구획과 상관없이 모든 줄에서 본다(그 줄 ref 원문에
  있으면 통과). 용량은 낱개로 견준다("2.5ml" 원문에 "5ml" 은 없다). 먹인 약 행의 ref 를 증상 줄에
  달면 그 줄을 지운다 — 약 줄 검사를 피해 가는 길이다
- 날짜는 코드 몫이다. 글머리 "M/D" 는 모든 구획에서 `line.date` 와 같아야 하고, 증상 · 식사 ·
  먹인 약 줄의 `line.date` 는 그 ref 기록의 날짜 범위(`state.ref_days`) 안이어야 한다 — 아니면
  거절한다 (지우지 않는다: 기록은 맞고 날짜만 틀린 줄이라 고쳐 쓰게 한다)
- 복약 줄의 `n/m회` 는 조회한 날짜별 체크 수(`state.check_counts`)와 같아야 한다. 띄어 써도 같은
  숫자로 보고, "3회 중 3회" 처럼 `n/m회` 밖의 "n회" 는 거절한다. 어느 기록에도 없는 약 이름은
  글자로 못 잡는다 — 프롬프트 몫

사유 문장에는 줄 번호와 규칙 이름만 쓴다. 약 이름 · 증상 원문은 싣지 않는다 (로그로 흘러간다).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.health.context import HealthContext
from app.agents.health.schemas.records import TimelineLine

# 공백을 없앤 글에서 찾는다 — 모두 낱말 조각이다.
# - "듯" 은 "따뜻" 과 다른 글자다(뜻 ≠ 듯) — 그대로 둔다
# - "인해" 는 "확인해 주세요" 에도 들어 있어서 "로인해"(…로 인해 · …으로 인해)로 건다
# - "같아" · "같습" · "같음" 은 "어제와 같아요" 같은 사실도 거른다 — 놓치는 것보다 낫다(안전 쪽)
FORBIDDEN_INTERPRETATION: tuple[str, ...] = (
    "때문",
    "의심",
    "같아요",
    "가능성",
    "진단",
    "괜찮아요",
    "위험",
    "것같",
    "같아",
    "같습",
    "같음",
    "듯",
    "추정",
    "원인",
    "탓",
    "로인해",
    "우려",
)
# 병 이름처럼 읽히는 말(~염 · ~렴 · ~증). 보호자 원문 인용 줄의 예외는 꼬리를 뺀 말(그룹 1)로
# 그 줄 ref 의 원문과 견준다.
# - ~염 · ~렴 은 뒤에 무엇이 붙든 잡는다("장염의" · "장염인가요" · "결막염임") — 조사 목록으로는
#   어미가 끝없이 새 나간다. '~제'(소염제)만 뺀다. "저렴" 은 병 이름이 아니라 뺀다. 아기 음식의
#   저염 · 무염 · 식염 · 흑염(저염치즈 · 무염버터 · 생리식염수 · 흑염소)도 뺀다. "오염" 은
#   걸린다(안전 쪽)
# - ~증 은 조사까지만 본다 — "증상" · "콧물증상" 같은 합성어를 잡지 않으려고
_PARTICLE = (
    "이에요|이었어요|이래요|이라고|이라는|이라서|이라|이래|이네요|이고|이며|이야|이요|입니다|이었|"
    "인지|인데|인|일|이|가|은|는|을|를|으로|로|에|에서|과|와|도|만|까지|처럼|부터|요"
)
FORBIDDEN_SUFFIX = re.compile(
    rf"(?<![가-힣])([가-힣]*?[가-힣]"
    rf"(?:(?<![저무식흑])염(?!제)|(?<!저)렴(?!제)|증(?=(?:{_PARTICLE})?(?![가-힣]))))"
)
# ~염 · ~증 꼴이 아닌 병 이름. FORBIDDEN_SUFFIX 와 같은 규칙이다 — 그 줄 ref 원문에 있으면 인용이다
FORBIDDEN_DISEASE_NAMES: tuple[str, ...] = (
    "감기",
    "독감",
    "수족구",
    "수두",
    "홍역",
    "코로나",
    "바이러스",
    "세균",
    "탈수",
)
# 약 줄 전용 — 체크 안 됨은 안 먹임이 아니다 · 비율 금지
FORBIDDEN_DOSE_GUESS: tuple[str, ...] = ("안 먹", "못 먹", "빼먹", "놓쳤", "%")
# 모든 줄 — 약을 안 먹였다는 단정. 식사 줄의 "밥은 못 먹음" 은 사실이라 여기 넣지 않는다.
# 보호자가 그 말을 했으면(그 줄 ref 의 원문에 있으면) 인용으로 통과한다
FORBIDDEN_MISSED: tuple[str, ...] = ("빼먹", "놓쳤", "안 먹였", "못 먹였")

# 줄에 적힌 용량 꼴. 이런 말이 있으면 등록 원문에 그대로 있어야 한다 (환산 · 반올림 금지).
# 낱개로 뽑아 원문의 용량 집합과 견준다 — 부분 문자열로 보면 "2.5ml" 원문에 "5ml" 이 숨는다.
# 분수(1/2정)도 한 덩어리다. 앞이 숫자 · 소수점 · '/' 이면 시작하지 않는다(12.5ml 의 2.5ml ·
# 1/2정 의 2정 을 따로 뽑지 않게 — "2정" 은 네 배다)
_DOSE = re.compile(r"(?<![\d./])\d+(?:[./]\d+)?\s*(?:ml|mg|mcg|g|cc|정|포|알|방울|스푼)")
_WHITESPACE = re.compile(r"\s+")

# 구획 → 그 줄이 가리킬 수 있는 id 의 종류
_KIND_OF_SECTION = {
    "symptom": "observation_health",
    "given": "observation_health",
    "meal": "observation_food",
    "medication": "medication_schedule",
}
_DRUG_SECTIONS = frozenset({"medication", "given"})

# 복약 줄의 "n/m회" · 줄 앞의 "M/D" 날짜.
# "n/m회" 는 '/' 둘레와 '회' 앞의 공백을 붙인 글에서 찾는다("1 / 3회" · "1/3 회"). 공백을 전부
# 지우지는 않는다 — "10/14 1/3회" 가 "10/141/3회" 로 붙어 날짜가 체크 수로 읽힌다
_COUNT = re.compile(r"(?<![\d/])(\d+)/(\d+)(?:회|번)")
_COUNT_SPACING = re.compile(r"\s*/\s*")
_BEFORE_TIMES = re.compile(r"\s+(?=회|번)")
# n/m회 를 지우고도 남은 "3회" · "3번" — "3회 중 3회" 처럼 대조를 피해 가는 꼴이다
_BARE_COUNT = re.compile(r"\d+(?:회|번)")
_LEADING_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?![\d/]|\s*회)")


@dataclass(frozen=True)
class Review:
    kept: tuple[TimelineLine, ...]
    dropped: tuple[str, ...]  # 지운 줄의 사유 — tool 은 실패하지 않는다
    rejected: tuple[str, ...]  # 거절한 줄의 사유 — tool 이 실패한다. 그 줄은 kept 에 없다


def review(lines: Sequence[TimelineLine], context: HealthContext) -> Review:
    kept: list[TimelineLine] = []
    dropped: list[str] = []
    rejected: list[str] = []
    for number, line in enumerate(lines, start=1):
        drop = _drop_reason(line, context)
        if drop is not None:
            dropped.append(f"{number}번째 줄: {drop}")
            continue
        reasons = _reject_reasons(line, context)
        if reasons:
            rejected.extend(f"{number}번째 줄: {reason}" for reason in reasons)
            continue
        kept.append(line)
    return Review(tuple(kept), tuple(dropped), tuple(rejected))


def review_lines(
    lines: Sequence[TimelineLine], context: HealthContext
) -> tuple[list[TimelineLine], list[str]]:
    """(통과한 줄, 사유). 지운 줄과 거절한 줄의 사유가 모두 들어 있다."""
    result = review(lines, context)
    return list(result.kept), [*result.dropped, *result.rejected]


def _drop_reason(line: TimelineLine, context: HealthContext) -> str | None:
    state = context.state
    ref = state.seen_refs.get(line.ref)
    if ref is None:
        return "ref 가 이번에 조회한 기록이 아니라서 지웠다"
    if ref.kind != _KIND_OF_SECTION[line.section]:
        return "구획과 ref 의 종류가 맞지 않아서 지웠다"
    if line.section == "symptom" and line.ref in state.ref_names:
        # 먹인 약 행도 종류는 observation_health 다 — 약 줄 검사를 피해 가는 길이라 막는다
        return "먹인 약 기록을 증상 줄의 근거로 달아서 지웠다"
    if line.section in _DRUG_SECTIONS and line.ref not in state.ref_names:
        return "약 이름 원문을 알 수 없는 ref 라서 지웠다"
    return None


def _reject_reasons(line: TimelineLine, context: HealthContext) -> list[str]:
    reasons: list[str] = []
    text = _squash(line.text)

    if any(word in text for word in FORBIDDEN_INTERPRETATION):
        reasons.append("원인 · 판단을 짐작하는 표현이 있다. 기록에 있는 사실만 쓴다")

    raw = _squash(context.state.ref_texts.get(line.ref, ""))
    if any(match.group(1) not in raw for match in FORBIDDEN_SUFFIX.finditer(line.text)) or any(
        name in text and name not in raw for name in FORBIDDEN_DISEASE_NAMES
    ):
        reasons.append("병 이름처럼 읽히는 말이 있다. 보호자가 그 말을 했을 때만 인용할 수 있다")
    if any(word in text and word not in raw for word in map(_squash, FORBIDDEN_MISSED)):
        reasons.append("약을 안 먹였다고 읽히는 말이 있다. 기록에 있는 사실만 쓴다")
    reasons += _date_reasons(line, context)

    # 용량 · 약 이름은 구획과 상관없이 그 줄 자신의 ref 원문에 있어야 한다 — 구획을 바꿔 달아 검사를
    # 피해 가지 못하게. 다른 기록의 약 이름을 끌어오는 것도 같은 길이다
    allowed = _doses(context.state.ref_texts.get(line.ref, ""))
    if any(found not in allowed for found in _doses(line.text)):
        reasons.append("약 용량이 그 줄의 기록 원문에 없다. 기록의 용량을 그대로 쓴다")
    if any(
        name in text and name not in raw
        for name in {_squash(n) for n in context.state.ref_names.values()}
        if name
    ):
        reasons.append("다른 기록의 약 이름이 들어 있다. 이 줄의 기록에 있는 약 이름만 쓴다")

    if line.section in _DRUG_SECTIONS:
        reasons += _drug_reasons(line, text, context)
    if line.section == "medication":
        reasons += _count_reasons(line, context)
    return reasons


def _drug_reasons(line: TimelineLine, text: str, context: HealthContext) -> list[str]:
    state = context.state
    reasons: list[str] = []
    if any(_squash(word) in text for word in FORBIDDEN_DOSE_GUESS):
        reasons.append(
            "체크 안 된 회차를 안 먹였다고 읽히게 썼거나 비율을 썼다. "
            "체크 n/m회 처럼 센 숫자만 쓴다 — 체크 안 됨은 안 먹임이 아니다"
        )
    if _squash(state.ref_names[line.ref]) not in text:
        reasons.append("약 이름이 등록된 원문과 다르다. 조회 결과의 이름을 그대로 쓴다")
    return reasons


def _date_reasons(line: TimelineLine, context: HealthContext) -> list[str]:
    """줄의 날짜는 코드가 정한 날짜다(루트 CLAUDE.md §3) — 모델이 고른 날짜로 정렬하지 않는다.

    - 글머리 "M/D" 는 모든 구획에서 `line.date` 와 같아야 한다
    - 증상 · 식사 · 먹인 약 줄의 `line.date` 는 그 ref 기록의 날짜 범위(`state.ref_days`) 안이어야
      한다. 복약 줄은 날짜별 체크 수 대조(`_count_reasons`)가 날짜를 본다
    """
    reasons: list[str] = []
    leading = _LEADING_DATE.match(line.text)
    if leading and (int(leading[1]), int(leading[2])) != (line.date.month, line.date.day):
        reasons.append("글머리 날짜가 줄의 날짜와 다르다")
    span = context.state.ref_days.get(line.ref)
    if line.section != "medication" and span is not None and not span[0] <= line.date <= span[1]:
        reasons.append("줄의 날짜가 그 기록의 날짜 범위 밖이다. 조회 결과의 날짜를 그대로 쓴다")
    return reasons


def _count_reasons(line: TimelineLine, context: HealthContext) -> list[str]:
    """복약 줄의 체크 수는 조회한 숫자와 같아야 한다. 숫자를 쓰지 않은 줄은 통과.

    "n/m회" 를 지우고 남은 "3회" 는 거절한다("3회 중 3회" 로 대조를 피해 가는 길). 등록 원문
    (용량 "1회 5ml" 등)에 있는 말이면 통과다.
    """
    reasons: list[str] = []
    tight = _BEFORE_TIMES.sub("", _COUNT_SPACING.sub("/", line.text))
    counts = _COUNT.findall(tight)
    if counts:
        stored = context.state.check_counts.get((line.ref, line.date))
        if stored is None or any((int(n), int(m)) != stored for n, m in counts):
            reasons.append(
                "체크 수가 조회한 날짜별 체크 수와 다르다. 조회 결과의 n/m회 그대로 쓴다"
            )
    raw = _squash(context.state.ref_texts.get(line.ref, ""))
    if any(bare not in raw for bare in _BARE_COUNT.findall(_COUNT.sub(" ", tight))):
        reasons.append("체크 수는 조회 결과의 n/m회 꼴로만 쓴다")
    return reasons


def _doses(text: str) -> set[str]:
    """글에 적힌 용량들 — 공백을 없애고 소문자로("5 ML" → "5ml").

    공백을 없애기 전 글에서 뽑는다. 먼저 붙이면 "9/27 5ml" 이 "9/275ml" 이 되어 날짜가 용량에
    붙는다.
    """
    return {_squash(found) for found in _DOSE.findall(text.lower())}


def _squash(text: str) -> str:
    """공백을 없앤다 — '안 먹' 과 '안먹', '5 ml' 과 '5ml' 을 같은 글자로 본다."""
    return _WHITESPACE.sub("", text)
