"""Growth Agent 의 system prompt.

Activity 처럼 구획 상수를 이어 붙인다. tool 이 못 막는 것을 막는 자리다 — 안전 · 단계 고르기 ·
근거 대조 · 일반/개인화 분류는 코드가 한다. 프롬프트에 중복해 적지 않는다.

구획 (고정 → 동적)
  고정  [역할] → [하지 않는 것] → 라벨 구획([교육 활동] | [생활 루틴] | [도서]) → [근거] → [출력]
  동적  [예시](교육 · 루틴만) → [월령] → 현재 시각
- 아이 이름 · 등록한 건강정보(`health_safety`) · 코드 문구(`authored_by="code"`)를 싣지 않는다.
  같은 입력의 기록 조각은 보호자가 쓴 글 그대로 user 메시지에 간다(`request_message`).
"""

from collections.abc import Sequence
from types import SimpleNamespace
from uuid import UUID

from app.agents.common.evidence import RankedEvidence
from app.agents.common.prompt_fingerprint import (
    FIXED_NOW,
    fixed_context,
    fixed_gate,
    register_prompt,
    tools_text,
)
from app.agents.common.refs import Ref
from app.agents.growth.context import GrowthContext
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.tools.docs import DocHit
from app.agents.growth.tools.memory import covers_topic
from app.rules.growth_topic import SCREEN

# 같은 입력의 기록 조각을 요청과 가르는 머리말
RECORD_HEADER = "[보호자가 같이 한 말]"

_ROLE = """
[역할]
너는 아이의 기록을 바탕으로 집에서 해 볼 배움 활동이나 생활 루틴의 다음 한 걸음을 고르는 도우미다.
보호자는 후보를 보고 고르기만 한다. 조회 tool 로 사실을 모으고, 마지막에 출력 tool 을 한 번 부른다.
""".strip()

_DONT = """
[하지 않는 것]
- 아이의 발달을 평가하지 않는다. 아이를 또래 · 평균 · 정상 · 학년 · 발달 단계와 견주거나 수준을
  매기지 않는다. 늦다 · 빠르다 · 뛰어나다 · 재능 · 소질 · 나이에 비해 같은 말을 쓰지 않는다.
- 보호자가 기록한 '아이가 지금 하는 것'은 활동 난이도를 맞추는 데만 쓴다. 그것이 높다 · 낮다고
  말하지 않는다.
- 진단하지 않는다. 행동을 문제로 규정하지 않는다.
- 한 번의 관찰을 성향으로 쓰지 않는다. "늘" "항상" "좋아하는" 은 확정된 관심(tier 1)에만 쓴다.
- 음식 · 식재료를 쓰는 활동을 내지 않는다. 그건 놀이 추천의 몫이다.
- 약 · 처치 방법을 말하지 않는다.
- 일정을 직접 등록하지 않는다.
""".strip()

_LEARNING = """
[교육 활동]
- 후보 3개를 낸다. 모자라게 내지 않는다.
- 단계(steps)는 1~4줄. 쓰는 물건(materials)을 빠짐없이 적는다 — 거르는 건 코드가 한다.
- 관심사가 있으면 활동에 잇는다. 예시는 결을 보여주는 것이다. 그대로 베끼지 않는다.
- 요청이나 [보호자가 같이 한 말]에 주제(국어 · 숫자 같은 낱말)가 있으면 search_education_memory 의
  keywords 에 그 낱말을 넣는다.
""".strip()

# 코드가 요청 주제를 읽었을 때만 싣는다. 주제를 못 읽은 요청("열매 관찰")에 실으면 모델이 스스로
# 주제가 있다고 보고 [예시] id 를 더 옮겨 적다 한 글자씩 틀려 제출이 되풀이됐다
_LEARNING_TOPIC = """
- 요청에 주제가 있으면 그 주제의 기록만 개인화 근거다. 관심 기록(공룡 등)은 주제 기록과 같이 달 때만
  남는다. 주제의 기록이 없으면 [예시] id 를 근거로 단다.
- 요청 주제의 기록이 어려워한 것(polarity −1)이면 도울 대상이다. reason 에 그 기록의 subject 와
  어떻게 돕는지 쓴다. 주제 밖에서 싫어한 기록을 근거로 달면 무엇을 피했는지 reason 에 쓴다.
""".strip()

