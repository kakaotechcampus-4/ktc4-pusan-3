"""Growth 라이브 eval 의 가짜 세계 — 아이 · 관찰 · 측정 · 알레르기. 전부 지어낸 값이다.

`test_expected_growth.md` §0 의 픽스처 이름(C48 · C48-empty · C48-noconsent …)을 그대로 쓴다.
컨텍스트는 운영과 같은 `entrypoint._domain_contexts` 로 만든다 — 문서 행(`from_seed()`) · 기억(같은
Memory store 를 읽는 `StoreGrowthMemory`) · 생일 · 동의(`ChildValues`)가 운영 run 과 같다.
다른 것은 넷이다.

  - 안전은 `InMemorySafety` 를 `growth_safety` 로 넘긴다. 🚨 `child_health_granted` 를 늘 같이
    넘긴다 — 안 넘기면 동의 없음으로 읽혀 알레르기 대조 없이 교육이 열린다(Activity 는 기본값이
    동의 있음이라 이 함정이 없다). test_growth_world 가 고정한다
  - 운영 Memory store 에는 profile_affinity 가 없다(늘 0행). 관심이 필요한 픽스처(C48 의 농구 ·
    공룡)만 `WithAffinities` 로 덧댄다 — 0행 운영 모양은 C48-empty · C30 이 본다
  - 성장 기록은 `InMemoryGrowthLog`, 위험 사전 실패(C48-nohazard)는 `safety_rules` 를 바꿔 낀다
  - 문서 포트를 `RecordingDocs` 로 감싸 코드가 만든 검색어를 남긴다(보호자 발화 0건 판정)

기준 시각은 고정이다(`NOW`) — 성장 추이 문장 · [예시] 돌림(아이 · 날짜)이 날마다 바뀌지 않게.
`EVAL_NOW=2026-11-02` 처럼 바꿀 수 있다.
"""

import calendar
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents import entrypoint
from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.evidence import AffinityRow
from app.agents.growth.context import GrowthContext
from app.agents.growth.store.inmemory import InMemoryGrowthLog, InMemorySafety
from app.agents.growth.store.ports import (
    AffinityDomain,
    GrowthDocReader,
    GrowthDocRow,
    GrowthMeasurement,
    GrowthMemoryReader,
    SafetyEntry,
)
from app.agents.growth.tools.safety import SafetyRules, SafetyRulesUnavailable
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID("00000000-0000-7000-8000-000000000041")
PARENT = UUID("00000000-0000-7000-8000-000000000042")
_PINNED = os.getenv("EVAL_NOW")
# 수요일 오전
NOW = (
    datetime.fromisoformat(_PINNED).replace(hour=10, tzinfo=KST)
    if _PINNED
    else datetime(2026, 10, 14, 10, tzinfo=KST)
)
TODAY = NOW.date()

# (Memory 영역, 며칠 전, 칸). Growth 는 원문을 읽지 않아 원문은 한 줄로 둔다
Observation = tuple[str, int, dict[str, Any]]
_RAW = "eval 용으로 지어낸 기록"


def _routine(
    days_ago: int,
    subject: str,
    category: str,
    level: str | None = None,
    *,
    polarity: int = 0,
    trigger: str | None = None,
) -> Observation:
    fields: dict[str, Any] = {
        "subject": subject,
        "routine_category": category,
        "polarity": polarity,
    }
    if level is not None:
        fields["assistance_level"] = level
    if trigger is not None:
        fields["trigger"] = trigger
    return ("routine", days_ago, fields)


