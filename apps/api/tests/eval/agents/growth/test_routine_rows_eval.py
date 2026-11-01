"""Growth 예절 · 습관 · 리듬 문서 행 eval — 시드 행과 코드 경로가 실제로 얼마나 잘 고르는지 잰다.

    uv run pytest tests/eval/agents/growth/test_routine_rows_eval.py -s          # 1~6: 모델 없음
    uv run pytest tests/eval/agents/growth/test_routine_rows_eval.py -m live -s  # 7: 실제 모델

1 ~ 6 은 키 없이 기본 실행에서 돈다 (실제 시드 `reference/growth_doc.yaml` 을 읽는다).
  1 예절 행 이름이 관찰 subject 를 얼마나 맞추는가 (적중 · 오탐)
  2 습관 trigger → 상황 태그 사전의 정밀도 · 재현율
  3 0–11개월 리듬 안내는 모델 0회로 시드 행 본문이 나가는가 (모든 월령 · 여러 날)
  4 월령 × 종류 커버리지 — 빈 월령이 없는가
  5 돌려 고르기 — 같은 날 같은 아이는 같은 행, 아이 · 날짜가 달라지면 구간 행을 두루 쓰는가
  6 증상 · 의료 닫힘이 전략 행보다 먼저인가 (요청 문장 · 관찰 subject 둘 다)
7 은 실제 모델이 코드가 고른 행만 옮기는지 본다. GROWTH_* 가 비면 MEMORY_* 로 대신한다
  (AgentSettings 규칙). 모델 설정이 없으면 건너뛴다.

결과 파일(test_routine_rows_eval_results.jsonl, gitignore)에는 건수 · 비율 · 행 키만 남긴다.
문장은 전부 지어낸 것이다 — 실제 사용자 발화 · 아이 정보를 쓰지 않는다.
"""

import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.agent import run
from app.agents.growth.context import GrowthContext, build_gate
from app.agents.growth.readouts import (
    CLOSED_MEDICAL_ROUTINE,
    CLOSED_SYMPTOM_HABIT,
    DOC_NO_ROW,
    READOUTS,
)
from app.agents.growth.schemas.memory import SearchRoutineMemoryArgs
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import (
    InMemoryGrowthDocs,
    InMemoryGrowthMemory,
    in_memory_ports,
)
from app.agents.growth.store.ports import RoutineObservation
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.docs import DOC_LIMIT, search_growth_doc, strategy_rows
from app.agents.growth.tools.memory import search_routine_memory
from app.agents.growth.tools.triggers import trigger_tag_set, trigger_tags
from app.rules.evaluative import find_evaluative

CHILD = UUID(int=7)
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 18, 10, tzinfo=KST)
TODAY = date(2026, 10, 18)
RESULTS = Path(__file__).with_name("test_routine_rows_eval_results.jsonl")
DOCS = InMemoryGrowthDocs.from_seed()


def born(months: int, today: date = TODAY) -> date:
    return today - timedelta(days=int(months * 30.44) + 1)


