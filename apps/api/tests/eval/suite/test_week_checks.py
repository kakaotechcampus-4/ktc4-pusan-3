"""W 하네스 자체의 단위 테스트 — 모델 · DB 를 부르지 않는다."""

import asyncio
import json
import re
from datetime import date

import pytest
from pydantic import ValidationError

from tests.eval.suite.invariants import check_state
from tests.eval.suite.report import blocked_at, daily_inputs, render
from tests.eval.suite.schema import Expect, SavedExpect, Step, WeekCase, load_week_text, load_weeks
from tests.eval.suite.trace import Trace
from tests.eval.suite.verdict import judge_check, judge_step, record_week
from tests.eval.suite.week_frames import apply_frames, parse_sse
from tests.eval.suite.week_runner import (
    W_LOCK,
    W_MARK,
    ContainsJudge,
    HashEmbedder,
    affinity_cite_misses,
    corrected_misses,
    interest_misses,
    parent_nickname,
    render_say,
    response_code,
    simulation_monday,
    step_day,
    step_moment,
)

_MIN = """
id: W-X
persona: 시험
world: { nickname: 별이, parents: [mom], seeds: [] }
steps:
  - { at: "mon 08:10", by: mom, tag: a, say: 안녕 }
"""


def test_두_대본이_스키마를_통과한다() -> None:
    weeks = {w.id: w for w in load_weeks()}
    assert set(weeks) == {"W-A", "W-C"}
    a = weeks["W-A"]
    assert len([s for s in a.steps if s.kind != "check"]) == 41
    assert a.steps[-1].kind == "check"
    assert len(a.world.seeds) == 4
    assert a.world.nickname == "콩이" and a.world.parents == ("mom", "dad")
    assert len(weeks["W-C"].steps) == 5 and weeks["W-C"].steps[-1].kind == "check"


def test_A_의_단계는_부록_대본_순서대로다() -> None:
    a = next(w for w in load_weeks() if w.id == "W-A")
    assert [s.at for s in a.steps][:3] == ["mon 07:50", "mon 12:40", "mon 18:20"]
    approve = next(s for s in a.steps if s.kind == "approve")
    assert approve.approve_of == "tue-notice" and approve.at == "wed 21:00"
    correct = next(s for s in a.steps if s.kind == "correct")
    assert correct.correct_of == "tue-dad-playground" and correct.verdict == "once_only"


_APPROVE_BAD = _MIN + '  - { at: "tue 09:00", by: mom, tag: b, kind: approve, approve_of: nope }\n'


@pytest.mark.parametrize(
    "bad",
    [
        _MIN.replace("W-X", "X-1"),  # id 모양
        _MIN.replace("tag: a, say: 안녕", "tag: a"),  # say 없음
        _MIN.replace("by: mom,", "by: dad,"),  # world.parents 밖
        _MIN.replace("mon 08:10", "mon 25:10"),  # 시각
        _MIN.replace("say: 안녕", "say: 안녕, 오타칸: 1"),  # extra forbid
        _MIN + '  - { at: "mon 09:00", by: mom, tag: a, say: 또 }\n',  # tag 중복
        _MIN + '  - { at: "sun 09:00", by: mom, tag: b, say: 늦음 }\n'
        '  - { at: "mon 09:00", by: mom, tag: c, say: 이름 }\n',  # 시각 역행
        _APPROVE_BAD,  # 앞 단계가 아닌 tag
        _MIN + '  - { at: "tue 09:00", by: mom, tag: b, kind: correct, correct_of: a }\n',
        _MIN + '  - { at: "tue 09:00", by: mom, tag: b, kind: check }\n',  # check 없음
    ],
)
def test_틀린_대본은_실행_전에_실패한다(bad: str) -> None:
    with pytest.raises(ValidationError):
        load_week_text(bad)


def test_최소_대본은_읽힌다() -> None:
    case = load_week_text(_MIN)
    assert isinstance(case, WeekCase) and isinstance(case.steps[0], Step)


