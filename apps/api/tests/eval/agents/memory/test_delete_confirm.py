"""삭제 제안 라이브 eval (#535). 모델은 지우지 않고, 조회한 뒤 propose_delete 로 고르기만 한다.

    uv run pytest tests/eval/agents/memory/test_delete_confirm.py -m live -s
    DELETE_EVAL_REPEAT=10 uv run pytest tests/eval/agents/memory/test_delete_confirm.py -m live -q

보호자가 화면 카드에서 체크를 고쳐 버튼을 누르면 서버가 지운다 — 그 쪽은 BE 테스트
(tests/integration/api/test_delete_proposals.py)가 본다. 여기서는 모델이 무엇을 제안했는지를 본다.

  ① 지우는 tool 이 없다 — 어떤 기록도 사라지지 않는다
  ② 제안은 한 번, 고른 것(기본 체크)이 요청과 맞다 — "간식은 빼고" 는 빼고, 다른 날 · 다른 일은 빼고
  ③ 제안한 run 은 되묻지 않고 끝난다. "지웠어요" 라고 말하지 않는다
  ④ 같은 run 의 다른 요청(일정 수정)을 위해 조회한 종류는 삭제 카드에 붙지 않는다

첫 턴만 본다 — 제안한 run 은 되묻기 맥락을 남기지 않아 다음 채팅은 제안을 모른다(단위 테스트).
기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.datetime_rules import DateRange
from app.agents.common.llm_client import LLMClient
from app.agents.memory.agent import MemoryAgentResult, run
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import MemoryHint, MemoryTask, WorkType
from app.agents.memory.store import InMemoryStore

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
WRITER = UUID(int=2)
DOMAINS = ("food", "health", "education", "activity", "routine")

TODAY = datetime.now(KST).date()
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=KST)
YESTERDAY = TODAY - timedelta(days=1)
# 지난주 수요일 — "저번주" 의 가운데
LAST_WEEK = TODAY - timedelta(days=TODAY.weekday() + 5)

# 같은 케이스를 몇 번 돌릴지. 흔들림은 한 번으로 안 보인다 (D02 는 16번 중 3번이었다)
REPEAT = int(os.getenv("DELETE_EVAL_REPEAT", "1"))

# 지웠다고 답한 말. "삭제하지 않았어요" 가 걸리지 않게 어미까지 본다
_SAID_DELETED = ("삭제했", "삭제됐", "삭제되었", "지웠")


@dataclass
class Seeded:
    """심어 둔 행의 id. 이름 → id."""

    ids: dict[str, str] = field(default_factory=dict)


Seed = Callable[[InMemoryStore, Seeded], Awaitable[None]]


async def _observation(
    store: InMemoryStore, seeded: Seeded, name: str, domain: str, day: date, **fields: object
) -> None:
    row = await store.create_observation(
        domain=domain,
        child_id=CHILD,
        source_writer=WRITER,
        raw_text=str(fields.pop("raw_text")),
        observed_on=day,
        observed_range=DateRange(start=day, end=day + timedelta(days=1)),
        fields=dict(fields),
    )
    seeded.ids[name] = row.id


async def _event(store: InMemoryStore, seeded: Seeded, name: str, title: str, day: date) -> None:
    row = await store.create_event(
        child_id=CHILD,
        title=title,
        starts_at=datetime(day.year, day.month, day.day, 10, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={"event_type": "episodic", "category": "activity"},
    )
    seeded.ids[name] = row.id


def _next(weekday: int) -> date:
    """오늘부터 가장 가까운 그 요일. 오늘이 그 요일이면 오늘이다 — "토요일" 이 토요일엔 오늘이다."""
    return TODAY + timedelta((weekday - TODAY.weekday()) % 7)


async def _seed_apple(store: InMemoryStore, seeded: Seeded) -> None:
    await _observation(
        store,
        seeded,
        "apple",
        "food",
        YESTERDAY,
        raw_text="어제 사과를 먹었어",
        subject="사과",
        action="먹었다",
    )
    await _observation(
        store,
        seeded,
        "rice",
        "food",
        YESTERDAY,
        raw_text="어제 저녁에 밥 잘 먹었어",
        subject="밥",
        action="먹었다",
    )


async def _seed_meals_and_snack(store: InMemoryStore, seeded: Seeded) -> None:
    await _observation(
        store,
        seeded,
        "porridge",
        "food",
        YESTERDAY,
        raw_text="어제 아침에 죽 먹었어",
        subject="죽",
        action="먹었다",
    )
    await _observation(
        store,
        seeded,
        "gimbap",
        "food",
        YESTERDAY,
        raw_text="어제 점심에 김밥 먹었어",
        subject="김밥",
        action="먹었다",
    )
    await _observation(
        store,
        seeded,
        "cookie",
        "food",
        YESTERDAY,
        raw_text="어제 간식으로 과자 먹었어",
        subject="과자",
        action="간식으로 먹었다",
    )


async def _seed_last_week(store: InMemoryStore, seeded: Seeded) -> None:
    await _observation(
        store,
        seeded,
        "lw_food",
        "food",
        LAST_WEEK,
        raw_text="카레 먹었어",
        subject="카레",
        action="먹었다",
    )
    await _observation(
        store,
        seeded,
        "lw_play",
        "activity",
        LAST_WEEK,
        raw_text="블록놀이 했어",
        subject="블록",
        activity="블록놀이",
    )
    # 이번 주 기록은 "저번주" 가 아니다
    await _observation(
        store,
        seeded,
        "tw_food",
        "food",
        TODAY,
        raw_text="오늘 사과 먹었어",
        subject="사과",
        action="먹었다",
    )


async def _seed_clinic(store: InMemoryStore, seeded: Seeded) -> None:
    await _event(store, seeded, "clinic", "소아과 예약", _next(5))
    await _event(store, seeded, "swim", "수영 수업", _next(5))


async def _seed_long_juk(store: InMemoryStore, seeded: Seeded) -> None:
    # 원문이 길다 — 모델이 검색어를 줄여 쓰면 원문에 그 글자가 붙어 있지 않아 0건이 난다 (#529)
    await _observation(
        store,
        seeded,
        "juk",
        "food",
        TODAY,
        raw_text="점심으로는 전복죽 먹었어.",
        subject="전복죽",
        action="먹었다",
    )
    await _observation(
        store,
        seeded,
        "rice",
        "food",
        TODAY,
        raw_text="아침에 밥 먹었어.",
        subject="밥",
        action="먹었다",
    )


_MENUS = ("죽", "김밥", "국수", "볶음밥", "미역국", "카레")
_PLAYS = ("블록", "레고", "퍼즐", "그림", "공놀이", "모래놀이")


async def _seed_bulk_last_week(store: InMemoryStore, seeded: Seeded) -> None:
    # 종류마다 45건 — 조회가 40건에서 잘린다. "다" 지우려면 id 를 늘어놓지 않고 종류로 골라야
    # 한 응답 출력에 들어간다 (90건 × id 20토큰 남짓 > 1400토큰)
    for i in range(45):
        day = LAST_WEEK + timedelta(days=i % 5 - 2)
        menu, play = _MENUS[i % len(_MENUS)], _PLAYS[i % len(_PLAYS)]
        await _observation(
            store,
            seeded,
            f"food{i}",
            "food",
            day,
            raw_text=f"{menu} 먹었어 {i}",
            subject=menu,
            action="먹었다",
        )
        await _observation(
            store,
            seeded,
            f"play{i}",
            "activity",
            day,
            raw_text=f"{play} 했어 {i}",
            subject=play,
            activity=f"{play} 놀이",
        )


async def _seed_bulk_meals_and_snack(store: InMemoryStore, seeded: Seeded) -> None:
    for i in range(30):
        menu = _MENUS[i % len(_MENUS)]
        await _observation(
            store,
            seeded,
            f"meal{i}",
            "food",
            YESTERDAY,
            raw_text=f"{menu} 먹었어 {i}",
            subject=menu,
            action="먹었다",
        )
    await _observation(
        store,
        seeded,
        "cookie",
        "food",
        YESTERDAY,
        raw_text="간식으로 과자 먹었어",
        subject="과자",
        action="간식으로 먹었다",
    )


async def _seed_sports_and_apple(store: InMemoryStore, seeded: Seeded) -> None:
    await _event(store, seeded, "sports", "운동회", TODAY + timedelta(days=2))
    await _seed_apple(store, seeded)


@dataclass(frozen=True)
class Case:
    case_id: str
    text: str
    seed: Seed
    picked: frozenset[str]  # 기본 체크돼야 하는 것 (Seeded 이름)
    not_picked: frozenset[str] = frozenset()  # 기본 체크되면 안 되는 것
    tab_kinds: frozenset[str] | None = None  # 탭이 이 종류만이어야 한다 (None 이면 보지 않음)
    # 한 원문에 다른 조각이 섞인 입력. 없으면 원문 하나가 lookup_edit 조각이다
    hints: tuple[tuple[str, WorkType], ...] = ()
    writes: frozenset[str] = frozenset()  # 이 케이스가 허락하는 쓰기 (섞인 수정 요청)
    # 못 찾고 "못 찾았어요" 로 끝나도 통과 — 검색 방식의 한계를 재는 케이스.
    # 틀린 것은 절대 고르지 않는다
    may_miss: bool = False
    # 카드에 보인 후보(not_picked 빼고)가 전부 체크돼야 한다 — 대량 삭제
    picked_all: bool = False


CASES = [
    Case(
        "P01",
        "어제 사과 먹었다고 저장한 기록 지워줘.",
        _seed_apple,
        frozenset({"apple"}),
        frozenset({"rice"}),
    ),
    # 빼 달라고 한 것은 기본 체크에 없다 — 카드에는 체크 없이 보인다
    Case(
        "P02",
        "어제 식사 기록 중에 간식은 빼고 다 지워줘.",
        _seed_meals_and_snack,
        frozenset({"porridge", "gimbap"}),
        frozenset({"cookie"}),
    ),
    # 종류를 말하지 않았으면 여러 종류를 조회한다. 이번 주 기록은 고르지 않는다
    Case(
        "P03",
        "저번주 기록 지워줘.",
        _seed_last_week,
        frozenset({"lw_food", "lw_play"}),
        frozenset({"tw_food"}),
    ),
    Case(
        "P04",
        "토요일 소아과 예약 일정 지워줘.",
        _seed_clinic,
        frozenset({"clinic"}),
        frozenset({"swim"}),
        frozenset({"event"}),
    ),
    # 시간을 바꾸려고 조회한 일정은 삭제 카드에 붙지 않는다
    Case(
        "P05",
        "모레 운동회 시간 오전 11시로 바꾸고, 어제 사과 먹은 기록은 지워줘.",
        _seed_sports_and_apple,
        frozenset({"apple"}),
        frozenset({"rice", "sports"}),
        frozenset({"observation_food"}),
        hints=(
            ("모레 운동회 시간 오전 11시로 바꿔줘", WorkType.LOOKUP_EDIT),
            ("어제 사과 먹은 기록은 지워줘", WorkType.LOOKUP_EDIT),
        ),
        writes=frozenset({"update_event"}),  # 초안만 만든다 — 저장된 일정은 그대로다
    ),
    # 원문을 그대로 인용한 요청. 검색어가 원문과 어긋나면 못 찾는다 — 안전한 실패다
    # (검색 방식 개선은 #535 범위 밖)
    Case(
        "P06",
        '아까 "점심으로는 전복죽 먹었어." 기록 보낸 것 지워줘.',
        _seed_long_juk,
        frozenset({"juk"}),
        frozenset({"rice"}),
        may_miss=True,
    ),
    Case(
        "P07",
        "저번주 기록 다 지워줘.",
        _seed_bulk_last_week,
        frozenset(),
        tab_kinds=frozenset({"observation_food", "observation_activity"}),
        picked_all=True,
    ),
    Case(
        "P08",
        "어제 식사 기록 중에 간식은 빼고 다 지워줘.",
        _seed_bulk_meals_and_snack,
        frozenset(),
        frozenset({"cookie"}),
        frozenset({"observation_food"}),
        picked_all=True,
    ),
]


def _client() -> LLMClient:
    settings = AgentSettings()
    if not settings.MEMORY_API_KEY or not settings.MEMORY_BASE_URL:
        pytest.skip("MEMORY_API_KEY / MEMORY_BASE_URL 이 필요합니다. apps/api/.env 를 확인하세요.")
    return LLMClient(settings, role="memory")


async def _rows(store: InMemoryStore) -> int:
    observations = [
        row
        for domain in DOMAINS
        for row in await store.query_observations(domain=domain, child_id=CHILD)
    ]
    return len(observations) + len(await store.query_events(child_id=CHILD))


def _log(case: Case, result: MemoryAgentResult, store: InMemoryStore, seeded: Seeded) -> None:
    names = {oid: name for name, oid in seeded.ids.items()}
    tabs = store.proposal[1] if store.proposal else ()
    # 대량이면 이름 대신 건수만 — (종류, 고른 수, 후보 수)
    shown = [
        (tab.kind, len(tab.picked), len(tab.ids))
        if len(tab.ids) > 10
        else (tab.kind, [names.get(i, i) for i in tab.picked], len(tab.ids))
        for tab in tabs
    ]
    # 모델이 어떻게 골랐는지 — id 를 늘어놓았는지, 종류로 골랐는지
    how = [
        {key: (len(v) if key != "all_kinds" else v) for key, v in c.arguments.items()}
        for c in result.calls
        if c.name == "propose_delete"
    ]
    reply = result.reply
    print(
        f"\n[{case.case_id}] tools={result.tool_names} propose={how} tabs={shown} "
        f"kind={reply.kind if reply else None} {result.final_message!r}"
    )


async def _flow(case: Case, client: LLMClient) -> None:
    store = InMemoryStore(now=NOW)
    seeded = Seeded()
    await case.seed(store, seeded)
    before = await _rows(store)
    hints = case.hints or ((case.text, WorkType.LOOKUP_EDIT),)
    task = MemoryTask(raw_text=case.text, hints=tuple(MemoryHint(text=t, work=w) for t, w in hints))
    context = AgentContext(
        child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store, run_id="eval"
    )

    result = await run(case.text, context, client=client, task=task)
    _log(case, result, store, seeded)

    assert await _rows(store) == before, (
        "기록 · 일정 수가 바뀌었다 — 모델은 지우지도 새로 쓰지도 않는다"
    )
    # 수 말고 내용도 그대로여야 한다. 수정을 요청한 케이스만 그 수정을 허락한다
    wrote = [
        c.name
        for c in result.calls
        if c.success and c.name.startswith(("create_", "update_")) and c.name not in case.writes
    ]
    assert not wrote, f"제안 말고 다른 쓰기: {wrote}"
    proposals = [c for c in result.calls if c.success and c.name == "propose_delete"]
    if case.may_miss and not proposals:
        # 못 찾았다 — 제안 없이 "못 찾았어요" 나 날짜를 묻는 말로 끝난다. 지웠다고 말하지 않는다
        assert result.reply is not None and result.reply.text.strip(), "답이 비었다"
        assert not any(word in result.reply.text for word in _SAID_DELETED), (
            "안 지웠는데 지웠다고 했다"
        )
        print(f"  [{case.case_id}] 못 찾음 (안전한 실패)")
        return
    assert len(proposals) == 1, f"제안을 한 번 남기지 않았다: {result.tool_names}"
    assert store.proposal is not None
    tabs = store.proposal[1]
    picked = {oid for tab in tabs for oid in tab.picked}
    want = {seeded.ids[name] for name in case.picked}
    assert want <= picked, f"골라야 할 것을 안 골랐다: {sorted(case.picked)}"
    wrong = sorted(name for name in case.not_picked if seeded.ids[name] in picked)
    assert not wrong, f"고르면 안 되는 것을 골랐다: {wrong}"
    if case.picked_all:
        excluded = {seeded.ids[name] for name in case.not_picked}
        shown = {oid for tab in tabs for oid in tab.ids} - excluded
        assert shown <= picked, f"보인 후보 {len(shown)}건 중 {len(shown - picked)}건을 안 골랐다"
    if case.tab_kinds is not None:
        assert {tab.kind for tab in tabs} == case.tab_kinds, f"탭: {[t.kind for t in tabs]}"
    assert result.reply is not None and result.reply.kind == "message", "제안한 뒤에 되물었다"
    assert result.pending is None, "제안한 run 이 되묻기 맥락을 남겼다"
    said = any(word in result.reply.text for word in _SAID_DELETED)
    assert not said, "지우지 않았는데 지웠다고 했다"


@pytest.mark.parametrize("round_", range(REPEAT), ids=lambda r: f"r{r}")
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_삭제는_제안만_하고_요청에_맞게_고른다(
    case: Case, round_: int, expect: Callable[..., None]
) -> None:
    expect(
        f"{case.text!r} → 조회 후 propose_delete 한 번, 아무것도 지우지 않는다",
        f"기본 체크 ⊇ {sorted(case.picked)} · 체크 안 됨 {sorted(case.not_picked)}"
        + (f" · 탭 {sorted(case.tab_kinds)}" if case.tab_kinds else ""),
        "kind=message · 되묻기 맥락 없음 · 지웠다고 말하지 않는다",
    )
    asyncio.run(_flow(case, _client()))
