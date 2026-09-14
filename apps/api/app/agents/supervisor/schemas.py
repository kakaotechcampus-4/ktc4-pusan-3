"""Supervisor 출력 스키마와 route tool 정의.

모델은 tool을 하나(`route`)만, 한 번 부른다. 그 인자가 곧 Supervisor 출력이다.
검증단계: JSON 파싱 → Pydantic(kind와 짝이 맞는 필드만) → 조각이 원문의 부분 문자열인지.

Agent의 내부(registry·tools·store)는 import하지 않는다.
"""

import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.common.tool_schema import ToolDefinition, build_tool_spec
from app.agents.food.schemas.common import FoodTaskType
from app.agents.memory.schemas.task import WorkType


class SegmentKind(StrEnum):
    RECORD = "record"  # Memory 몫(저장·조회·수정·삭제 전부)
    REQUEST = "request"  # 도메인 Agent 요청
    GUARDED = "guarded"  # 알레르기 등록 / 진단 문의 → 정해진 안내
    OUT_OF_SCOPE = "out_of_scope"  # 구매·예약·기관 연락·대신 발송
    UNCLEAR = "unclear"  # 무엇을 하라는지 알 수 없음


class DomainAgentName(StrEnum):
    """domains.suggestion.models.SuggestionAgent와 값이 같아야 한다."""

    FOOD = "food"
    ACTIVITY = "activity"
    GROWTH = "growth"
    HEALTH = "health"


class GuardReason(StrEnum):
    SAFETY_RECORD = "safety_record"  # 알레르기·만성질환 등록·수정은 보호자 직접 입력
    DIAGNOSIS = "diagnosis"  # 진단·결핍 판정·치료식·약·영양제 추천은 하지 않음


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Segment(_Strict):
    """발화에서 잘라낸 한 조각. kind에 맞는 필드만 채운다."""

    text: Annotated[
        str,
        Field(description="원문에서 잘라낸 구간. 고치거나 요약하지 않는다. 연결어는 빼도 된다"),
    ]
    kind: Annotated[
        SegmentKind,
        Field(
            description=(
                "record(저장·조회·수정·삭제) / request(도메인 Agent 요청) / "
                "guarded(알레르기 등록·진단 문의) / out_of_scope(구매·예약·대신 발송) / unclear"
            )
        ),
    ]
    work: Annotated[
        WorkType | None,
        Field(
            default=None,
            description="kind=record 일 때만. observe / schedule / lookup_edit",
        ),
    ]
    agent: Annotated[
        DomainAgentName | None,
        Field(
            default=None,
            description="kind=request 일 때만. food / activity / growth / health",
        ),
    ]
    food_task: Annotated[
        FoodTaskType | None,
        Field(
            default=None,
            description=(
                "agent=food 일 때만. meal_recommendation(뭘 먹일까·메뉴·간식) / "
                "nutrient_analysis(영양 괜찮은지·영양소·과잉/부족)"
            ),
        ),
    ]
    guard: Annotated[
        GuardReason | None,
        Field(
            default=None,
            description="kind=guarded일 때만. safety_record / diagnosis",
        ),
    ]

    @model_validator(mode="after")
    def _pair(self) -> "Segment":
        """kind 와 짝이 맞지 않는 필드가 있으면 거절한다.

        라벨만 맞고 필드가 비면 routing 이 조각을 어디로 보낼지 알 수 없고,
        반대로 엉뚱한 필드가 차 있으면 (예: activity인데 food_task) 뒤에서 무시된다.
        """
        required = {
            SegmentKind.RECORD: "work",
            SegmentKind.REQUEST: "agent",
            SegmentKind.GUARDED: "guard",
        }.get(SegmentKind(self.kind))
        for name in ("work", "agent", "guard"):
            value = getattr(self, name)
            if name == required and value is None:
                raise ValueError(f"kind={self.kind}이면 {name}가 필요하다")
            if name != required and value is not None:
                raise ValueError(f"kind={self.kind}에는 {name}를 넣지 않는다")

        is_food = self.kind == SegmentKind.REQUEST and self.agent == DomainAgentName.FOOD
        if is_food and self.food_task is None:
            raise ValueError("agent=food이면 food_task가 필요하다")
        if not is_food and self.food_task is not None:
            raise ValueError("food_task는 agent=food일 때만 넣는다")
        return self


class SupervisorOutput(_Strict):
    segments: Annotated[
        list[Segment],
        Field(min_length=1, description="입력에 담긴 조각 목록. 하나뿐이어도 배열로 준다"),
    ]


ROUTE_TOOL = ToolDefinition(
    name="route",
    description=(
        "보호자 발화를 조각으로 나누고 각 조각을 어디로 보낼지 라벨을 붙인다. "
        "이 tool을 한 번만 부른다. 저장·조회·추천은 하지 않는다."
    ),
    args=SupervisorOutput,
)
ROUTE_TOOL_SPEC = build_tool_spec(ROUTE_TOOL)

