"""보호자 기록 원문을 화면에 그대로 내도 되는지 — 코드가 거르는 말의 표.

진료 준비 정리(`tools/visit_lines.py`)와 검진 맥락 요약(`tools/checkup_context.py`)이 기록 원문
(증상 · 조치 · 식사 · 먹인 약 이름)을 줄로 그릴 때 한 번 거른다. 해석 표현 · 병 이름처럼 읽히는 말 ·
측정 · 성장 평가 표현이 들었으면 원문을 내지 않고 `FLAGGED` 자리표시로 바꾼다 — 조용히 빼지도
않는다.
원문은 Memory 가 뽑은 문장이라 보호자가 한 말 그대로라고 보장하지 못한다 — 그래서 인용 예외가 없다.

eval 판정(`tests/eval/agents/health/judges.py`)도 같은 표로 결과 글을 본다.
"""

import re

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
# 병 이름처럼 읽히는 말(~염 · ~렴 · ~증).
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
# ~염 · ~증 꼴이 아닌 병 이름
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
# 약 줄 — 체크 안 됨은 안 먹임이 아니다 · 비율 금지
FORBIDDEN_DOSE_GUESS: tuple[str, ...] = ("안 먹", "못 먹", "빼먹", "놓쳤", "%")
# 약을 안 먹였다는 단정. 식사의 "밥은 못 먹음" 은 사실이라 여기 넣지 않는다
FORBIDDEN_MISSED: tuple[str, ...] = ("빼먹", "놓쳤", "안 먹였", "못 먹였")

# 측정 · 성장 평가 말 — 발달 평가로 읽힌다(루트 CLAUDE.md §2).
# "키" 는 낱말일 때만 본다: 뒤에 조사만 붙을 수 있어서 "키위" · "키즈" 는 걸리지 않는다
MEASUREMENT_WORDS: tuple[str, ...] = ("cm", "kg", "백분위", "또래", "성장곡선", "몸무게", "체중")
_HEIGHT = re.compile(r"(?<![가-힣])키(?:가|는|를|도|만|로|에|와|과|은|을)?(?![가-힣])")

# 걸린 기록 한 건 자리에 낼 중립 문구 — 사실만 말한다
FLAGGED = "기록 1건 (표현은 기록에서 확인)"

_WHITESPACE = re.compile(r"\s+")


def squash(text: str) -> str:
    """공백을 없앤다 — '안 먹' 과 '안먹', '5 ml' 과 '5ml' 을 같은 글자로 본다."""
    return _WHITESPACE.sub("", text)


def unsafe(text: str) -> bool:
    """해석 · 병 이름 · 측정 · 성장 평가 표현이 있다. 원문을 내보내지 않는다."""
    squashed = squash(text)
    return (
        any(word in squashed for word in FORBIDDEN_INTERPRETATION)
        or FORBIDDEN_SUFFIX.search(text) is not None
        or any(name in squashed for name in FORBIDDEN_DISEASE_NAMES)
        or any(word in squashed.lower() for word in MEASUREMENT_WORDS)
        or _HEIGHT.search(text) is not None
    )


def shown(text: str) -> str:
    """화면에 낼 글 — 걸리면 `FLAGGED`."""
    return FLAGGED if unsafe(text) else text