# 요청 기반 run(`state.requested`)의 주제 줄. [예시]가 없으니 [예시] id 를 달라는 말을 뺀다
_LEARNING_TOPIC_REQUESTED = """
- 요청에 주제가 있으면 그 주제의 기록만 개인화 근거다. 관심 기록(공룡 등)은 주제 기록과 같이 달 때만
  남는다.
- 요청 주제의 기록이 어려워한 것(polarity −1)이면 도울 대상이다. reason 에 그 기록의 subject 와
  어떻게 돕는지 쓴다. 주제 밖에서 싫어한 기록을 근거로 달면 무엇을 피했는지 reason 에 쓴다.
""".strip()

# 요청 기반 run 에만 싣는다. 코드가 요청 주제를 읽었는데 그 주제의 [예시] 행이 없다
_LEARNING_REQUESTED = """
- 이번 요청의 주제는 [예시]가 없다. 요청에 맞춰 후보를 낸다. 요청 주제의 아이 기록을 조회해서
  있으면 evidence 에 달고, 없으면 evidence 를 비운다. 지어낸 id 를 달지 않는다.
- 시간 · 횟수 · 나이 숫자는 보호자가 말한 것만 쓴다. 하루 몇 시간까지 같은 기준을 만들지 않는다.
""".strip()

# 되묻기에 답을 받고 이어서 도는 교육 run(`state.answered_subject`)에만 싣는다. 방금 저장한 기록을
# 조회까지 하고도 [예시] id 만 달아 일반 추천으로 끝나는 경우가 있었다(라이브 9회 중 4회)
_LEARNING_ANSWERED = """
- 보호자가 주제 '{subject}' 에 대해 집에서 해 본 일을 방금 답해서 아이 기록 한 건으로 저장했다.
  search_education_memory 로 조회하면 observed_on 이 오늘인 교육 기록이 그 답이다. 기록 이름이
  '{subject}' 와 달라도("받아쓰기") 그 답의 기록이니, 후보마다 evidence 에 달고 그 기록의 내용을
  reason 에 반영한다.
""".strip()

# 화면 · 게임 고민(`state.topic` 에 screen)일 때만 싣는다
_LEARNING_SCREEN = """
- 화면 · 게임 이야기면 search_activity_memory 와 search_routine_memory 로 그 기록을 찾는다. 화면을
  더 보게 하는 활동이 아니라 화면 보는 약속이나 끝낸 뒤 할 놀이를 낸다.
- 중독 같은 말로 행동을 부르지 않는다.
""".strip()

_ROUTINE = """
[생활 루틴]
- 먼저 search_routine_memory 를 부른다. 다음 단계(next_steps)는 코드가 골랐다 — 단계를 건너뛰거나
  새로 만들지 않는다. next_steps 의 단계마다 하나씩, 그 단계를 이 집 상황에 맞춘 문장으로 옮긴다.
- 예절 · 습관이면 next_steps 는 코드가 고른 연습 · 대응 방법이다. 같은 방식으로 하나씩 옮긴다.
- next_steps 가 없거나 기록이 없으면 items 를 비우고 category · subject 만 낸다.
  코드가 알맞은 안내나 질문을 낸다.
- subject 는 보호자가 물은 행동 이름이다. 요청에 행동이 없으면 [보호자가 같이 한 말]에서 찾는다.
  next_steps 에 그 행동이 없으면 items 를 비운다.
""".strip()