# 검증 실패 코드 — 모두 "Memory 단독으로 강등" 으로 이어진다 (재시도하지 않는다)
NOT_SUBSTRING = "NOT_SUBSTRING"
EMPTY_TEXT = "EMPTY_TEXT"


Range = tuple[int, int]

# 공유 서술어: "저녁 메뉴랑 주말 놀이 추천해줘" 에서 "저녁 메뉴 추천해줘" 를 허용하는 조건
# 건너뛴 구간이 이 말로 시작해야 한다 (요청을 나열하는 접속)
_CONJUNCTIONS = ("이랑", "랑", "하고", "이나", "나", "과", "와", "도", "및", ",")
# 건너뛴 구간에 이 말이 있으면 거절: 빼면 뜻이 뒤집힌다 ("우유 말고 추천해줘")
_NEGATION_WORDS = ("안", "못")
_NEGATION_PARTS = ("말고", "말구", "빼고", "없", "제외", "싫")
_SENTENCE_END = re.compile(r"^\s*(?:[.?!…~]|$)")


def normalize(text: str) -> str:
    """공백을 한 칸으로 줄이고 앞뒤를 자른다. 부분 문자열 비교의 기준."""
    return re.sub(r"\s+", " ", text).strip()


def align_segment(raw_text: str, segment: "Segment") -> list[Range] | None:
    """조각이 원문의 어느 구간에서 왔는지 나타낸다. 찾을 수 없으면 None(= 의역함).

    - 기본적으로 모든 조각은 원문에서 연속된 부분 문자열이어야 하며, 해당 구간 하나를 반환한다.
    - request 조각에 한해서는 "앞부분 + 같은 문장 끝의 서술어"처럼 두 구간을 조합하는 것도 허용한다.
    예를 들어 "A랑 B 추천해줘"를 "A 추천해줘", "B 추천해줘"로 나누는 것은
    모델이 자연스럽게 수행하는 분리이고, 각 Agent에도 완결된 요청을 전달하는 편이 낫다.
    - 단, 건너뛴 구간이 접속 조사나 연결 표현(랑·하고·도·, …)으로 시작하고,
    부정 표현(안·못·말고·빼고·없…)을 포함하지 않으며, 두 구간이 같은 문장 안에 있을 때만 허용한다.
    - record 조각에는 이 예외를 적용하지 않는다. 기록 문장에서 일부 표현을 생략하면 의미가 달라질 수 있기 때문이다.
    """
    haystack = normalize(raw_text)
    text = normalize(segment.text)
    if not text:
        return None
    start = haystack.find(text)
    if start >= 0:
        return [(start, start + len(text))]
    if segment.kind != SegmentKind.REQUEST:
        return None
    return _shared_predicate(haystack, text)


def _shared_predicate(haystack: str, text: str) -> list[Range] | None:
    words = text.split(" ")
    for cut in range(1, len(words)):
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        for head_start in _occurrences(haystack, head):
            head_end = head_start + len(head)
            for tail_start in _occurrences(haystack, tail, after=head_end):
                gap = haystack[head_end:tail_start]
                tail_end = tail_start + len(tail)
                if _is_listing_gap(gap) and _SENTENCE_END.match(haystack[tail_end:]):
                    return [(head_start, head_end), (tail_start, tail_end)]
    return None


def _occurrences(haystack: str, needle: str, after: int = 0) -> list[int]:
    found, start = [], haystack.find(needle, after)
    while start >= 0:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def _is_listing_gap(gap: str) -> bool:
    """건너뛴 구간이 "나열" 인지. 한 문장 안 · 접속 조사로 시작 · 부정어 없음."""
    stripped = gap.strip()
    if not stripped or re.search(r"[.?!]", stripped):
        return False
    # 첫 단어가 통째로 접속 조사여야 함. "나중에" 가 "나"로 시작한다고 통과시키는 것 방지
    first = stripped.split()[0].rstrip(",")
    if not (stripped.startswith(",") or first in _CONJUNCTIONS):
        return False
    tokens = stripped.replace(",", " ").split()
    if any(token in _NEGATION_WORDS for token in tokens):
        return False
    return not any(part in stripped for part in _NEGATION_PARTS)


def validate_segments(raw_text: str, output: SupervisorOutput) -> list[tuple[int, str]]:
    """조각이 원문에서 그대로 추출된 것인지 검사한다. 의역된 조각은 실패 처리한다.

    의역 과정에서 "했대" 같은 출처 단서가 사라질 수 있고, Memory가 이를 바탕으로
    출처 라벨을 결정하므로 조각은 원문 표현을 그대로 유지해야 한다.
    단, request 조각에서 공유 서술어를 붙이는 경우는 예외로 허용한다.

    반환값은 (조각 번호, 실패 코드) 목록이다.
    조각 번호만으로 실패 위치를 식별할 수 있어 로그에 원문을 남기지 않아도 된다.
    """
    failures: list[tuple[int, str]] = []
    for index, segment in enumerate(output.segments, start=1):
        if not normalize(segment.text):
            failures.append((index, EMPTY_TEXT))
        elif align_segment(raw_text, segment) is None:
            failures.append((index, NOT_SUBSTRING))
    return failures
