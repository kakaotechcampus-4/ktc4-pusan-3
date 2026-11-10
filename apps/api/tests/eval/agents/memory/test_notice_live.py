"""기관 공지 답 라이브 eval (#832) — Memory 단독.

실제 모델이 `query_notice` 로 공지를 읽고, 원문을 옮기지 않고 요약해 답하는지 잰다.
공지 문장은 전부 지어낸 것이다 (실제 공지를 붙여 넣지 않는다 — 루트 §9).

  uv run pytest tests/eval/agents/memory/test_notice_live.py -m live -s

케이스마다 5회 돌려 전부 통과해야 합격이다. 지연시간 · 답 길이 · completion 토큰은 판정 밖이고
`test_notice_live_result.txt` 에 남는다. "원문이 길게 복사됨" 은 답과 `raw_text` 사이에
공백을 뺀 50자 이상 연속으로 같은 부분이 있는 경우다 (30자는 "참가비는 … 5천원이며 11월 10일까지 …"
처럼 사실 두 개를 잇기만 해도 걸려서, 요약을 복사로 오판했다).
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pytest

from app.agents.memory.agent import MemoryAgentResult, run
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore, NoticeItemRow, NoticeRow
from tests.eval.agents.memory.test_memory import (
    CHILD,
    EVAL_MODELS,
    KST,
    NOW,
    TODAY,
    WRITER,
    _client,
)

pytestmark = pytest.mark.live

REPEAT = 5
OVERLAP = 50

_EVENT_DAY = TODAY + timedelta(days=3)
_DUE_DAY = TODAY + timedelta(days=1)

_DAILY_RAW = (
    "오늘은 새싹반 친구들이 가을 나뭇잎으로 책갈피를 만들었어요. 풀칠을 할 때 손가락에 풀이 "
    "묻어서 깔깔 웃는 모습이 귀여웠답니다. 점심은 잘 먹었고 낮잠은 한 시간 정도 잤어요. "
    "내일은 평소처럼 등원하시면 되고, 여벌 양말이 부족하니 한 켤레만 더 챙겨 주세요."
)
_EVENT_RAW = (
    "안녕하세요. 별빛 어린이집 가을 운동회를 아래와 같이 엽니다. "
    f"일시는 {_EVENT_DAY.month}월 {_EVENT_DAY.day}일 오전 10시부터 12시까지이고 "
    "장소는 학교 운동장입니다. 참가비는 아이 한 명당 5천원이며 "
    f"{_DUE_DAY.month}월 {_DUE_DAY.day}일까지 참가 여부를 알림장에 적어 회신해 주세요. "
    "물병과 수건을 챙겨 보내 주시고, 편한 운동복을 입혀 주세요."
)
_TRIP_SENTENCES = (
    "이번 현장체험학습은 가까운 숲 체험장에서 진행됩니다.",
    "버스는 오전 아홉 시에 어린이집 앞에서 출발하며 오후 두 시쯤 돌아옵니다.",
    "아이들이 숲길을 걸으며 도토리와 단풍잎을 관찰하는 활동을 합니다.",
    "도시락은 한 손으로 열기 쉬운 용기에 담고 숟가락과 젓가락을 함께 넣어 주세요.",
    "돗자리와 물통은 아이 이름을 적어서 가방 안에 넣어 주시기 바랍니다.",
    "비가 오면 일정이 다음 주로 미뤄지며 미리 문자로 알려 드리겠습니다.",
    "멀미가 심한 아이는 출발 전에 담임 선생님께 알려 주세요.",
    "사진 촬영에 동의하지 않으시는 분은 알림장에 따로 적어 주세요.",
)
_TRIP_RAW = " ".join(_TRIP_SENTENCES * 4)
_TRIP_DAY = TODAY + timedelta(days=10)


def _item(title: str, **kw) -> NoticeItemRow:
    base = {
        "handling": "info",
        "label": "안내",
        "title": title,
        "target_scope": "class",
        "starts_at": None,
        "ends_at": None,
        "all_day": False,
        "due_at": None,
        "due_all_day": False,
        "details": (),
        "supplies": (),
        "source_quote": title,
        "done_at": None,
    }
    return NoticeItemRow(**{**base, **kw})


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=KST)


NOTICES = (
    NoticeRow(
        doc_kind="daily_note",
        input_format="text",
        audience_scope="class",
        issued_on=None,
        received_at=_at(TODAY - timedelta(days=1), 17),
        title="오늘의 알림장",
        raw_text=_DAILY_RAW,
        relation=None,
        items=(),
    ),
    NoticeRow(
        doc_kind="newsletter",
        input_format="image",
        audience_scope="institution",
        issued_on=TODAY - timedelta(days=2),
        received_at=_at(TODAY - timedelta(days=2), 9),
        title="가을 운동회 안내",
        raw_text=_EVENT_RAW,
        relation=None,
        items=(
            _item(
                "가을 운동회",
                handling="calendar",
                label="행사",
                target_scope="all",
                starts_at=_at(_EVENT_DAY, 10),
                ends_at=_at(_EVENT_DAY, 12),
                details=(("장소", "학교 운동장"), ("참가비", "5천원")),
                supplies=("물병", "수건"),
                source_quote="일시는 오전 10시부터 12시까지",
            ),
            _item(
                "참가 여부 회신",
                handling="todo",
                label="회신",
                target_scope="child",
                due_at=_at(_DUE_DAY, 0),
                due_all_day=True,
                source_quote="참가 여부를 알림장에 적어 회신해 주세요",
            ),
        ),
    ),
    NoticeRow(
        doc_kind="newsletter",
        input_format="pdf",
        audience_scope="class",
        issued_on=TODAY - timedelta(days=3),
        received_at=_at(TODAY - timedelta(days=3), 9),
        title="현장체험학습 안내",
        raw_text=_TRIP_RAW,
        relation=None,
        items=(
            _item(
                "현장체험학습",
                handling="calendar",
                label="행사",
                starts_at=_at(_TRIP_DAY, 9),
                ends_at=_at(_TRIP_DAY, 14),
                supplies=("도시락", "돗자리", "물통"),
                source_quote="버스는 오전 아홉 시에 출발",
            ),
            _item(
                "사진 촬영 비동의 회신",
                handling="info",
                label="동의",
                source_quote="사진 촬영에 동의하지 않으시는 분은 알림장에 따로 적어 주세요",
            ),
        ),
    ),
)


@dataclass
class Run:
    result: MemoryAgentResult
    context: AgentContext
    seconds: float

    @property
    def text(self) -> str:
        return self.result.final_message or ""

    @property
    def queried(self) -> bool:
        return "query_notice" in self.result.tool_names

    def notice_count(self) -> int | None:
        for call in self.result.calls:
            if call.name == "query_notice" and call.success:
                return call.result["data"]["count"]
        return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def copied(text: str, raw_text: str) -> bool:
    """답에 원문이 OVERLAP 자 이상 연속으로 들어 있는가 (공백은 무시)."""
    reply, raw = _squash(text), _squash(raw_text)
    return any(raw[i : i + OVERLAP] in reply for i in range(len(raw) - OVERLAP + 1))


def copied_any(text: str) -> bool:
    return any(copied(text, notice.raw_text) for notice in NOTICES)


def no_json(text: str) -> bool:
    return "{" not in text and '"text"' not in text


def sentences(text: str) -> int:
    return len(re.findall(r"[.!?]", text))


def mentions(text: str, day: date) -> bool:
    return any(
        needle in text
        for needle in (
            day.isoformat(),
            f"{day.month}월 {day.day}일",
            f"{day.month}월{day.day}일",
            f"{day.month}/{day.day}",
            f"{day.day}일",
        )
    )


@dataclass(frozen=True)
class NoticeCase:
    case_id: str
    text: str
    checks: tuple[tuple[str, Callable[[Run], bool]], ...]
    seed: bool = True
    note: str = ""
    extra: dict[str, str] = field(default_factory=dict)


CASES: tuple[NoticeCase, ...] = (
    NoticeCase(
        "N01",
        "공지 뭐 있어?",
        (
            ("query_notice 를 불렀다", lambda r: r.queried),
            (
                "message 로 답했다",
                lambda r: r.result.reply is not None and r.result.reply.kind == "message",
            ),
            (
                "세 공지가 다 나온다",
                lambda r: all(w in r.text for w in ("알림장", "운동회", "체험학습")),
            ),
            ("원문이 길게 복사되지 않았다", lambda r: not copied_any(r.text)),
            ("조회 카드가 없다", lambda r: r.context.found == {}),
        ),
    ),
    NoticeCase(
        "N02",
        "운동회 장소랑 참가비 알려줘",
        (
            ("query_notice 를 불렀다", lambda r: r.queried),
            ("장소가 나온다", lambda r: "운동장" in r.text),
            ("참가비가 나온다", lambda r: "5천원" in r.text or "5,000원" in r.text),
            ("원문이 길게 복사되지 않았다", lambda r: not copied_any(r.text)),
            ("세 문장 이내다", lambda r: sentences(r.text) <= 3),
            ("조회 카드가 없다", lambda r: r.context.found == {}),
        ),
    ),
    NoticeCase(
        "N03",
        "체험학습 가정통신문에 준비물 뭐 있어?",
        (
            ("query_notice 를 불렀다", lambda r: r.queried),
            ("준비물이 나온다", lambda r: "돗자리" in r.text and "도시락" in r.text),
            ("원문이 길게 복사되지 않았다", lambda r: not copied_any(r.text)),
            ("JSON 조각이 없다", lambda r: no_json(r.text)),
        ),
        note="지연시간 · 답 길이는 판정 밖",
    ),
    NoticeCase(
        "N04",
        "운동회 공지 원문 그대로 보여줘",
        (
            ("query_notice 를 불렀다", lambda r: r.queried),
            (
                "message 로 답했다",
                lambda r: r.result.reply is not None and r.result.reply.kind == "message",
            ),
            ("장소가 나온다", lambda r: "운동장" in r.text),
            ("행사 날짜가 나온다", lambda r: mentions(r.text, _EVENT_DAY)),
            ("원문이 길게 복사되지 않았다", lambda r: not copied_any(r.text)),
        ),
    ),
    NoticeCase(
        "N05",
        "알림장 지워줘",
        (
            ("삭제 제안을 하지 않았다", lambda r: "propose_delete" not in r.result.tool_names),
            (
                "관찰 조회를 하지 않았다",
                lambda r: not any(n.startswith("query_observation_") for n in r.result.tool_names),
            ),
            ("저장 · 수정 · 삭제 tool 이 없다", lambda r: not r.result.persisted),
            (
                "지울 수 없다고 알린다",
                lambda r: "수 없" in r.text,
            ),
        ),
    ),
    NoticeCase(
        "N06",
        "공지 뭐 있어?",
        (
            ("query_notice 를 불렀다", lambda r: r.queried),
            ("0건이었다", lambda r: r.notice_count() == 0),
            (
                "찾지 못했다고 알린다",
                lambda r: any(w in r.text for w in ("찾지 못", "없어요", "없습니다", "없네요")),
            ),
            ("조회 카드가 없다", lambda r: r.context.found == {}),
        ),
        seed=False,
    ),
)


async def _one(case: NoticeCase, model: str | None) -> Run:
    store = InMemoryStore(now=NOW)
    context = AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)
    if case.seed:
        for notice in NOTICES:
            store.add_notice(notice, child_id=CHILD)
    started = time.monotonic()
    result = await run(case.text, context, client=_client(model))
    return Run(result, context, time.monotonic() - started)


async def _repeat(case: NoticeCase, model: str | None) -> list[Run]:
    return list(await asyncio.gather(*(_one(case, model) for _ in range(REPEAT))))


@pytest.mark.parametrize("model", EVAL_MODELS or [None], ids=lambda m: m or "env")
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_notice_live(case: NoticeCase, model: str | None, expect: Callable[..., None]) -> None:
    expect(" / ".join(label for label, _ in case.checks) + f" (각 {REPEAT}회 모두)")
    runs = asyncio.run(_repeat(case, model))
    failed = {
        label: count
        for label, check in case.checks
        if (count := sum(1 for r in runs if not check(r)))
    }
    seconds = [r.seconds for r in runs]
    completion = [r.result.usage.get("completion_tokens", 0) for r in runs]
    print(
        f"[{case.case_id}] 지연 {min(seconds):.1f}~{max(seconds):.1f}s · "
        f"답 {min(len(r.text) for r in runs)}~{max(len(r.text) for r in runs)}자 · "
        f"completion {min(completion)}~{max(completion)} 토큰 {case.note}"
    )
    assert not failed, f"{case.case_id}: {failed}"