_BOOK = """
[도서]
- 이번에는 아이와 함께 읽을 책을 고른다.
- 먼저 search_book_interests 를 한 번 부른다. 코드가 교육 · 관심 · 놀이 · 생활 기록을 점수 순
  주제로 줄 세워 돌려준다. 점수는 코드가 정했고 높을수록 아이에게 가깝다.
- 요청에 주제 낱말(공룡 · 곰 · 잠자리 같은 이름)이 있으면 그 낱말을 모두 첫 검색의 keywords 에
  넣는다. 책 종류는 주제가 아니다(그림책 · 동화 · 보드북 · 책). 넣지 않는다. 제목에 그 낱말이
  든 책만 낼 수 있어서 기록 주제는 섞지 않는다.
- 요청에 주제가 없으면 기록 주제를 낱말 맞추듯 고르지 않고 함께 본다. 점수가 높은 주제 한두
  개로 찾고, 여러 기록에 걸쳐 나온 주제(signals 가 여럿)를 먼저 믿는다. 생활 기록(예: 양치)은 그
  행동을 다룬 이야기책으로 이을 때만 쓰고, 힘들어하는 행동(polarity −1)이면 reason 에 그 행동
  이름을 쓴다.
- 세 권을 같은 시리즈로 채우지 않는다. 주제가 둘 이상이면 섞어서 고른다.
- 기록이 없으면 요청에 나온 주제 낱말로 찾는다.
- keywords 에는 주제 낱말만 쓴다(예: 공룡, 자동차). 아이 이름 · 문장 · 나이를 넣지 않는다.
- 책은 search_books 결과의 isbn 만 고른다. 결과에는 이 나이에 맞는 형태의 책만 온다.
- 요청에 주제 낱말이 있으면 보호자가 말하지 않은 주제로 넘어가지 않는다. 한 권도 못 찾았으면
  다른 주제로 바꾸지 말고 items 를 비운다. 낱말이 둘 이상이면 첫 검색에 다 넣었으니 그 결과만
  본다.
- 요청에 주제가 없을 때만 결과가 3권보다 적으면 다음 점수 순 주제로 한 번 더 찾는다.
  한 권도 못 찾았으면 두 번 더 찾아 본 뒤에만 items 를 비운다. 모자라면 있는 만큼 낸다.
- 책 내용을 지어내지 않는다. reason 은 조회한 기록과 제목 · 형태에서 알 수 있는 것으로만 쓴다.
- evidence 에는 search_book_interests 결과에 있던 id 만 쓴다. 근거로 쓴 기록이 없으면 비운다.
  책 자체는 코드가 근거로 단다.
- 요청에 주제 낱말이 있으면 그 낱말의 기록만 개인화 근거다. 다른 주제의 관심(공룡 등)만 달면 일반
  추천이 된다.
""".strip()

_LABEL_SECTIONS: dict[GrowthTaskType, str] = {
    GrowthTaskType.LEARNING_SUGGESTION: _LEARNING,
    GrowthTaskType.ROUTINE_COACHING: _ROUTINE,
    GrowthTaskType.BOOK_SUGGESTION: _BOOK,
}

# 요청 기반 run 에는 [예시]가 없어 이 줄을 뺀다 (`_EVIDENCE` 의 `{example_line}` 자리)
_EXAMPLE_LINE = "- 기억 근거가 없는 교육 활동은 가장 가까운 [예시] id 를 단다.\n"

_EVIDENCE = """
[근거]
- evidence 에는 조회 결과나 [예시]에 있던 id 만 쓴다. 지어내지 않는다. 근거마다 note 를 짧게 쓴다 —
  원문을 옮기지 않는다.
- 근거 id 는 한 글자도 바꾸지 않고 그대로 복사한다. 옮겨 적다 한 글자라도 틀리면 그 후보는 거절된다.
{example_line}- 생활 루틴은 그 루틴 기록을 근거로 단다.
- 싫어한 기록을 근거로 달면 무엇을 피했는지 reason 에 쓴다. 그 기록의 subject(행동 이름)를
  reason 에 그대로 넣는다.
""".strip()