def test_주의_월요일은_실행일_뒤_7일_이상이다() -> None:
    assert simulation_monday(date(2026, 11, 7)) == date(2026, 11, 16)  # 토 +7 = 토 → 월
    assert simulation_monday(date(2026, 11, 9)) == date(2026, 11, 16)  # 월 +7 = 월
    for offset in range(14):
        today = date.fromordinal(date(2026, 11, 7).toordinal() + offset)
        monday = simulation_monday(today)
        assert monday.weekday() == 0 and monday.toordinal() - today.toordinal() >= 7


def test_단계_시각은_그_주_요일_KST다() -> None:
    monday = date(2026, 11, 16)
    moment = step_moment(monday, "thu 08:30")
    assert (moment.month, moment.day, moment.hour, moment.minute) == (11, 19, 8, 30)
    assert str(moment.tzinfo) == "Asia/Seoul"
    assert step_day(monday, "sun 20:00") == date(2026, 11, 22)


def test_문장_틀은_월요일_기준_날짜로_바뀐다() -> None:
    got = render_say("일시 {d+10} 오전, {d+3} 에", date(2026, 11, 16))
    assert got == "일시 11월 26일 오전, 11월 19일 에"
    assert render_say("그대로", date(2026, 11, 16)) == "그대로"


def test_가짜_임베더는_같은_글은_같은_벡터_다른_글은_직교다() -> None:
    vectors = asyncio.run(HashEmbedder().embed(["블록 놀이", "블록놀이", "퍼즐"]))
    assert len(vectors[0]) == 1536
    assert vectors[0] == vectors[1]  # 공백 정규화
    assert sum(a * b for a, b in zip(vectors[0], vectors[2], strict=True)) == 0.0
    assert sum(vectors[0]) == 1.0


def test_가짜_판정기는_이름이_포함되면_그_후보_아니면_none() -> None:
    judge = ContainsJudge()
    hit = asyncio.run(
        judge.judge(subject="블록 쌓기", domain="activity", candidates=["퍼즐", "블록"])
    )
    miss = asyncio.run(judge.judge(subject="그네", domain="activity", candidates=["퍼즐", "블록"]))
    assert hit.choice == "블록" and miss.choice == "none"


# ── SSE 파서 · 프레임 → Trace ───────────────────────────────────────────────────

_BODY = (
    'event: step\ndata: {"index":1,"total":3,"label":"읽는 중"}\n\n'
    'event: saved\ndata: {"observations":[{"id":"o1"},{"id":"o2"}]}\n\n'
    ": 주석뿐인 프레임\n\n"
    "event: ping\ndata: {}\n\n"
    'event: guidance\ndata: {"code":"diagnosis","message":"m","deeplink":null}\n\n'
    'event: event_draft\ndata: {"drafts":[{"op":"create","event":{"title":"소풍"},"items":[]}]}\n\n'
    'event: agent_result\ndata: {"agent":"food","task_type":"t","status":"completed","readouts":'
    '[{"kind":"suggestion","title":"두부조림","body":"담백해요"}],"question":null,'
    '"awaits_reply":false,"suggestion_ids":["s1"],"kind":"personalized","reason":"r",'
    '"record_count":null}\n\n'
    'event: agent_result\ndata: {"agent":"health","task_type":"t","status":"completed","readouts":'
    '[{"kind":"notice","title":"기록","body":"저장"}],"question":null,"awaits_reply":false,'
    '"suggestion_ids":[],"kind":null,"reason":null,"record_count":null}\n\n'
    'event: agent_result\ndata: {"agent":"memory","task_type":"t","status":"completed",'
    '"readouts":[],"question":"언제 있었던 일이에요?","awaits_reply":true,"suggestion_ids":[],'
    '"kind":null,"reason":null,"record_count":null}\n\n'
    'event: partial\ndata: {"reason":"timeout","succeeded":["food"],"failed":["activity"],'
    '"retryable":[]}\n\n'
    'event: done\ndata: {"run_id":"r1","model_calls":4}\n\n'
)