C48_EDUCATION: tuple[Observation, ...] = (
    ("education", 3, {"subject": "숫자", "topic": "계단 숫자 세기", "polarity": 1}),
    ("education", 8, {"subject": "공룡", "topic": "공룡 이름 말하기", "polarity": 1}),
)
C48_ACTIVITY: tuple[Observation, ...] = (
    ("activity", 2, {"subject": "농구공", "activity": "농구공 튀기기", "polarity": 1}),
)
# 같은 카테고리에 행동이 둘 이상인 것이 일부러 있다(양치 · 손 씻기, 숟가락 · 앉아서 먹기) — GAP-1
C48_ROUTINE: tuple[Observation, ...] = (
    _routine(2, "양치하기", "self_care", "partial_assist"),
    _routine(6, "손 씻기", "self_care", "full_assist"),
    _routine(3, "숟가락 쓰기", "mealtime", "verbal_prompt"),
    _routine(3, "앉아서 먹기", "mealtime", "partial_assist", polarity=-1),
    _routine(1, "등원 준비", "transition", "partial_assist"),
    _routine(4, "장난감 정리", "household_task", "full_assist"),
    _routine(5, "인사하기", "social_manner", "verbal_prompt"),
    _routine(2, "손톱 물어뜯기", "habit", polarity=-1),
)
# test_expected §0 "농구 · 공룡 confirmed affinity"
C48_AFFINITY: tuple[tuple[AffinityDomain, str], ...] = (
    ("activity", "농구"),
    ("education", "공룡"),
)
_ALLERGY = (
    SafetyEntry(kind="allergy", label="라텍스", status="active"),
    SafetyEntry(kind="allergy", label="꽃가루", status="active"),
    SafetyEntry(kind="environmental", label="고소공포", status="active"),
)


@dataclass(frozen=True)
class WorldSpec:
    months: int = 48
    consent: bool = True
    observations: tuple[Observation, ...] = C48_EDUCATION + C48_ACTIVITY + C48_ROUTINE
    affinities: tuple[tuple[AffinityDomain, str], ...] = C48_AFFINITY
    measurements: str = "two"  # none · one · two · five_weeks · six · oneside · onemetric · posture
    safety: tuple[SafetyEntry, ...] = ()
    safety_fails: bool = False
    hazard_fails: bool = False


_EMPTY: dict[str, Any] = {"observations": (), "affinities": ()}


def _only(*observations: Observation) -> dict[str, Any]:
    return {"observations": observations, "affinities": ()}


WORLDS: dict[str, WorldSpec] = {
    "C8": WorldSpec(months=8, measurements="five_weeks", **_EMPTY),
    "C11": WorldSpec(months=11, **_EMPTY),
    "C12": WorldSpec(months=12, **_EMPTY),
    "C15": WorldSpec(months=15, **_EMPTY),
    "C23": WorldSpec(months=23, **_EMPTY),
    "C24": WorldSpec(months=24, **_EMPTY),
    "C26-posture": WorldSpec(months=26, measurements="posture", **_EMPTY),
    "C30": WorldSpec(months=30, **_only(_routine(2, "양치하기", "self_care", "partial_assist"))),
    "C35": WorldSpec(months=35, **_EMPTY),
    "C36": WorldSpec(months=36, **_EMPTY),
    "C48": WorldSpec(),
    "C48-tooth": WorldSpec(**_only(_routine(1, "양치하기", "self_care", "verbal_prompt"))),
    # Memory 가 같은 run 에 방금 저장한 "혼자 양치했어" (T23)
    "C48-today": WorldSpec(**_only(_routine(0, "양치하기", "self_care", "independent"))),
    "C48-empty": WorldSpec(**_EMPTY),
    "C48-1m": WorldSpec(measurements="one"),
    "C48-six": WorldSpec(measurements="six"),
    "C48-oneside": WorldSpec(measurements="oneside"),
    "C48-onemetric": WorldSpec(measurements="onemetric"),
    "C48-noconsent": WorldSpec(consent=False),
    "C48-nosafety": WorldSpec(safety_fails=True),
    "C48-nohazard": WorldSpec(hazard_fails=True),
    "C48-allergy": WorldSpec(safety=_ALLERGY),
    "C48-allergy-noconsent": WorldSpec(safety=_ALLERGY, consent=False),
    "C48-stale": WorldSpec(**_only(_routine(15, "양치하기", "self_care", "partial_assist"))),
    "C48-othercat": WorldSpec(**_only(_routine(2, "숟가락 쓰기", "mealtime", "partial_assist"))),
    "C48-noassist": WorldSpec(**_only(_routine(2, "양치하기", "self_care"))),
    "C48-trigger": WorldSpec(
        **_only(_routine(2, "손톱 물어뜯기", "habit", polarity=-1, trigger="긴장할 때"))
    ),
    # 시드 교육 행은 72개월 미만까지다 — [예시]도 기록도 없다(plan1 의 doc.no_row)
    "C80": WorldSpec(months=80, **_EMPTY),
}