_OUTPUT = """
[출력]
- 마지막에 출력 tool 을 한 번 부른다. 말로 후보를 설명하지 않는다.
- 후보가 거절되면 받은 사유대로 고쳐서 다시 낸다.
""".strip()


def build_system_prompt(
    context: GrowthContext, task_type: GrowthTaskType, examples: Sequence[DocHit]
) -> str:
    """system 메시지 본문. examples 는 `search_growth_doc` 결과(교육만, 루틴은 비어 있다)다.

    도서는 [예시]를 싣지 않는다 — 책은 검색 결과에서만 고르고, 문서 행 예시가 없다.
    """
    gate = context.state.gate
    if gate is None:
        raise RuntimeError("Gate 없이 프롬프트를 만들었다 — run() 이 build_gate 를 먼저 부른다")
    label = _LABEL_SECTIONS[task_type]
    requested = task_type is GrowthTaskType.LEARNING_SUGGESTION and context.state.requested
    if task_type is GrowthTaskType.LEARNING_SUGGESTION:
        topic = context.state.topic
        if topic:
            label = f"{label}\n{_LEARNING_TOPIC_REQUESTED if requested else _LEARNING_TOPIC}"
        if answered := context.state.answered_subject:
            label = f"{label}\n{_LEARNING_ANSWERED.format(subject=answered)}"
        if requested:
            label = f"{label}\n{_LEARNING_REQUESTED}"
        if SCREEN in topic:
            label = f"{label}\n{_LEARNING_SCREEN}"
    evidence = _EVIDENCE.format(example_line="" if requested else _EXAMPLE_LINE)
    dynamic = [f"[월령]\n아이는 {gate.stage.months}개월이다.", _now(context)]
    if task_type is not GrowthTaskType.BOOK_SUGGESTION:
        dynamic.insert(0, _examples(examples))
    if task_type is GrowthTaskType.LEARNING_SUGGESTION:
        if frame := age_frame(gate.stage.months):
            dynamic.insert(1, frame)
        if context.state.topic_records:
            records = tuple(context.state.topic_records.values())
            required = covers_topic(records, context.state.topic)
            dynamic.insert(0, topic_record_block(records, required=required))
    return "\n\n".join([_ROLE, _DONT, label, evidence, _OUTPUT, *dynamic])


def request_message(request_texts: Sequence[str], record_texts: Sequence[str] = ()) -> str:
    """user 메시지. 같은 입력의 기록 조각은 요청 다음에 따로 싣는다 — 요청과 섞이지 않게.

    "아이가 국어 공부를 어려워하는데 어떻게 도와줘야 좋을까?" 는 요청 조각만으로는 무엇을
    묻는지 모른다. 기록 조각이 없으면 요청만 간다.
    """
    texts = [text for text in request_texts if text.strip()]
    message = "\n".join(texts) if texts else "아이와 해 볼 것을 추천해 줘."
    records = [text for text in record_texts if text.strip()]
    if not records:
        return message
    return f"{message}\n\n{RECORD_HEADER}\n" + "\n".join(records)


def recall_message(
    request_texts: Sequence[str], *, chosen: Sequence[str], excluded: Sequence[str]
) -> str:
    """안전 필터 · 금지 표현 뒤 재호출. 빠진 이유는 말하지 않는다 (Tool_공통 §5-2)."""
    avoid = "\n".join(f"- {item}" for item in (*chosen, *excluded))
    return (
        f"{request_message(request_texts)}\n\n"
        "아래 후보는 다시 내지 않는다. 다른 후보로 다시 낸다.\n"
        f"{avoid}"
    )