def test_SSE_파서는_이벤트와_데이터를_나눈다() -> None:
    frames = parse_sse(_BODY)
    assert [name for name, _ in frames] == [
        "step",
        "saved",
        "ping",
        "guidance",
        "event_draft",
        "agent_result",
        "agent_result",
        "agent_result",
        "partial",
        "done",
    ]  # 주석 프레임은 버린다
    assert frames[1][1]["observations"][1]["id"] == "o2"
    assert parse_sse('event: x\r\ndata: {"a":1}\r\n\r\n') == [("x", {"a": 1})]
    assert parse_sse("") == []


def test_프레임에서_Trace_칸을_채운다() -> None:
    trace = Trace(case_id="x", attempt=0, event_level="sse")
    info = apply_frames(trace, parse_sse(_BODY))
    assert trace.event_names[0] == "step" and "ping" not in trace.event_names
    assert trace.route == ("food", "health")  # memory 되묻기는 라우트가 아니다
    assert trace.intent == "mixed"  # saved + 도메인 Agent
    assert trace.guidance == ("diagnosis",)
    assert trace.questions == ("언제 있었던 일이에요?",)
    assert info.awaits_reply is True
    assert trace.agent_texts == ("두부조림", "담백해요")
    assert trace.health_texts == ("기록", "저장")
    assert trace.bundle_kinds == {"food": "personalized", "health": None}
    assert trace.event_drafts == 1 and info.drafts[0]["event"]["title"] == "소풍"
    assert info.suggestion_ids == ["s1"] and info.saved_ids == ["o1", "o2"]
    assert trace.partial is True and trace.model_calls == 4 and trace.failed is None


def test_의도는_저장_유무와_도메인_Agent_로_읽는다() -> None:
    def intent(body: str) -> str | None:
        trace = Trace(case_id="x", attempt=0, event_level="sse")
        apply_frames(trace, parse_sse(body))
        return trace.intent

    saved = 'event: saved\ndata: {"observations":[{"id":"o"}]}\n\n'
    agent = (
        'event: agent_result\ndata: {"agent":"activity","readouts":[],"question":null,'
        '"awaits_reply":false,"suggestion_ids":[],"kind":null,"record_count":null}\n\n'
    )
    unavailable = 'event: unavailable\ndata: {"agents":["growth"]}\n\n'
    assert intent(saved) == "record"
    assert intent(agent) == "request"
    assert intent(unavailable) == "request"
    assert intent(saved + unavailable) == "mixed"
    assert intent('event: done\ndata: {"run_id":"r","model_calls":1}\n\n') is None


def test_실패_프레임과_메모_되묻기() -> None:
    trace = Trace(case_id="x", attempt=0, event_level="sse")
    info = apply_frames(
        trace,
        parse_sse(
            'event: note\ndata: {"text":"몇 시였어요?","kind":"question"}\n\n'
            'event: failed\ndata: {"reason":"timeout","raw_text":"x"}\n\n'
        ),
    )
    assert trace.questions == ("몇 시였어요?",) and info.awaits_reply is True
    assert trace.failed == "timeout" and trace.memory_text == "몇 시였어요?"


def _agent_frame(agent: str, status: str, question: str | None = None) -> str:
    return (
        f'event: agent_result\ndata: {{"agent":"{agent}","task_type":"t","status":"{status}",'
        f'"readouts":[],"question":{json.dumps(question, ensure_ascii=False)},'
        '"awaits_reply":false,"suggestion_ids":[],"kind":"personalized","reason":null,'
        '"record_count":null}\n\n'
    )


def test_실패한_Agent_는_route_와_bundle_kinds_에_안_센다() -> None:
    trace = Trace(case_id="x", attempt=0, event_level="sse")
    apply_frames(
        trace, parse_sse(_agent_frame("food", "completed") + _agent_frame("health", "failed"))
    )
    assert trace.route == ("food",)
    assert trace.bundle_kinds == {"food": "personalized"}


def test_unavailable_프레임은_route_에_안_더한다() -> None:
    trace = Trace(case_id="x", attempt=0, event_level="sse")
    apply_frames(trace, parse_sse('event: unavailable\ndata: {"agents":["growth"]}\n\n'))
    assert trace.route == ()


