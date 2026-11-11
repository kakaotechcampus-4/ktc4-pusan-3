"""P 의 가짜 세계 — 케이스의 World 를 진입점에 넘길 in-memory 포트로 바꾼다.

운영 입구(`entrypoint.handle_input`)를 그대로 부르고 DB 에 닿는 포트만 in-memory 로 바꾼다
(Health · Growth supervisor e2e 와 같은 방식). 기억 읽기는 감싸서 "이 케이스에서 읽힌 id" 를
모은다 — I3(인용 ⊆ 검색)을 제품의 EVIDENCE_REQUIRED 와 따로 판정하려고.

진입점은 `datetime.now(KST)` 를 쓰므로 세계도 실행 시각 기준으로 심는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.agents.activity.store.inmemory import InMemorySafety as ActivitySafety
from app.agents.activity.store.inmemory import InMemoryWeather
from app.agents.activity.store.ports import CoarseLocation, RawForecast
from app.agents.activity.store.ports import SafetyEntry as ActivitySafetyEntry
from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.evidence import AffinityRow
from app.agents.food.store.inmemory import InMemoryRecipeCatalog
from app.agents.food.store.inmemory import InMemorySafety as FoodSafety
from app.agents.food.store.ports import SafetyEntry as FoodSafetyEntry
from app.agents.growth.store.inmemory import InMemorySafety as GrowthSafety
from app.agents.growth.store.ports import SafetyEntry as GrowthSafetyEntry
from app.agents.health.store.inmemory import InMemoryHealthSafety, InMemoryMedicationStore
from app.agents.health.store.ports import HealthSafetyEntry
from app.agents.memory.store import InMemoryStore

# 제품이 InMemory id 를 인용 id 로 바꾸는 방식 그대로(_citation_id) — 바뀌면 여기서 바로
# 깨지게 복사하지 않고 가져온다
from app.agents.memory_bridge import _uuid as _citation_id

# "쌓인 기록 N건" 을 셀 관찰 종류 — 운영 러너(agent_ports.record_counter)와 같은 표
from app.api.agent_ports import RECORD_KINDS
from tests.eval.agents.food.world import birth_date_for
from tests.eval.agents.food.world import build_world as build_food_world
from tests.eval.suite.schema import World
from tests.eval.suite.trace import SavedRow

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID("00000000-0000-7000-8000-0000000000e1")  # 지어낸 id
PARENT = UUID("00000000-0000-7000-8000-0000000000e2")
STALE_DAYS = 183  # 루트 §2 "6개월 이상 지난 관심 기록" — 케이스는 200일로 경계에서 멀리 둔다
OBSERVATION_DOMAINS = ("food", "health", "education", "activity", "routine")
_SEED_TEXT = "심어 둔 기록"  # 활동 · 대상 칸이 없을 때만. 원문 칸은 판정하지 않는다
_BUSAN = CoarseLocation(lat=35.18, lon=129.08)
_HEAVY_RAIN = RawForecast(sky="4", pcp="30.0~50.0mm", pop="90")


class RecordingStore:
    """MemoryStore 를 감싸 읽기 결과 id 를 모은다. 나머지 메서드는 그대로 넘긴다."""

    def __init__(self, inner: InMemoryStore) -> None:
        self.inner = inner
        self.seen: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def query_observations(self, **kwargs: Any) -> list[Any]:
        rows = await self.inner.query_observations(**kwargs)
        self.seen.update(str(_citation_id(row.id)) for row in rows)
        return rows

    async def get_observation(self, **kwargs: Any) -> Any:
        row = await self.inner.get_observation(**kwargs)
        if row is not None:
            self.seen.add(str(_citation_id(row.id)))
        return row


class RecordingAffinity:
    """`AffinityReader` — 심은 관심을 도메인으로 걸러 돌려주고, 돌려준 id 를 모은다."""

    def __init__(self, rows: Sequence[tuple[str, AffinityRow]]) -> None:
        self._rows = list(rows)
        self.seen: set[str] = set()

    async def affinities(self, *, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]:
        found = [row for domain, row in self._rows if domain in domains]
        self.seen.update(str(row.id) for row in found)
        return found


def record_counter(store: InMemoryStore) -> Any:
    """`CountRecords` — 운영의 `agent_ports.record_counter` 를 InMemory 로. 그 Agent 의 관찰 종류에
    남아 있는 아이 관찰 수다. 기록기(RecordingStore)를 거치지 않는다 — 센 것은 읽은 근거가
    아니다(I3)."""

    async def count_records(agent: str) -> int:
        total = 0
        for kind in RECORD_KINDS.get(agent, ()):
            domain = kind.removeprefix("observation_")
            total += len(await store.query_observations(domain=domain, child_id=CHILD))
        return total

    return count_records


@dataclass
class ProbeWorld:
    store: RecordingStore
    affinity: RecordingAffinity
    now: datetime
    stale_ids: frozenset[str]
    ports: dict[str, Any]  # entrypoint.handle_input 에 그대로 넘길 키워드

    @property
    def searched(self) -> frozenset[str]:
        return frozenset(self.store.seen | self.affinity.seen)


def _seed_text(fields: Mapping[str, object]) -> str:
    """심은 관찰의 원문 — 보호자가 한 말처럼 활동 · 대상 이름을 담는다. 원문으로 찾는 조회
    (`raw_text_query`)가 실제 DB 에서처럼 찾게 한다 (P-REC-08, 11-10 기준선)."""
    name = fields.get("activity") or fields.get("subject")
    return f"{name} 했어" if name else _SEED_TEXT


async def build_probe_world(world: World, *, now: datetime) -> ProbeWorld:
    today = now.astimezone(KST).date()
    inner = InMemoryStore(now=now)
    stale: set[str] = set()
    for seed in world.observations:
        day = today - timedelta(days=seed.days_ago)
        row = await inner.create_observation(
            domain=seed.domain,
            child_id=CHILD,
            source_writer=PARENT,
            raw_text=_seed_text(seed.fields),
            observed_on=day,
            observed_range=build_observed_range(day),
            fields=dict(seed.fields),
        )
        if seed.days_ago >= STALE_DAYS:
            stale.add(str(_citation_id(row.id)))
    affinity_rows: list[tuple[str, AffinityRow]] = []
    for seed in world.affinities:
        row = AffinityRow(
            id=uuid4(),
            merge_key=seed.merge_key,
            state=seed.state,
            polarity=seed.polarity,
            strength=seed.strength,
            last_observed_on=today - timedelta(days=seed.last_days_ago),
            observed_days=seed.observed_days,
        )
        affinity_rows.append((seed.domain, row))
        if seed.last_days_ago >= STALE_DAYS:
            stale.add(str(row.id))
    store = RecordingStore(inner)
    affinity = RecordingAffinity(affinity_rows)
    # 레시피 풀은 Food eval 세계의 것을 빌린다 — 이유기면 이유식 풀
    food_world = build_food_world("C8" if world.months < 12 else "C48", now=now)
    medication = InMemoryMedicationStore()
    allergies = world.allergies
    ports: dict[str, Any] = {
        "store": store,
        "affinity_reader": affinity,
        "birth_date": birth_date_for(world.months, today),
        "child_health_granted": world.consent,
        "food_recipes": InMemoryRecipeCatalog(list(food_world.recipes)),
        "food_safety": FoodSafety(
            [FoodSafetyEntry(kind="allergy", label=a, status="active") for a in allergies]
        ),
        "activity_safety": ActivitySafety(
            [ActivitySafetyEntry(kind="allergy", label=a, status="active") for a in allergies]
        ),
        "growth_safety": GrowthSafety(
            [GrowthSafetyEntry(kind="allergy", label=a, status="active") for a in allergies]
        ),
        "health_safety": InMemoryHealthSafety(
            [HealthSafetyEntry(kind="allergy", label=a, status="active") for a in allergies]
        ),
        "health_medication": medication,
        "health_medication_writer": medication,
        # 일반 추천 블록의 "쌓인 기록 N건" (§2). 안 넘기면 record_count 가 늘 None 이라 I2 의
        # 건수 절반을 P 에서 판정할 수 없다 (리뷰 11-08)
        "count_records": record_counter(inner),
    }
    if world.rain:
        ports["location"] = _BUSAN
        ports["activity_weather"] = InMemoryWeather(forecast=_HEAVY_RAIN, uv="4")
    return ProbeWorld(
        store=store, affinity=affinity, now=now, stale_ids=frozenset(stale), ports=ports
    )


async def observation_rows(store: InMemoryStore) -> dict[str, SavedRow]:
    """아이 관찰 전부 (id → SavedRow). 하네스가 읽는 것이라 기록기를 거치지 않는다."""
    rows: dict[str, SavedRow] = {}
    for domain in OBSERVATION_DOMAINS:
        for row in await store.query_observations(domain=domain, child_id=CHILD):
            rows[row.id] = SavedRow(domain=domain, id=row.id, observed_on=row.observed_on)
    return rows


async def stored_event_count(store: InMemoryStore) -> int:
    return len(await store.query_events(child_id=CHILD))