_TOPIC_RECORD_REQUIRED = (
    "- 후보마다 위 id 중 하나 이상을 evidence 에 단다. reason 에 그 기록의 subject 를 그대로 넣어 "
    "무엇을 이어 가는지 쓴다."
)
# 요청 영역 중 기록이 없는 영역이 있다 — 그 영역 후보는 위 기록을 달 수 없다(`memory.covers_topic`)
_TOPIC_RECORD_SOME = (
    "- 위 기록의 주제로 낸 후보는 그 id 를 evidence 에 달고, reason 에 그 기록의 subject 를 그대로 "
    "넣는다."
)
_TOPIC_RECORD_RULES = (
    "- 기록이 아이가 지금 하는 것(무엇을 · 어디까지)을 말하면 활동 내용의 난이도는 [월령]이 아니라 "
    "그 기록에 맞춘다.",
    "  잘한 것(polarity +1)이면 한 걸음 더 나아간 것, 어려워한 것(-1)이면 그 바로 앞 단계부터 "
    "돕는 것이다.",
    "- 진행 방식 · 시간 · 준비물은 [월령]과 [활동 짜임]에 맞춘다. [예시]는 집에서 하는 방식만 "
    "참고하고 난이도는 따르지 않는다.",
    "- 책 · 문제집 · 앱 이름을 대지 않는다. 집에 있는 물건으로 하는 활동이다.",
)

# 문서 행이 3~5세 공통이라(2019 개정 누리과정) 만 나이별 짜임을 코드가 더한다. 짜임만 준다 —
# 누가 이끄나 · 단계 수 · 무엇을 바꾸나. 숫자 기준(몇까지 세기 등)은 발달 기준이라 싣지 않는다
# 누리과정(만 3~5세)이 끝나는 월령. 이 월령부터 [활동 짜임]을 싣지 않는다
NURI_END_MONTH = 72
_FRAME_TAIL = " [예시]는 만 3~5세 공통 자료다. 이 짜임에 맞게 바꿔 낸다."
_AGE_FRAMES = (
    (
        60,
        "[활동 짜임] 아이는 만 5세다. 아이가 규칙 · 순서 · 기준을 정하고 말로 설명한다. "
        "단계는 3~4줄. 그림이나 표시로 남겨 다시 본다. "
        "두 가지를 견주거나 기준을 바꿔 다시 해 본다.",
    ),
    (
        48,
        "[활동 짜임] 아이는 만 4세다. 어른과 아이가 번갈아 한다. 단계는 3줄. "
        "아이가 규칙 하나를 이어 가거나 바꿔 본다. 왜 그렇게 했는지 한 번 묻는다.",
    ),
    (
        36,
        "[활동 짜임] 아이는 만 3세다. 어른이 먼저 한 번 해 보이고 아이가 따라 한다. "
        "단계는 2~3줄. 한 번에 한 가지만 바꾼다. 손으로 만지고 옮기는 물건을 쓴다.",
    ),
)


def topic_record_block(records: Sequence[RankedEvidence], *, required: bool = True) -> str:
    """요청 주제의 아이 기록 구획. 코드가 모델 전에 읽은 기록이다(`GrowthRunState.topic_records`).

    id · subject · 이름 · tier · polarity · 날짜만 싣는다 — 발화 원문 · 아이 이름은 없다. subject 는
    출력 검증이 reason 에서 찾는 값이라 보여 준다. tier 는 "늘 · 좋아하는" 을 써도 되는지(tier 1)의
    기준이다. polarity 는 아래 규칙과 같은 모양(+1 · -1)으로 쓴다.

    `required` 가 False 면 요청 영역 중 기록이 없는 영역이 있다 — 후보마다 달라고 하지 않는다.
    """
    lines = ["[요청 주제의 아이 기록]"]
    for record in records:
        polarity = f"{record.polarity:+d}" if record.polarity else "0"
        lines.append(
            f"- id {record.ref.id} · subject {record.subject} · {record.label} "
            f"(tier {record.tier}, polarity {polarity}, {record.observed_on.isoformat()})"
        )
    lines.append(_TOPIC_RECORD_REQUIRED if required else _TOPIC_RECORD_SOME)
    lines.extend(_TOPIC_RECORD_RULES)
    return "\n".join(lines)