def test_같은_글의_note_와_agent_질문은_한_번만_센다() -> None:
    trace = Trace(case_id="x", attempt=0, event_level="sse")
    apply_frames(
        trace,
        parse_sse(
            'event: note\ndata: {"text":"언제였어요?","kind":"question"}\n\n'
            + _agent_frame("food", "completed", "언제였어요?")
        ),
    )
    assert trace.questions == ("언제였어요?",)


# ── 판정 · 결과 한 줄 ──────────────────────────────────────────────────────────


def test_끝_상태_점검은_DB_상태_불변식과_점검_항목을_본다() -> None:
    clean = Trace(
        case_id="x",
        attempt=0,
        safety_before=("a",),
        safety_after=("a",),
        confirmed_days={"블록": 3},
    )
    assert judge_check(clean, misses=[]).passed
    broken = Trace(
        case_id="x", attempt=0, safety_before=("a",), safety_after=(), confirmed_days={"퍼즐": 1}
    )
    verdict = judge_check(broken, misses=["블록 관심이 confirmed 가 아니다"])
    assert not verdict.passed
    assert {v.rule for v in verdict.violations} == {"I5", "I8"}
    assert verdict.misses == ("블록 관심이 confirmed 가 아니다",)
    assert [v.rule for v in check_state(broken)] == ["I5", "I8"]


def test_단계_판정은_불변식과_기대와_종류별_확인을_합친다() -> None:
    trace = Trace(case_id="x", attempt=0, event_level="sse", event_names=("done",))
    ok = judge_step(trace, Expect(), allergies=(), today=date(2026, 11, 16))
    assert ok.passed
    bad = judge_step(
        trace,
        Expect(saved=SavedExpect(min_rows=1)),
        allergies=(),
        today=date(2026, 11, 16),
        extra_misses=["일정 등록 422"],
    )
    assert not bad.passed
    assert "일정 등록 422" in bad.misses and any("저장 0행" in m for m in bad.misses)


def _row(case: str, tag: str, kind: str, by: str, day: str, *, passed: bool = True) -> dict:
    spec = (
        {"at": "mon 08:10", "by": by, "tag": tag, "say": "안녕", "ability": ["추출", "시간"]}
        if kind != "check"
        else {"at": "sun 22:00", "by": by, "tag": tag, "kind": "check", "check": {}}
    )
    step = Step.model_validate(spec)
    trace = Trace(case_id=f"{case}:{tag}", attempt=0, event_level="sse", event_names=("done",))
    misses = [] if passed else ["미달"]
    if kind == "check":
        verdict = judge_check(trace, misses=misses)
    else:
        trace.usage = (
            {
                "run_id": "r",
                "role": "x",
                "model": "m",
                "prompt_tokens": 100,
                "cached_prompt_tokens": 0,
                "completion_tokens": 20,
                "reasoning_tokens": 0,
                "latency_ms": 5,
            },
        )
        verdict = judge_step(
            trace, Expect(), allergies=(), today=date(2026, 11, 16), extra_misses=misses
        )
    row = record_week(
        case, step, trace, verdict, persona="p", day=date.fromisoformat(day), input_count=1
    )
    row["git"] = {"sha": "abc", "dirty": False}
    return row


def test_결과_한_줄은_W_칸을_갖는다() -> None:
    row = _row("W-A", "mon-evening", "say", "mom", "2026-11-16")
    assert row["case"] == "W-A:mon-evening" and row["week"] == "W-A"
    assert row["ability"] == "추출" and row["severity"] == "quality" and row["repeat"] == 1
    assert row["step_kind"] == "say" and row["by"] == "mom" and row["day"] == "2026-11-16"
    end = _row("W-A", "end", "check", "mom", "2026-11-22")
    assert end["input_kind"] == "end_state" and end["ability"] == "-"
    spec = {"at": "thu 08:30", "by": "mom", "tag": "ap", "kind": "approve", "approve_of": "x"}
    approve = record_week(
        "W-A",
        Step.model_validate(spec),
        Trace(case_id="x", attempt=0),
        judge_check(Trace(case_id="x", attempt=0), misses=[]),
        persona="p",
        day=date(2026, 11, 19),
        input_count=0,
    )
    assert approve["input_kind"] == "approve" and approve["step_kind"] == "approve"


