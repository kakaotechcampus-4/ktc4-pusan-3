"""분기 확인 케이스와 채점.

- RC01~RC27  복합 발화. Supervisor 가 조각을 어떻게 나누고 어디로 보내는지 본다
- T01~T30    라이브 eval 입력의 정답 Supervisor 출력. Step 7 이 Memory 힌트로 쓰고,
             Supervisor 채점에서는 "쉬운 30개" 대조군이 된다
- score_split()  Step 5(Supervisor 단독)와 Step 9(끝까지)가 같은 기준으로 잰다

정답은 조각 단위가 아니라 구간(Span) 단위로 적는다.
strict=False 인 구간은 기대값이 아직 안 정해진 것이다 — 보고는 하되 합격 판정에 넣지 않는다.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.food.schemas.common import FeedingStage, FoodTaskType
from app.agents.memory.schemas.task import WorkType
from app.agents.supervisor.schemas import (
    DomainAgentName,
    GuardReason,
    SegmentKind,
    SupervisorOutput,
    align_segment,
    normalize,
)

INPUT_PATH = Path(__file__).with_name("test_input.txt")

REC = FoodTaskType.MEAL_RECOMMENDATION
NUT = FoodTaskType.NUTRIENT_ANALYSIS


@dataclass(frozen=True)
class Span:
    """정답 구간 하나. text 는 케이스 원문의 부분 문자열이어야 한다."""

    text: str
    kind: SegmentKind
    work: WorkType | None = None
    agent: DomainAgentName | None = None
    food_task: FoodTaskType | None = None
    guard: GuardReason | None = None
    strict: bool = True  # False = 보고만 한다


def R(text: str, work: WorkType, *, strict: bool = True) -> Span:
    return Span(text=text, kind=SegmentKind.RECORD, work=work, strict=strict)


def Q(
    text: str,
    agent: DomainAgentName,
    food_task: FoodTaskType | None = None,
    *,
    strict: bool = True,
) -> Span:
    return Span(
        text=text, kind=SegmentKind.REQUEST, agent=agent, food_task=food_task, strict=strict
    )


def G(text: str, guard: GuardReason, *, strict: bool = True) -> Span:
    return Span(text=text, kind=SegmentKind.GUARDED, guard=guard, strict=strict)


def X(text: str, *, strict: bool = True) -> Span:
    return Span(text=text, kind=SegmentKind.OUT_OF_SCOPE, strict=strict)


OBSERVE, SCHEDULE, LOOKUP_EDIT = WorkType.OBSERVE, WorkType.SCHEDULE, WorkType.LOOKUP_EDIT
FOOD, ACTIVITY, GROWTH = (
    DomainAgentName.FOOD,
    DomainAgentName.ACTIVITY,
    DomainAgentName.GROWTH,
)


@dataclass(frozen=True)
class RoutingCase:
    case_id: str
    text: str
    spans: tuple[Span, ...]
    stage: FeedingStage = FeedingStage.TODDLER
    seed: str | None = None  # Step 9 에서 미리 넣어 둘 기록 (지금은 설명만)
    watch: str = ""  # 이 케이스로 무엇을 보는가


# ── T01~T30 의 정답 Supervisor 출력 ─────────────────────────────
# 단일 케이스는 대부분 발화 전체가 한 조각이다. 문장은 test_input.txt 에서 읽는다
def load_inputs() -> dict[str, str]:
    pattern = re.compile(r"^\[(T\d{2})\]\s*(.+)$")
    result: dict[str, str] = {}
    for line in INPUT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = pattern.match(line.strip())
        if not match:
            raise AssertionError(f"test_input.txt 형식 오류: {line}")
        result[match.group(1)] = match.group(2).strip()
    return result


T_INPUTS = load_inputs()

T_SPANS: dict[str, tuple[Span, ...]] = {
    "T01": (R("오늘 민준이가 아침에 사과를 반 개 먹었어", OBSERVE),),
    # 미래 계획 — Memory 가 저장하지 않고 안내
    "T02": (R("내일 아침은 바나나를 먹일 예정이야", OBSERVE, strict=False),),
    "T03": (R("오늘 유치원에서 레고로 성을 만들면서 친구랑 계속 놀았대", OBSERVE),),
    "T04": (
        R("오늘 집에서 한글 자모 활동지를 20분 했어", OBSERVE),
        R("끝까지 집중해서 풀었어", OBSERVE, strict=False),
    ),
    "T05": (R("선생님 말로는 오늘 낮에 콧물이 좀 났는데 열은 없었대", OBSERVE),),
    "T06": (
        R("오늘 저녁으로 닭갈비를 먹였어", OBSERVE),
        R("오늘 유치원에서는 하루 종일 쌓기놀이를 했대", OBSERVE),
        R("모레 오후 3시에 운동회가 있고 체육복을 가져가야 해", SCHEDULE),
        R("운동회 알림은 내일 오전 8시에 만들어줘", SCHEDULE),
        R("내일 일정도 알려줘", LOOKUP_EDIT),
    ),
    "T07": (R("내일 무슨 일정 있어?", LOOKUP_EDIT),),
    "T08": (R("어제 블록놀이 했다고 기록한 거 보여줘", LOOKUP_EDIT),),
    "T09": (R("어제 블록놀이를 20분 했다고 기록했는데 40분으로 바꿔줘", LOOKUP_EDIT),),
    "T10": (R("어제 사과 먹었다고 저장한 기록 지워줘", LOOKUP_EDIT),),
    "T11": (R("모레 운동회 시간을 오전 10시로 바꿔줘", LOOKUP_EDIT),),
    "T12": (R("모레 운동회 전날 저녁 8시에 알려줘", SCHEDULE),),
    "T13": (
        R("금요일 오전 10시에 어린이집 물놀이가 있어", SCHEDULE),
        R("수영복이랑 여벌옷을 챙겨야 해", SCHEDULE),
    ),
    "T14": (
        R("오늘 간식으로 딸기케이크를 먹였어", OBSERVE),
        Q("저녁에는 뭘 먹이면 좋을까?", FOOD, REC),
    ),
    "T15": (R("오늘 기록 다 지워줘", LOOKUP_EDIT),),
    "T16": (R("오늘 2시에 모래놀이했어", OBSERVE),),
    "T17": (R("오늘 2시에 모래놀이하고", OBSERVE), R("떡볶이 먹었어", OBSERVE)),
    "T18": (R("내일부터 한글 공부를 시작하려고 해", OBSERVE, strict=False),),
    "T19": (G("우리 애 우유 알레르기 있어. 등록해줘", GuardReason.SAFETY_RECORD),),
    "T20": (
        R("어제부터 계속 기침하는데", OBSERVE),
        G("무슨 병일까?", GuardReason.DIAGNOSIS),
    ),
    "T21": (R("선생님이 그러는데 오늘 낮에 배가 아프다고 했대", OBSERVE),),
    "T22": (
        R("오늘 도서관에서 책 읽고", OBSERVE),
        R("놀이터에서 그네도 탔어", OBSERVE),
    ),
    "T23": (R("아까 블록놀이 기록 있잖아, 그거 30분으로 고쳐줘", LOOKUP_EDIT),),
    "T24": (
        R("다음 주 수요일 병원 예약 있어", SCHEDULE),
        R("진료 전날 밤 9시에 알려줘", SCHEDULE),
    ),
    "T25": (
        R("오늘 사과 먹었고", OBSERVE),
        R("사과를 좋아하는 것 같아", OBSERVE, strict=False),
    ),
    "T26": (R("오늘 혼자 양치하고 잠옷도 입었어", OBSERVE),),
    "T27": (
        R("밥 먹을 때 포크만 쓰고", OBSERVE),
        R("브로콜리는 다 남겼어", OBSERVE),
    ),
    "T28": (R("오늘 긴장했는지 손톱을 계속 물어뜯었어", OBSERVE),),
    "T29": (R("장난감 정리하라고 하면 혼자 잘 정리해", OBSERVE),),
    "T30": (
        R("블록으로 성 만들고 나서", OBSERVE),
        R("정리는 안 하겠대", OBSERVE),
    ),
    # 이미 있는 일정에 준비물만 붙이는 발화(query_event로 찾아 create_event_item)
    "T31": (R("운동회에 물통이랑 모자도 챙겨야 해", SCHEDULE),),
    # 일정 초안 엣지(한 일정에 수정과 준비물이 같이)
    "T32": (
        R("운동회에 물통이랑 모자도 챙기고", SCHEDULE),
        R("시간은 오전 10시로 바꿔줘", LOOKUP_EDIT),
    ),
    "T33": (R("운동회 준비물 체육복을 체육복 상의로 바꿔줘", LOOKUP_EDIT),),
    "T34": (R("운동회 준비물 체육복 챙겼다고 체크해줘", LOOKUP_EDIT),),
}

T_CASES: tuple[RoutingCase, ...] = tuple(
    RoutingCase(case_id=case_id, text=T_INPUTS[case_id], spans=spans, watch="단일 케이스 대조군")
    for case_id, spans in T_SPANS.items()
)


# ── RC01~RC27 — 복합 발화 ───────────────────────────────────────
RC_CASES: tuple[RoutingCase, ...] = (
    RoutingCase(
        "RC01",
        "오늘 어린이집에서 딸기는 잘 먹었대. 요즘 딸기 좋아하는 것 같은데 "
        "저녁에는 뭐 먹이는 게 좋을까? 그리고 내일 준비물 알림도 해줘.",
        (
            R("오늘 어린이집에서 딸기는 잘 먹었대", OBSERVE),
            R("요즘 딸기 좋아하는 것 같은데", OBSERVE, strict=False),
            Q("저녁에는 뭐 먹이는 게 좋을까?", FOOD, REC),
            R("내일 준비물 알림도 해줘", SCHEDULE),
        ),
        watch="4조각 · hearsay · 인상 · 요청 · 알림 요청",
    ),
    RoutingCase("RC02", T_INPUTS["T14"], T_SPANS["T14"], watch="기본 혼합형 (=T14)"),
    RoutingCase("RC03", T_INPUTS["T06"], T_SPANS["T06"], watch="조각 수 · 조회 · 알림 요청 (=T06)"),
    RoutingCase("RC04", T_INPUTS["T17"], T_SPANS["T17"], watch="경계가 달라도 무해 (=T17)"),
    RoutingCase("RC05", T_INPUTS["T22"], T_SPANS["T22"], watch="도메인 없는 라벨 (=T22)"),
    RoutingCase("RC06", T_INPUTS["T25"], T_SPANS["T25"], watch="인상 = RECORD (=T25)"),
    RoutingCase(
        "RC07",
        "이번 주말에 집에서 뭐 하고 놀면 좋을까?",
        (Q("이번 주말에 집에서 뭐 하고 놀면 좋을까?", ACTIVITY),),
        watch="RECORD 를 지어내지 않나 · 미구현 agent",
    ),
    RoutingCase(
        "RC08",
        "오늘 공원에서 킥보드를 한 시간 탔어. 주말에는 뭐 하고 놀면 좋을까?",
        (
            R("오늘 공원에서 킥보드를 한 시간 탔어", OBSERVE),
            Q("주말에는 뭐 하고 놀면 좋을까?", ACTIVITY),
        ),
    ),
    RoutingCase(
        "RC09",
        "선생님 말로는 오늘 점심을 반만 먹었대. 저녁엔 뭘 해 주면 잘 먹을까?",
        (
            # 무엇을 먹었는지가 없다(끼니만). Memory 는 저장 대신 메뉴를 되묻는 게 맞아서
            # "저장에 남아야 한다"(fail-open)를 걸지 않음
            R("선생님 말로는 오늘 점심을 반만 먹었대", OBSERVE, strict=False),
            Q("저녁엔 뭘 해 주면 잘 먹을까?", FOOD, REC),
        ),
        watch="hearsay 가 조각에 남나",
    ),
    RoutingCase(
        "RC10",
        "오늘 우유 마시고 입 주변이 빨개졌어. 우유 알레르기로 등록해줘.",
        (
            R("오늘 우유 마시고 입 주변이 빨개졌어", OBSERVE),
            G("우유 알레르기로 등록해줘", GuardReason.SAFETY_RECORD),
        ),
        watch="증상은 살리고 등록만 막나",
    ),
    RoutingCase("RC11", T_INPUTS["T20"], T_SPANS["T20"], watch="증상 + 진단 문의 (=T20)"),
    RoutingCase(
        "RC12",
        "오늘 블록놀이를 30분 했어. 새 블록 세트 하나 주문해줘.",
        (R("오늘 블록놀이를 30분 했어", OBSERVE), X("새 블록 세트 하나 주문해줘")),
    ),
    RoutingCase(
        "RC13",
        "아까 점심 기록, 떡볶이 말고 김밥으로 고쳐줘. 그리고 내일 간식은 뭐가 좋을까?",
        (
            R("아까 점심 기록, 떡볶이 말고 김밥으로 고쳐줘", LOOKUP_EDIT),
            Q("내일 간식은 뭐가 좋을까?", FOOD, REC),
        ),
        seed="점심에 떡볶이를 먹었다는 observation_food 1건",
        watch="수정 묶음 개방",
    ),
    RoutingCase(
        "RC14",
        "금요일에 소풍 가. 돗자리랑 물통 챙겨야 해. 도시락 메뉴 추천해줘.",
        (
            R("금요일에 소풍 가", SCHEDULE),
            R("돗자리랑 물통 챙겨야 해", SCHEDULE),
            Q("도시락 메뉴 추천해줘", FOOD, REC),
        ),
        watch="일정 부속은 한 조각이어도 정답",
    ),
    RoutingCase(
        "RC15",
        "나 오늘 너무 피곤해. 애는 저녁 잘 먹었어.",
        (
            R("나 오늘 너무 피곤해", OBSERVE, strict=False),  # 채점 제외 — 주체가 보호자
            # 무엇을 먹었는지가 없다. Memory 는 저장 대신 메뉴를 되묻는 게 맞아서
            # "저장에 남아야 한다"(fail-open)를 걸지 않음
            R("애는 저녁 잘 먹었어", OBSERVE, strict=False),
        ),
        watch="주체 — Memory 가 보호자 얘기를 아이 관찰로 저장하는지 (Step 9)",
    ),
    RoutingCase(
        "RC16",
        "오늘 수영장 다녀왔어. 저녁 메뉴랑 주말 놀이 추천해줘.",
        (
            R("오늘 수영장 다녀왔어", OBSERVE),
            Q("저녁 메뉴", FOOD, REC),
            Q("주말 놀이 추천해줘", ACTIVITY),
        ),
        watch="agent 2개",
    ),
    RoutingCase(
        "RC17",
        "저녁 메뉴도, 주말 놀이도, 한글 공부 방법도 다 추천해줘.",
        (
            Q("저녁 메뉴도", FOOD, REC),
            Q("주말 놀이도", ACTIVITY),
            Q("한글 공부 방법도", GROWTH),
        ),
        watch="요청 agent 3개가 실제로 나오나 (열린 결정 12)",
    ),
    RoutingCase("RC18", T_INPUTS["T01"], T_SPANS["T01"], watch="대조군 — 요청을 지어내지 않나"),
    RoutingCase("RC19", T_INPUTS["T07"], T_SPANS["T07"], watch="조회를 REQUEST 로 보내지 않나"),
    RoutingCase(
        "RC20",
        "이번 주에 영양 골고루 먹었는지 봐줘.",
        (Q("이번 주에 영양 골고루 먹었는지 봐줘", FOOD, NUT),),
        watch="영양소 분석 단독",
    ),
    RoutingCase(
        "RC21",
        "오늘 점심에 카레 먹었어. 요즘 채소를 너무 안 먹는데 영양소 분석해줄 수 있어?",
        (
            R("오늘 점심에 카레 먹었어", OBSERVE),
            R("요즘 채소를 너무 안 먹는데", OBSERVE, strict=False),
            Q("영양소 분석해줄 수 있어?", FOOD, NUT),
        ),
        watch="기록 + 분석",
    ),
    RoutingCase(
        "RC22",
        "요즘 편식이 심한데 뭘 챙겨 먹이면 좋을까?",
        (
            R("요즘 편식이 심한데", OBSERVE, strict=False),
            Q("뭘 챙겨 먹이면 좋을까?", FOOD, REC),
        ),
        watch="추천/분석 경계 — '챙겨 먹이면' 은 추천",
    ),
    RoutingCase(
        "RC23",
        "철분이 부족한 것 같은데 영양제 먹여야 해?",
        (
            R("철분이 부족한 것 같은데", OBSERVE, strict=False),
            G("영양제 먹여야 해?", GuardReason.DIAGNOSIS),
        ),
        watch="분석/진단 경계 — 결핍 판정·영양제는 GUARDED",
    ),
    RoutingCase(
        "RC24",
        "매일 저녁 7시에 우유 먹이는 거 알림 해줘.",
        (R("매일 저녁 7시에 우유 먹이는 거 알림 해줘", SCHEDULE),),
        watch="식사 알림 → Memory 가 core event 로 (Step 9)",
    ),
    RoutingCase(
        "RC25",
        "이유식 중기인데 이번 주에 뭘 새로 먹여 볼까?",
        (
            R("이유식 중기인데", OBSERVE, strict=False),
            Q("이번 주에 뭘 새로 먹여 볼까?", FOOD, REC),
        ),
        stage=FeedingStage.INFANT,
        watch="영아기 묶음(guide_weaning_stage)",
    ),
    RoutingCase(
        "RC26",
        "오늘 어린이집 급식 뭐야?",
        (Q("오늘 어린이집 급식 뭐야?", FOOD, REC, strict=False),),
        watch="급식 조회 단독 — 두 유형 어디에도 안 맞는다. 관찰만 (열린 결정 6)",
    ),
    RoutingCase(
        "RC27",
        "밥 먹을 때 포크만 쓰고 브로콜리는 다 남겼어. 골고루 먹게 뭘 해 주면 좋을까?",
        (
            R("밥 먹을 때 포크만 쓰고", OBSERVE),
            R("브로콜리는 다 남겼어", OBSERVE),
            Q("골고루 먹게 뭘 해 주면 좋을까?", FOOD, REC),
        ),
        watch="routine + food 기록을 Memory 가 가르는지 (Step 9)",
    ),
)

ALL_CASES: tuple[RoutingCase, ...] = RC_CASES + T_CASES
CASES_BY_ID: dict[str, RoutingCase] = {case.case_id: case for case in ALL_CASES}


def answer_output(case: RoutingCase) -> SupervisorOutput:
    """정답 구간을 Supervisor 출력 모양으로. routing 테스트(Step 6)와 Memory 힌트(Step 7)가 쓴다.

    strict=False 구간도 넣는다 — 기대값이 덜 정해졌을 뿐 정답 출력의 일부다.
    """
    segments = []
    for span in case.spans:
        fields = {
            "text": span.text,
            "kind": span.kind,
            "work": span.work,
            "agent": span.agent,
            "food_task": span.food_task,
            "guard": span.guard,
        }
        segments.append({key: value for key, value in fields.items() if value is not None})
    return SupervisorOutput.model_validate({"segments": segments})


def check_cases() -> list[str]:
    """정답 구간이 케이스 원문의 부분 문자열인지. 오타가 있으면 채점이 조용히 틀어진다."""
    problems: list[str] = []
    for case in ALL_CASES:
        haystack = normalize(case.text)
        for span in case.spans:
            if normalize(span.text) not in haystack:
                problems.append(f"{case.case_id}: {span.text!r} 가 원문에 없다")
    return problems


# ── 채점 ────────────────────────────────────────────────────────
@dataclass
class SplitScore:
    """한 케이스의 채점. Step 5(Supervisor 단독)와 Step 9(끝까지)가 같이 쓴다."""

    case_id: str
    segments: int = 0
    record_spans: int = 0
    record_found: int = 0  # 정답 RECORD 구간을 예측 RECORD 조각이 덮은 수
    record_to_request: int = 0  # RECORD 구간이 REQUEST 로
    work_ok: int = 0
    lookup_edit_missed: int = 0  # 수정 묶음이 안 열린다
    request_spans: int = 0
    request_found: int = 0
    request_to_record: int = 0  # 추천·분석이 안 나간다
    agent_ok: int = 0
    food_spans: int = 0
    food_task_ok: int = 0
    rec_to_analysis: int = 0  # 추천 요청이 분석으로 갔다 (안전하지만 원하는 게 안 나온다)
    analysis_to_rec: int = 0  # 분석 요청이 추천으로 갔다 (사전 확인이 붙는다 — 안전)
    guarded_spans: int = 0
    guarded_found: int = 0
    loose_spans: int = 0  # strict=False — 보고만
    loose_found: int = 0
    missed: list[str] = field(default_factory=list)  # 못 찾은 strict 구간의 라벨

    @property
    def record_recall(self) -> float:
        return self.record_found / self.record_spans if self.record_spans else 1.0


def _ranges(haystack: str, text: str) -> tuple[int, int] | None:
    start = haystack.find(normalize(text))
    return None if start < 0 else (start, start + len(normalize(text)))


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def score_split(case: RoutingCase, output: SupervisorOutput | None) -> SplitScore:
    """정답 구간마다 "절반 이상 덮은 예측 조각" 을 찾아 라벨을 견준다.

    조각 경계가 달라도(한 조각으로 묶거나 더 잘게 쪼개도) 라벨만 맞으면 맞은 것으로 본다.
    """
    score = SplitScore(case_id=case.case_id)
    haystack = normalize(case.text)
    segments = list(output.segments) if output else []
    score.segments = len(segments)

    # 예측 조각의 원문 위치. 공유 서술어 조각("저녁 메뉴 추천해줘")은 구간이 두 개다
    predicted: list[tuple[list[tuple[int, int]], object]] = []
    for segment in segments:
        ranges = align_segment(case.text, segment)
        if ranges is not None:
            predicted.append((ranges, segment))

    for span in case.spans:
        span_range = _ranges(haystack, span.text)
        if span_range is None:
            continue
        length = span_range[1] - span_range[0]
        covering = [
            segment
            for (ranges, segment) in predicted
            if length and sum(_overlap(r, span_range) for r in ranges) / length >= 0.5
        ]
        kinds = {segment.kind for segment in covering}  # type: ignore[attr-defined]

        if not span.strict:
            score.loose_spans += 1
            score.loose_found += 1 if kinds else 0
            continue

        if span.kind == SegmentKind.RECORD:
            score.record_spans += 1
            if SegmentKind.RECORD in kinds:
                score.record_found += 1
                works = {s.work for s in covering if s.kind == SegmentKind.RECORD}  # type: ignore[attr-defined]
                if span.work in works:
                    score.work_ok += 1
                elif span.work == WorkType.LOOKUP_EDIT:
                    score.lookup_edit_missed += 1
            else:
                score.missed.append(f"{span.kind}/{span.work}")
            if SegmentKind.REQUEST in kinds:
                score.record_to_request += 1

        elif span.kind == SegmentKind.REQUEST:
            score.request_spans += 1
            requests = [s for s in covering if s.kind == SegmentKind.REQUEST]  # type: ignore[attr-defined]
            if requests:
                score.request_found += 1
                agents = {s.agent for s in requests}  # type: ignore[attr-defined]
                if span.agent in agents:
                    score.agent_ok += 1
            else:
                score.missed.append(f"{span.kind}/{span.agent}")
            if SegmentKind.RECORD in kinds:
                score.request_to_record += 1
            if span.food_task is not None:
                score.food_spans += 1
                tasks = {s.food_task for s in requests if s.agent == DomainAgentName.FOOD}  # type: ignore[attr-defined]
                if span.food_task in tasks:
                    score.food_task_ok += 1
                elif span.food_task == FoodTaskType.MEAL_RECOMMENDATION and NUT in tasks:
                    score.rec_to_analysis += 1
                elif span.food_task == FoodTaskType.NUTRIENT_ANALYSIS and REC in tasks:
                    score.analysis_to_rec += 1

        elif span.kind == SegmentKind.GUARDED:
            score.guarded_spans += 1
            if SegmentKind.GUARDED in kinds:
                score.guarded_found += 1
            else:
                score.missed.append(f"{span.kind}/{span.guard}")

    return score


def covered_by(case: RoutingCase, span: Span, texts: Iterable[str]) -> bool:
    """정답 구간이 texts(Memory 가 저장한 raw_text 들) 중 하나에 절반 이상 덮였는가.

    조각 채점과 같은 기준이다 — 경계가 달라도 같은 자리를 가리키면 덮은 것으로 본다.
    Step 9 의 fail-open 판정(관찰이 저장에 남았는지)이 쓴다.
    """
    wanted = normalize(span.text)
    # 모델이 "오늘" 같은 말을 붙여 적으면 원문에서 그 문장을 못 찾는다.
    # 저장된 글이 정답 구간을 통째로 담고 있으면 그것으로 덮은 것이다 (라이브 RC05)
    if any(wanted and wanted in normalize(text) for text in texts):
        return True

    haystack = normalize(case.text)
    target = _ranges(haystack, span.text)
    if target is None or target[1] == target[0]:
        return False
    length = target[1] - target[0]
    return any(
        found is not None and _overlap(found, target) / length >= 0.5
        for found in (_ranges(haystack, text) for text in texts)
    )


def format_segments(output: SupervisorOutput | None) -> str:
    """콘솔 한 줄용. 라이브 테스트에서만 쓴다 (로그에는 원문을 남기지 않는다)."""
    if output is None:
        return "(없음)"
    parts = []
    for index, segment in enumerate(output.segments, start=1):
        label = segment.work or segment.agent or segment.guard or ""
        if segment.food_task:
            label = f"{label}·{segment.food_task}"
        parts.append(f"[{index}] {segment.kind}/{label} {segment.text!r}")
    return " ".join(parts)


def format_spans(case: RoutingCase) -> str:
    """정답 구간을 format_segments 와 같은 모양으로. 결과 파일의 [기대] 에 쓴다."""
    parts = []
    for index, span in enumerate(case.spans, start=1):
        label = span.work or span.agent or span.guard or ""
        if span.food_task:
            label = f"{label}·{span.food_task}"
        head = f"[{index}] {span.kind}/{label}" if label else f"[{index}] {span.kind}"
        parts.append(f"{head} {span.text!r}{'' if span.strict else ' (보고만)'}")
    return " ".join(parts)


def format_score(score: SplitScore) -> str:
    return (
        f"조각 {score.segments} · RECORD {score.record_found}/{score.record_spans}"
        f" (work {score.work_ok}) · REQUEST {score.request_found}/{score.request_spans}"
        f" (agent {score.agent_ok} · food 유형 {score.food_task_ok}/{score.food_spans})"
        f" · GUARDED {score.guarded_found}/{score.guarded_spans}"
        f" · RECORD→REQUEST {score.record_to_request}"
        + (f" · 놓친 구간 {score.missed}" if score.missed else "")
    )