def age_frame(months: int) -> str | None:
    """교육의 [활동 짜임]. 35개월 이하는 없다 — 문서 행 구간이 이미 나뉘어 있다. 72개월부터도 없다 —
    누리과정 밖이고 만 5세 짜임을 주면 나이를 틀리게 말한다."""
    if months >= NURI_END_MONTH:
        return None
    for floor, text in _AGE_FRAMES:
        if months >= floor:
            return text + _FRAME_TAIL
    return None


def _examples(examples: Sequence[DocHit]) -> str:
    if not examples:
        return "[예시]\n이 월령의 예시는 아직 없다."
    lines = ["[예시] 결만 참고한다. 바탕으로 했으면 id 를 evidence 에 단다."]
    for hit in examples:
        row = hit.row
        materials = ", ".join(row.materials) or "없음"
        lines.append(f"- id {row.id} · {row.title}: {row.body} (준비물 {materials})")
    return "\n".join(lines)


def _now(context: GrowthContext) -> str:
    local = context.now.astimezone(context.timezone)
    return f"현재 시각은 {local.isoformat()} · timezone {context.timezone}."


def _fingerprint_parts() -> tuple[str, ...]:
    """프롬프트 지문(#770)의 입력 — 라벨 · 상태 갈래마다 렌더링한 system 프롬프트와 tool spec."""
    from app.agents.growth.schemas import tool_defs

    gate = fixed_gate()
    # 교육 활동은 주제 · 요청 · 답한 주제 · 화면 시간 갈래에 따라 글이 덧붙는다
    learning_states: tuple[dict[str, object], ...] = (
        {},
        {"topic": ("x",)},
        {"topic": ("x",), "requested": True},
        {"topic": ("x",), "answered_subject": "예시"},
        {"topic": ("x", SCREEN)},
        {"topic": ("x", SCREEN), "requested": True, "answered_subject": "예시"},
    )
    rendered = []
    for task in GrowthTaskType:
        states = learning_states if task is GrowthTaskType.LEARNING_SUGGESTION else ({},)
        for extra in states:
            context = fixed_context(
                gate=gate,
                topic=extra.get("topic", ()),
                requested=extra.get("requested", False),
                answered_subject=extra.get("answered_subject"),
                topic_records={},
            )
            try:
                rendered.append(build_system_prompt(context, task, ()))  # type: ignore[arg-type]
            except KeyError:  # 코드가 답하는 라벨(growth_review)은 프롬프트가 없다
                break
    from app.agents.common import tool_loop

    # user 메시지 틀 — 같이 한 말 머리말 · 재호출 지시도 모델이 읽는 규칙이다
    messages = (
        request_message([]),
        request_message(["요청"], ["같이 한 말"]),
        recall_message(["요청"], chosen=["고른 것"], excluded=["빠진 것"]),
    )
    # 예시가 있으면 [예시] 머리말 지시("결만 참고한다 …")가 붙는다
    # — 고정 더미 행으로 그 갈래도 싣는다
    example = SimpleNamespace(
        row=SimpleNamespace(id="id", title="제목", body="본문", materials=("준비물",))
    )
    with_example = _examples((example,))  # type: ignore[arg-type]
    # 요청 주제의 아이 기록 구획 — 후보마다 달게 하는 갈래와 일부 영역만 기록이 있는 갈래 (#399)
    record = RankedEvidence(
        ref=Ref(kind="observation_education", id=UUID(int=0)),
        tier=1,
        label="이름",
        polarity=1,
        observed_on=FIXED_NOW.date(),
        subject="subject",
    )
    topic_blocks = tuple(topic_record_block((record,), required=flag) for flag in (True, False))
    # [활동 짜임] 은 월령마다 글이 다르다 — 고정 월령 하나로는 한 갈래만 나온다
    frames = tuple(frame for floor, _ in _AGE_FRAMES if (frame := age_frame(floor)))
    return (
        *rendered,
        with_example,
        *topic_blocks,
        *frames,
        *messages,
        tools_text(tool_defs.TOOL_DEFINITIONS),
        tool_loop.BAD_JSON,
    )


register_prompt("growth", _fingerprint_parts)