def test_한도_분석은_보호자_날짜별_입력_수로_N_마다_막히는_수를_센다() -> None:
    rows = [_row("W-B", f"s{i}", "say", "dad", "2026-12-23") for i in range(8)]
    rows += [
        _row("W-B", "m1", "say", "mom", "2026-12-23"),
        _row("W-B", "m2", "say", "dad", "2026-12-24"),
        _row("W-B", "end", "check", "dad", "2026-12-27"),
    ]
    counts = daily_inputs(rows)["W-B"]
    assert counts[("dad", "2026-12-23")] == 8 and counts[("mom", "2026-12-23")] == 1
    assert [blocked_at(counts, n) for n in (5, 8, 10)] == [3, 0, 0]  # 점검 단계는 입력이 아니다
    assert sum(counts.values()) == 10


def test_리포트는_W_행이_섞여도_그려지고_W_요약을_낸다() -> None:
    rows = [
        _row("W-A", "mon-breakfast", "say", "mom", "2026-11-16"),
        _row("W-A", "mon-evening", "say", "mom", "2026-11-16", passed=False),
        _row("W-A", "end-state", "check", "mom", "2026-11-22", passed=False),
        _row("W-C", "mon-weekend", "say", "mom", "2026-11-16"),
        {"case": "W-C:wed", "skipped": "token_cap", "repeat": 1},
    ]
    md = render(rows, source="week.jsonl", sha="abc", day="2026-11-08")
    assert "## W · 일주일 시나리오" in md
    assert "가짜 임베딩" in md
    assert "| W-A | 1/2 | ❌ |" in md  # 단계 통과 · 끝 상태 점검
    assert "| W-C | 1/1 | 없음 |" in md
    assert "N=5" in md and "N=10" in md
    assert "토큰 상한으로 건너뛴 단계 1개" in md
    assert "W-A:mon-evening" in md  # 실패한 케이스 표에도 그려진다
    assert "## 비용 · 지연" in md


def test_P_만_있는_결과에는_W_절이_없다() -> None:
    md = render([], source="x", sha="abc", day="2026-11-08")
    assert "일주일 시나리오" not in md


def test_실패_응답은_상태_코드와_오류_코드만_남긴다() -> None:
    import httpx

    body = {"error": {"code": "validation_error", "message": "입력 일부", "detail": {"x": 1}}}
    coded = httpx.Response(422, json=body)
    assert response_code(coded) == "422 validation_error"
    plain = httpx.Response(500, text="본문에 섞인 값")
    assert response_code(plain) == "500"


# ── 끝 상태 점검 — 리뷰 11-08 ────────────────────────────────────────────────────


def test_not_confirmed_는_그_관심이_있어야_통과한다() -> None:
    def misses(states: dict[str, str]) -> list[str]:
        return interest_misses(states, confirmed=(), not_confirmed=("퍼즐",))

    assert misses({"퍼즐": "candidate"}) == []
    assert misses({}) and "없다" in misses({})[0]  # 행이 아예 없으면 점검이 안 된 것
    assert misses({"퍼즐": "confirmed"})
    assert interest_misses({"블록놀이": "confirmed"}, confirmed=("블록",), not_confirmed=()) == []
    assert interest_misses({"블록놀이": "candidate"}, confirmed=("블록",), not_confirmed=())


def test_확정된_관심은_그_뒤_추천의_근거로_쓰여야_한다() -> None:
    confirmed_at = {"a1": ("블록놀이", 4)}
    assert affinity_cite_misses(("블록",), confirmed_at=confirmed_at, cites=[(6, "a1")]) == []
    # 확정되기 전(같은 단계 포함 — 그 run 의 인용은 Curator 보다 먼저다)의 인용은 세지 않는다
    assert affinity_cite_misses(("블록",), confirmed_at=confirmed_at, cites=[(4, "a1")])
    assert affinity_cite_misses(("블록",), confirmed_at=confirmed_at, cites=[(6, "a2")])
    [miss] = affinity_cite_misses(("블록",), confirmed_at={}, cites=[])
    assert "확정된 적이 없어" in miss