class RecordingDocs:
    """문서 포트를 감싸 코드가 만든 검색어를 남긴다 — 보호자 발화가 들어가면 안 된다(§12)."""

    def __init__(self, inner: GrowthDocReader) -> None:
        self._inner = inner
        self.queries: list[str] = []

    async def search(self, **kwargs: Any) -> list[GrowthDocRow]:
        self.queries.append(kwargs["query"])
        return await self._inner.search(**kwargs)

    async def chain(self, *, chain_key: str) -> list[GrowthDocRow]:
        return await self._inner.chain(chain_key=chain_key)


class WithAffinities:
    """운영 Memory store 의 관찰은 그대로 읽고, store 에 없는 profile_affinity 만 덧댄다."""

    def __init__(
        self, inner: GrowthMemoryReader, rows: Sequence[tuple[AffinityDomain, AffinityRow]]
    ) -> None:
        self._inner = inner
        self._rows = tuple(rows)

    async def affinities(
        self, *, child_id: UUID, domains: tuple[AffinityDomain, ...]
    ) -> list[AffinityRow]:
        return [row for domain, row in self._rows if domain in domains]

    async def education(self, **kwargs: Any) -> list[Any]:
        return await self._inner.education(**kwargs)

    async def routine(self, **kwargs: Any) -> list[Any]:
        return await self._inner.routine(**kwargs)

    async def activities(self, **kwargs: Any) -> list[Any]:
        return await self._inner.activities(**kwargs)


@dataclass(frozen=True)
class World:
    name: str
    spec: WorldSpec
    context: GrowthContext
    safety: InMemorySafety
    growth_log: InMemoryGrowthLog
    docs: RecordingDocs
    birth: date
    measurements: tuple[GrowthMeasurement, ...]

    @property
    def today(self) -> date:
        return self.context.today


def birth_date_for(months: int, today: date) -> date:
    """오늘 기준 `months` 개월 된 날. 말일이 모자라면 그 달 말일로 맞춘다(월령은 달력 계산)."""
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, min(today.day, calendar.monthrange(year, month + 1)[1]))


async def build_world(name: str, *, run_id: str = "live") -> World:
    spec = WORLDS[name]
    birth = birth_date_for(spec.months, TODAY)
    store = InMemoryStore(now=NOW)
    for domain, days_ago, fields in spec.observations:
        day = TODAY - timedelta(days=days_ago)
        await store.create_observation(
            domain=domain,
            child_id=CHILD,
            source_writer=PARENT,
            raw_text=_RAW,
            observed_on=day,
            observed_range=build_observed_range(day),
            fields=dict(fields),
        )
    measurements = _measurements(spec.measurements, birth=birth)
    safety = InMemorySafety(spec.safety, fail=spec.safety_fails)
    growth_log = InMemoryGrowthLog(measurements)
    contexts, _ = entrypoint._domain_contexts(
        child_id=CHILD,
        run_id=run_id,
        now=NOW,
        store=store,
        birth_date=birth,
        # 🚨 growth_safety 와 늘 같이 — 빠지면 동의 없음으로 읽혀 알레르기 대조 없이 교육이 열린다
        child_health_granted=spec.consent,
        location=None,
        activity_weather=None,
        domain_store=None,
        growth_safety=safety,
        growth_log=growth_log,
    )
    context = contexts["growth"]
    docs = RecordingDocs(context.ports.docs)
    memory: GrowthMemoryReader = context.ports.memory
    if spec.affinities:
        memory = WithAffinities(memory, _affinities(spec.affinities))
    context = replace(context, ports=replace(context.ports, docs=docs, memory=memory))
    if spec.hazard_fails:
        context = replace(context, safety_rules=_broken_rules)
    return World(name, spec, context, safety, growth_log, docs, birth, measurements)


