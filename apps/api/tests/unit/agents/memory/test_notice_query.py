"""query_notice — 기관 공지 조회 tool (#832).

공지는 읽기만 한다. 식별자는 포트에서부터 없고, 라벨 · KST 날짜 문자열은 tool 이 만든다.
조회 카드(`context.found`)는 만들지 않아 답이 일반 응답 문장으로 나간다.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.memory.bundles import LOOKUP_EDIT, RECORD_BASE
from app.agents.memory.context import AgentContext
from app.agents.memory.registry import execute_tool, registered_names
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore, NoticeItemRow, NoticeRow
from app.agents.memory.tools import notice as notice_tool
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItemHandling,
    NoticeItemTarget,
    NoticeRelation,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=KST)
CHILD = UUID(int=1)


def _context() -> AgentContext:
    store = InMemoryStore(now=NOW)
    return AgentContext(
        child_id=CHILD, source_writer=UUID(int=2), now=NOW, timezone=KST, store=store
    )


def _item(title: str, **kw: Any) -> NoticeItemRow:
    base: dict[str, Any] = {
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


def _row(raw_text: str, **kw: Any) -> NoticeRow:
    base: dict[str, Any] = {
        "doc_kind": "newsletter",
        "input_format": "text",
        "audience_scope": "class",
        "issued_on": None,
        "received_at": datetime(2026, 11, 2, 9, 0, tzinfo=KST),
        "title": None,
        "raw_text": raw_text,
        "relation": None,
        "items": (),
    }
    return NoticeRow(**{**base, **kw})


def _seed(context: AgentContext, row: NoticeRow) -> None:
    assert isinstance(context.store, InMemoryStore)
    context.store.add_notice(row, child_id=context.child_id)


async def _query(context: AgentContext, **args: Any):
    return await execute_tool("query_notice", args, context, allowed=None)


def _walk(value: Any):
    """결과 안의 모든 (키, 값). 키가 없는 자리(리스트 원소)는 키를 None 으로 낸다."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key, inner
            yield from _walk(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield None, inner
            yield from _walk(inner)


async def test_공지와_항목의_모든_칸을_라벨로_돌려준다() -> None:
    context = _context()
    _seed(
        context,
        _row(
            "가을 운동회 안내 원문입니다.",
            doc_kind="newsletter",
            input_format="hwpx",
            audience_scope="class",
            issued_on=date(2026, 11, 1),
            received_at=datetime(2026, 11, 2, 9, 30, tzinfo=KST),
            title="가을 운동회",
            relation="correction",
            items=(
                _item(
                    "운동회",
                    handling="calendar",
                    label="행사",
                    target_scope="all",
                    starts_at=datetime(2026, 11, 12, 10, 0, tzinfo=KST),
                    ends_at=datetime(2026, 11, 12, 12, 0, tzinfo=KST),
                    details=(("장소", "운동장"), ("참가비", "5천원")),
                    supplies=("물병", "수건"),
                    source_quote="11월 12일 오전 10시",
                ),
                _item(
                    "참가 회신",
                    handling="todo",
                    label="회신",
                    target_scope="child",
                    due_at=datetime(2026, 11, 7, 0, 0, tzinfo=KST),
                    due_all_day=True,
                    source_quote="11월 7일까지 회신",
                    done_at=datetime(2026, 11, 3, 8, 30, tzinfo=KST),
                ),
            ),
        ),
    )

    result = await _query(context)

    assert result.success is True, result.error
    assert result.data == {
        "count": 1,
        "notices": [
            {
                "doc_kind": "가정통신문",
                "input_format": "한글 파일",
                "audience_scope": "반 전체",
                "relation": "정정 공지",
                "title": "가을 운동회",
                "raw_text": "가을 운동회 안내 원문입니다.",
                "issued_on": "2026-11-01(일)",
                "received_at": "2026-11-02(월) 09:30",
                "items": [
                    {
                        "handling": "일정",
                        "target_scope": "전체",
                        "label": "행사",
                        "title": "운동회",
                        "source_quote": "11월 12일 오전 10시",
                        "supplies": ["물병", "수건"],
                        "details": [
                            {"name": "장소", "value": "운동장"},
                            {"name": "참가비", "value": "5천원"},
                        ],
                        "starts_at": "2026-11-12(목) 10:00",
                        "ends_at": "2026-11-12(목) 12:00",
                        "due_at": None,
                        "done_at": None,
                    },
                    {
                        "handling": "할 일",
                        "target_scope": "우리 아이",
                        "label": "회신",
                        "title": "참가 회신",
                        "source_quote": "11월 7일까지 회신",
                        "supplies": [],
                        "details": [],
                        "starts_at": None,
                        "ends_at": None,
                        "due_at": "2026-11-07(토)",
                        "done_at": "2026-11-03(화) 08:30",
                    },
                ],
            }
        ],
    }


async def test_식별자는_어디에도_없다() -> None:
    context = _context()
    _seed(
        context,
        _row(
            "알림장 원문입니다.",
            title="알림장",
            items=(_item("준비물", supplies=("수건",)),),
        ),
    )

    result = await _query(context)

    banned = {
        "id",
        "notice_id",
        "child_id",
        "uploader_parent_id",
        "related_notice_id",
        "content_hash",
        "extractor_version",
        "created_at",
        "updated_at",
    }
    pairs = list(_walk(result.data))
    assert not {key for key, _ in pairs if key} & banned
    assert not any(str(context.child_id) in str(value) for _, value in pairs)


async def test_시각은_KST_로_요일을_붙인다() -> None:
    context = _context()
    _seed(
        context,
        _row(
            "시각 확인용 공지입니다.",
            items=(
                _item(
                    "체험",
                    starts_at=datetime.fromisoformat("2026-11-12T01:00:00+00:00"),
                ),
                _item(
                    "종일 행사",
                    starts_at=datetime.fromisoformat("2026-11-12T01:00:00+00:00"),
                    all_day=True,
                ),
            ),
        ),
    )

    result = await _query(context)

    starts = sorted(i["starts_at"] for i in result.data["notices"][0]["items"])
    assert starts == ["2026-11-12(목)", "2026-11-12(목) 10:00"]


async def test_조회_카드_후보를_남기지_않는다() -> None:
    context = _context()
    _seed(context, _row("공지 원문입니다 하나."))

    await _query(context)

    assert context.found == {}


async def test_없으면_0건으로_성공한다() -> None:
    context = _context()

    result = await _query(context)

    assert result.success is True
    assert result.data["count"] == 0
    assert result.data["notices"] == []
    assert "truncated" not in result.data


async def test_5건이_넘으면_최신_5건과_잘림_안내() -> None:
    context = _context()
    for day in range(1, 7):
        _seed(
            context,
            _row(
                f"{day}일에 받은 공지 원문입니다.",
                received_at=datetime(2026, 11, day, 9, 0, tzinfo=KST),
            ),
        )

    result = await _query(context)

    assert result.data["count"] == 5
    assert result.data["truncated"] is True
    assert result.data["notice"] == notice_tool._NOTICE_CUT
    assert result.data["notices"][0]["raw_text"].startswith("6일")
    assert not any("1일에" in n["raw_text"] for n in result.data["notices"])


async def test_기준만_있고_날짜가_없으면_DATE_UNPARSEABLE() -> None:
    context = _context()

    result = await _query(context, anchor_event="소풍")

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE


async def test_날짜와_종류로_좁혀_찾는다() -> None:
    context = _context()
    _seed(
        context,
        _row(
            "알림장 원문입니다.",
            doc_kind="daily_note",
            received_at=datetime(2026, 11, 5, 9, 0, tzinfo=KST),
        ),
    )
    _seed(
        context,
        _row(
            "가정통신문 원문입니다.",
            doc_kind="newsletter",
            received_at=datetime(2026, 11, 5, 10, 0, tzinfo=KST),
        ),
    )

    result = await _query(context, doc_kind="daily_note", text_query="알림장")

    assert result.data["count"] == 1
    assert result.data["notices"][0]["doc_kind"] == "알림장"


async def test_라벨이_도메인_enum_을_모두_덮는다() -> None:
    covered = [
        (notice_tool.DOC_KIND_LABELS, NoticeDocKind),
        (notice_tool.INPUT_FORMAT_LABELS, NoticeInputFormat),
        (notice_tool.AUDIENCE_LABELS, NoticeAudience),
        (notice_tool.RELATION_LABELS, NoticeRelation),
        (notice_tool.HANDLING_LABELS, NoticeItemHandling),
        (notice_tool.TARGET_LABELS, NoticeItemTarget),
    ]
    for labels, enum in covered:
        assert set(labels) == {member.value for member in enum}


async def test_query_notice_는_조회_묶음에만_있다() -> None:
    assert "query_notice" in LOOKUP_EDIT
    assert "query_notice" not in RECORD_BASE
    assert "query_notice" in registered_names()