def test_정정한_기록은_뒤_추천의_근거에_없어야_한다() -> None:
    corrections = [("once_only", "o1", 10)]
    assert corrected_misses(("once_only",), corrections=corrections, cited=[(9, "o1")]) == []
    assert corrected_misses(("once_only",), corrections=corrections, cited=[(12, "o1")])
    [miss] = corrected_misses(("wrong",), corrections=corrections, cited=[])
    assert "점검할 수 없다" in miss


def test_A_의_정정_대상은_뒤에서_인용될_만한_놀이_기록이다() -> None:
    [week] = [w for w in load_weeks() if w.id == "W-A"]
    tags = [s.tag for s in week.steps]
    correct = next(s for s in week.steps if s.kind == "correct")
    target = next(s for s in week.steps if s.tag == correct.correct_of)
    # Activity · Growth 만 once_only 를 근거에서 뺀다 — Food · Health 는 설계상 계속 읽는다
    assert target.expect.saved is not None and target.expect.saved.domains == ("activity",)
    assert correct.match and target.say and re.search(correct.match, target.say)
    later = week.steps[tags.index(correct.tag) + 1 :]
    assert any(s.expect.suggestions.get("activity") for s in later if s.kind == "say")
    end = next(s for s in week.steps if s.kind == "check")
    assert end.check is not None and "블록" in end.check.cited_affinity


def test_남은_W_행을_고르는_표식() -> None:
    assert W_MARK and parent_nickname("mom").startswith(W_MARK)
    assert W_LOCK == (777, 1)  # 두 int 꼴 — Curator 의 bigint 잠금과 공간이 겹치지 않는다


class _FakeSession:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def __aenter__(self) -> "_FakeSession":
        self.log.append("open")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.log.append("close")

    async def execute(self, statement: object, params: dict | None = None) -> None:
        self.log.append(f"{statement} {params}")


async def test_W_잠금은_실행_전체를_감싸고_예외에도_푼다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tests.eval.suite import week_runner

    log: list[str] = []
    monkeypatch.setattr(week_runner, "async_session_factory", lambda: _FakeSession(log))
    with pytest.raises(RuntimeError):
        async with week_runner.week_lock():
            log.append("run")
            raise RuntimeError
    assert log[0] == "open" and "pg_advisory_lock" in log[1] and log[2] == "run"
    assert "pg_advisory_unlock" in log[3] and log[4] == "close"
    assert "{'a': 777, 'b': 1}" in log[1]


async def test_남은_W_행은_표식_보호자와_eval_레시피만_지운다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from uuid import uuid4

    from tests.eval.suite import week_runner

    parent, child = uuid4(), uuid4()
    seen: dict[str, object] = {}

    async def rows(sql: str, **params: object) -> list[tuple[object]]:
        if "from parent" in sql:
            seen["parent_like"] = params["m"]
            return [(parent,)]
        return [(child,)]

    async def drop(children: list, parents: list) -> None:
        seen["drop"] = (children, parents)

    async def recipes(keys: list) -> None:
        seen["recipes"] = keys

    monkeypatch.setattr(week_runner, "_rows", rows)
    monkeypatch.setattr(week_runner, "_drop", drop)
    monkeypatch.setattr(week_runner, "drop_recipes", recipes)
    assert await week_runner.drop_leftovers() == (1, 1)
    assert seen["parent_like"] == f"{W_MARK}%"
    assert seen["drop"] == ([child], [parent])
    assert seen["recipes"] == week_runner.recipe_keys() and seen["recipes"]


def test_W_요약은_시험_못_한_단계를_실패로_세지_않는다() -> None:
    asked = _row("W-A", "mon-dinner-ask", "say", "mom", "2026-11-16", passed=False)
    asked["status"] = "untested"
    asked["misses"] = ["food 추천 없음"]
    rows = [_row("W-A", "mon-breakfast", "say", "mom", "2026-11-16"), asked]
    md = render(rows, source="week.jsonl", sha="abc", day="2026-11-08")
    assert "| W-A | 1/2 · 판정 못 함 1 | 없음 | " in md
    assert "W-A:mon-dinner-ask: food" not in md  # 첫 실패 이유로 올리지 않는다