def _write(record: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")


class NoModel:
    """모델을 부르면 곧바로 실패한다 — 모델 0회여야 하는 경로를 고정한다."""

    async def chat(self, messages, **kwargs):
        raise AssertionError("이 경로는 모델을 부르면 안 된다")


def make_context(months: int, *, routine=(), child=CHILD, now=NOW, docs=DOCS) -> GrowthContext:
    ports = in_memory_ports(
        child,
        born(months, now.date()),
        consent=False,
        docs=docs,
        memory=InMemoryGrowthMemory(routine=list(routine)),
    )
    return GrowthContext(child_id=child, run_id="eval-routine", now=now, timezone=KST, ports=ports)


def routine_task(text: str) -> DomainTask:
    return DomainTask(
        run_id="eval-routine", agent="growth", task_type="routine_coaching", request_texts=(text,)
    )


def obs(n: int, subject: str, category: str, *, trigger: str | None = None, days_ago: int = 1):
    return RoutineObservation(
        id=UUID(int=9000 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        routine_category=category,
        polarity=0,
        trigger=trigger,
    )


# ── 1. 예절 행 이름 적중 ─────────────────────────────────────────────────────

# (월령, 관찰 subject, 기대하는 행 주제 — 없으면 None). Memory 가 쓸 법한 말로 지어낸 것이다
MANNER_CASES = [
    (40, "인사하기", "greeting"),
    (40, "인사", "greeting"),
    (40, "약속 지키기", "promise"),
    (30, "약속 지키기", "promise"),
    (40, "약속", "promise"),
    (40, "차례 지키기", "turns"),
    (40, "고운 말 쓰기", "kind_words"),
    (40, "친구 도와주기", "helping"),
    (40, "화해하기", "make_up"),
    (40, "이야기 듣기", "listening"),
    (30, "친구와 놀기", "play_together"),
    (30, "친구 마음 살피기", "feelings"),
    (40, "다름 받아들이기", "respect"),
    # 아직 이름이 안 맞는 말 — 적중률에서 빠지지 않게 같이 잰다
    (40, "차례 기다리기", "turns"),
    (40, "동생 도와주기", "helping"),
    (40, "친구 달래기", "make_up"),
    (40, "양보하기", "turns"),
    (40, "고맙다고 말하기", "kind_words"),
    (40, "인사 안 하기", "greeting"),
    # 맞으면 안 되는 말
    (40, "양치하기", None),
    (40, "공룡 놀이", None),
    (30, "인사하기", None),  # 인사 행은 36개월부터 — 2세에는 행이 없다
    (30, "차례 지키기", None),
]


async def test_1_예절_행_이름_적중표():
    hits, misses, wrong = 0, [], []
    expected_total = sum(1 for _, _, topic in MANNER_CASES if topic)
    for months, subject, topic in MANNER_CASES:
        rows = await strategy_rows(DOCS, months=months, category="social_manner", subject=subject)
        topics = {row.doc_key.split(".")[2] for row in rows}
        if topic is None:
            if rows:
                wrong.append((months, subject, sorted(topics)))
        elif topic in topics:
            hits += 1
        else:
            misses.append((months, subject))
    rate = hits / expected_total
    _write({"case": "manner_names", "hit_rate": round(rate, 3), "misses": misses, "wrong": wrong})
    print(f"\n예절 이름 적중 {hits}/{expected_total} = {rate:.0%}\n  놓침 {misses}\n  오탐 {wrong}")
    assert not wrong  # 상관없는 행동에 예절 행이 붙으면 안 된다
    # 행 이름 그대로인 subject(앞의 13개)는 전부 맞아야 한다
    for months, subject, topic in [case for case in MANNER_CASES[:13] if case[2]]:
        rows = await strategy_rows(DOCS, months=months, category="social_manner", subject=subject)
        assert topic in {row.doc_key.split(".")[2] for row in rows}, (months, subject)
    assert rate >= 0.65, (rate, misses)


# ── 2. 습관 trigger 사전 ─────────────────────────────────────────────────────

# (보호자가 말한 trigger, 기대 태그 집합). 지어낸 말이다
TRIGGER_CASES = [
    ("긴장할 때", {"anxious"}),
    ("낯선 사람을 만나면", {"anxious"}),
    ("처음 가는 곳에서 불안해할 때", {"anxious"}),
    ("무서울 때", {"anxious"}),
    ("겁이 날 때", {"anxious"}),
    ("낯가림이 심할 때", {"anxious"}),
    ("화가 날 때", {"frustrated"}),
    ("속상할 때", {"frustrated"}),
    ("짜증 날 때", {"frustrated"}),
    ("혼난 뒤에", {"frustrated"}),
    ("떼를 쓸 때", {"frustrated"}),
    ("졸릴 때", {"tired"}),
    ("피곤하면", {"tired"}),
    ("잠들기 전에", {"tired"}),
    ("낮잠 자기 전에", {"tired"}),
    ("자기 전에", {"tired"}),
    ("졸려서 칭얼댈 때", {"tired"}),
    ("심심할 때", {"bored"}),
    ("지루할 때", {"bored"}),
    ("기다릴 때", {"bored"}),
    ("할 일이 없을 때", {"bored"}),
    ("가만히 있을 때", {"bored"}),
    ("기다리다가 화가 날 때", {"bored", "frustrated"}),
    # 어느 태그에도 걸리면 안 되는 말
    ("TV 볼 때", set()),
    ("밥 먹을 때", set()),
    ("친구와 놀 때", set()),
    ("엄마 옆에 있을 때", set()),
    ("차 탈 때", set()),
    ("책 읽을 때", set()),
    # 낱말이 비슷해서 오탐이 나기 쉬운 말
    ("동생을 잘 때려요", set()),
    ("자기 전용 컵을 뺏길 때", set()),
    ("화장실 갈 때", set()),
]


async def test_2_습관_trigger_사전의_정밀도와_재현율():
    tp = fp = fn = 0
    errors = []
    for text, expected in TRIGGER_CASES:
        got = set(trigger_tags([text]))
        tp += len(got & expected)
        fp += len(got - expected)
        fn += len(expected - got)
        if got != expected:
            errors.append((text, sorted(expected), sorted(got)))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    _write(
        {
            "case": "trigger_dict",
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "errors": errors,
        }
    )
    print(f"\ntrigger 사전 정밀도 {precision:.0%} · 재현율 {recall:.0%}\n  틀린 것 {errors}")
    assert set(tag for _, tags in TRIGGER_CASES for tag in tags) <= trigger_tag_set()
    # 오탐은 엉뚱한 대응 행이 아이에게 나가는 것이라 한 건도 허용하지 않는다
    assert fp == 0, errors
    assert recall >= 0.9, errors


# ── 3. 리듬 안내 — 모델 0회 ──────────────────────────────────────────────────


async def test_3_리듬_안내는_모든_월령에서_모델_없이_시드_행이_나간다():
    seed_bodies = {row.body async for row in _rows(DOCS, "rhythm_info", range(0, 12))}
    seen_titles: Counter = Counter()
    draws = 0
    for months in range(0, 12):
        for child in range(1, 7):
            for day in range(7):
                now = NOW + timedelta(days=day)
                context = make_context(months, now=now, child=UUID(int=child))
                result = await run(
                    routine_task("하루를 어떻게 보내면 좋을까"), context, client=NoModel()
                )
                assert (result.status, result.model_calls) == ("completed", 0), (months, day)
                (readout,) = result.readouts
                assert readout.kind == "rhythm_info" and readout.body in seed_bodies
                assert len(readout.source_refs) == 1  # 출처 행을 달았다
                assert not find_evaluative(readout.body) and not find_evaluative(readout.title)
                assert not any(ch.isdigit() for ch in readout.body)  # 시간 · 횟수 숫자 없음
                seen_titles[readout.title] += 1
                draws += 1
    _write({"case": "rhythm_model_free", "distinct_rows": len(seen_titles), "draws": draws})
    print(f"\n리듬 12개월 × 아이 6 × 7일 = {draws}회, 서로 다른 안내 {len(seen_titles)}개")
    assert len(seen_titles) >= 8  # 한 가지만 계속 나가면 안 된다


async def _rows(docs, row_type, months_range):
    seen = set()
    for months in months_range:
        for row in await docs.search(
            months=months,
            row_type=row_type,
            routine_category=None,
            trigger_tags=(),
            query="",
            limit=99,
        ):
            if row.id not in seen:
                seen.add(row.id)
                yield row


# ── 4. 월령 커버리지 ─────────────────────────────────────────────────────────


async def test_4_월령_종류_커버리지에_빈_곳이_없다():
    table: dict[str, dict[int, int]] = defaultdict(dict)
    for months in range(0, 72):
        if months < 12:
            rhythm = await search_growth_doc(
                DOCS, task_type=GrowthTaskType.ROUTINE_COACHING, months=months, rotation="c|d"
            )
            table["rhythm"][months] = len(rhythm)
        if months >= 24:
            manner = {
                topic
                for subject in (
                    "인사하기",
                    "약속 지키기",
                    "차례 지키기",
                    "고운 말 쓰기",
                    "친구 도와주기",
                    "화해하기",
                    "친구와 놀기",
                    "친구 마음 살피기",
                    "다름 받아들이기",
                    "이야기 듣기",
                )
                for topic in [
                    r.doc_key.split(".")[2]
                    for r in await strategy_rows(
                        DOCS, months=months, category="social_manner", subject=subject
                    )
                ]
            }
            table["manner_topics"][months] = len(manner)
        if months >= 36:
            for tag in sorted(trigger_tag_set()):
                rows = await strategy_rows(
                    DOCS, months=months, category="habit", trigger_tags=(tag,), rotation="c|d"
                )
                table[f"habit_{tag}"][months] = len(rows)
    holes = {
        name: [m for m, n in by_month.items() if n < (DOC_LIMIT if name != "manner_topics" else 2)]
        for name, by_month in table.items()
    }
    _write({"case": "coverage", "holes": holes})
    print("\n빈 월령(기대 수 미만):", {k: v for k, v in holes.items() if v} or "없음")
    assert all(not months for months in holes.values()), holes


# ── 5. 돌려 고르기 ───────────────────────────────────────────────────────────


async def test_5_돌려_고르기는_같은_날_같은_행이고_아이와_날짜가_바뀌면_두루_쓴다():
    # 같은 아이 · 같은 날은 같은 행
    first = await strategy_rows(
        DOCS, months=40, category="habit", trigger_tags=("bored",), rotation="child-1|2026-10-18"
    )
    again = await strategy_rows(
        DOCS, months=40, category="habit", trigger_tags=("bored",), rotation="child-1|2026-10-18"
    )
    assert first == again and len(first) == DOC_LIMIT

    used: dict[str, Counter] = {}
    for tag in sorted(trigger_tag_set()):
        used[tag] = Counter()
        for child in range(12):
            for day in range(5):
                rows = await strategy_rows(
                    DOCS,
                    months=40,
                    category="habit",
                    trigger_tags=(tag,),
                    rotation=f"child-{child}|2026-10-{10 + day}",
                )
                used[tag].update(row.doc_key for row in rows)
    spread = {tag: len(counter) for tag, counter in used.items()}
    _write({"case": "rotation_spread", "rows_used_per_tag": spread})
    print(f"\n태그마다 쓰인 행 수 (60번 뽑기, 구간 행 5개): {spread}")
    assert all(count == 5 for count in spread.values()), spread  # 5행 모두 쓰인다
    # 한 행에 몰리지 않는다 — 가장 많이 나온 행이 전체의 절반을 넘지 않는다
    for tag, counter in used.items():
        assert max(counter.values()) <= sum(counter.values()) / 2, (tag, counter)


# ── 6. 닫힘이 먼저 ───────────────────────────────────────────────────────────

CLOSED_REQUESTS = [
    ("눈을 자꾸 깜빡여서 고치고 싶어", CLOSED_SYMPTOM_HABIT),
    ("머리를 박는 행동을 고치고 싶어", CLOSED_SYMPTOM_HABIT),
    ("약 먹기 싫어하는 걸 연습시키고 싶어", CLOSED_MEDICAL_ROUTINE),
    ("안약 넣을 때 우는데 어떻게 하지", CLOSED_MEDICAL_ROUTINE),
]
# 관찰 subject 가 증상이면 trigger 가 사전에 걸려도 전략 행을 받지 않는다
SYMPTOM_SUBJECTS = ["눈 깜빡임", "머리 박기", "헛기침"]
# 닫힘 목록(reference/growth_closures.yaml — PM 이 정한다)에 아직 없는 말. 닫히지 않는 것을 재서
# 결과 파일에만 남긴다 — 목록을 고치는 건 PM 결정이라 여기서 단정하지 않는다
CLOSURE_GAPS = ["눈을 자꾸 깜빡이는 버릇을 고치고 싶어", "틱이 있어서 고치고 싶어"]
# 흔한 습관은 닫지 않는다 — 전략 행이 나가야 한다
COMMON_HABITS = [
    ("손톱 물어뜯기", "긴장할 때"),
    ("손가락 빨기", "졸릴 때"),
    ("코 파기", "심심할 때"),
]


async def test_6_증상과_의료는_전략_행보다_먼저_닫히고_흔한_습관은_닫지_않는다():
    for text, expected in CLOSED_REQUESTS:
        context = make_context(48)
        result = await run(routine_task(text), context, client=NoModel())
        assert result.status == "blocked" and result.model_calls == 0, text
        assert result.readouts == (READOUTS.render(expected),), (text, result.readouts)

    for subject in SYMPTOM_SUBJECTS:
        context = make_context(48, routine=[obs(1, subject, "habit", trigger="긴장할 때")])
        context.state.gate = await build_gate(context, GrowthTaskType.ROUTINE_COACHING)
        found = await search_routine_memory(context, SearchRoutineMemoryArgs())
        assert found.data["next_steps"] == [], subject
        picked = [p for p in context.state.steps.values() if p.category == "habit"]
        assert picked and all(p.step.readout_key == DOC_NO_ROW for p in picked), subject

    gaps = [text for text in CLOSURE_GAPS if closure_key((text,)) is None]
    _write({"case": "closure_gaps", "not_closed": gaps})
    print(f"\n닫힘 목록이 못 잡은 말: {gaps or '없음'}")

    over_closed = []
    for subject, trigger in COMMON_HABITS:
        context = make_context(48, routine=[obs(2, subject, "habit", trigger=trigger)])
        context.state.gate = await build_gate(context, GrowthTaskType.ROUTINE_COACHING)
        found = await search_routine_memory(context, SearchRoutineMemoryArgs())
        if not found.data["next_steps"]:
            over_closed.append((subject, trigger))
    _write({"case": "closures_first", "over_closed": over_closed})
    print(f"\n흔한 습관 중 닫힌 것: {over_closed or '없음'}")
    assert not over_closed


# ── 7. 실제 모델 ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def llm() -> LLMClient:
    try:
        return LLMClient(role="growth")
    except LLMConfigError:
        pytest.skip("GROWTH_* (또는 MEMORY_*) 모델 설정이 필요하다 — apps/api/.env 를 확인한다")


LIVE_CASES = [
    (
        "habit",
        48,
        "손톱을 물어뜯는데 어떻게 하면 좋을까",
        [obs(11, "손톱 물어뜯기", "habit", trigger="긴장할 때")],
    ),
    ("manner", 40, "인사를 잘 안 하는데 연습시켜 줘", [obs(12, "인사하기", "social_manner")]),
    (
        "habit_tired",
        50,
        "손가락 빨기 어떻게 해",
        [obs(13, "손가락 빨기", "habit", trigger="졸릴 때")],
    ),
]


@pytest.mark.live
async def test_7_실제_모델은_코드가_고른_전략_행만_옮기고_근거를_단다(llm):
    for name, months, text, observations in LIVE_CASES:
        context = make_context(months, routine=observations)
        result = await run(routine_task(text), context, client=llm)
        chosen = {p.step.row.id for p in context.state.steps.values() if p.step.row is not None}
        _write(
            {
                "case": f"live_{name}",
                "status": result.status,
                "model_calls": result.model_calls,
                "kinds": [d.kind for d in result.suggestions],
                "chosen_rows": len(chosen),
            }
        )
        assert chosen, name  # 코드가 행을 골랐다
        assert result.status == "completed", (name, [r.body for r in result.readouts])
        assert 1 <= len(result.suggestions) <= len(chosen)
        for draft in result.suggestions:
            assert draft.kind == "personalized", name
            docs = {c.ref.id for c in draft.citations if c.ref.kind == "growth_doc"}
            assert docs and docs <= chosen, name  # 고르지 않은 행을 옮기지 않았다
            assert any(c.ref.is_child_record for c in draft.citations), name
            assert not find_evaluative(draft.content) and not find_evaluative(draft.reason)
            assert not any(word in draft.content for word in ("벌", "혼내", "진단", "증상")), draft
        await asyncio.sleep(0)