def _broken_rules() -> SafetyRules:
    raise SafetyRulesUnavailable("위험 사전을 읽지 못했다 (eval 주입)")


def _affinities(
    pairs: Sequence[tuple[AffinityDomain, str]],
) -> tuple[tuple[AffinityDomain, AffinityRow], ...]:
    return tuple(
        (
            domain,
            AffinityRow(
                id=UUID(int=5000 + n),
                merge_key=key,
                state="confirmed",
                polarity=1,
                strength=0.9,
                last_observed_on=TODAY - timedelta(days=5),
            ),
        )
        for n, (domain, key) in enumerate(pairs, start=1)
    )


def _m(n: int, day: date, height: str | None, weight: str | None) -> GrowthMeasurement:
    return GrowthMeasurement(
        id=UUID(int=6000 + n),
        child_id=CHILD,
        measured_on=day,
        height_cm=Decimal(height) if height is not None else None,
        weight_kg=Decimal(weight) if weight is not None else None,
    )


def _measurements(kind: str, *, birth: date) -> tuple[GrowthMeasurement, ...]:
    """측정 모양 — test_expected §5 의 픽스처. 값은 지어낸 것이다."""
    recent = TODAY - timedelta(days=3)
    if kind == "none":
        return ()
    if kind == "one":  # C48-1m
        return (_m(1, recent, "103.6", "16.9"),)
    if kind == "two":  # C48 — 6개월 간격 2건
        return (_m(1, birth_date_for(6, TODAY), "100.0", "15.8"), _m(2, recent, "103.6", "16.9"))
    if kind == "five_weeks":  # 간격이 짧아도 그대로 나온다
        return (
            _m(1, TODAY - timedelta(days=38), "68.0", "7.9"),
            _m(2, recent, "70.1", "8.3"),
        )
    if kind == "six":  # 6건 전부 · 반올림 없음
        values = (("98.2", "15.0"), ("99.1", "15.3"), ("100.0", "15.8"))
        values += (("101.2", "16.1"), ("102.5", "16.4"), ("103.6", "16.9"))
        return tuple(
            _m(n, birth_date_for(2 * (6 - n), TODAY), height, weight)
            for n, (height, weight) in enumerate(values, start=1)
        )
    if kind == "oneside":  # 둘 다 · 몸무게만 · 키만
        return (
            _m(1, birth_date_for(4, TODAY), "100.8", "16.0"),
            _m(2, birth_date_for(2, TODAY), None, "16.4"),
            _m(3, recent, "103.1", None),
        )
    if kind == "onemetric":  # 키 2건 · 몸무게 1건
        return (_m(1, birth_date_for(4, TODAY), "100.8", "16.0"), _m(2, recent, "103.1", None))
    if kind == "posture":  # 20 · 23 · 26개월에 잰 키 — 24개월을 걸친다
        assert birth == birth_date_for(26, TODAY)
        return (
            _m(1, birth_date_for(6, TODAY), "82.0", "11.0"),
            _m(2, birth_date_for(3, TODAY), "84.6", "11.6"),
            _m(3, TODAY, "86.9", "12.1"),
        )
    raise ValueError(f"모르는 측정 모양: {kind}")
