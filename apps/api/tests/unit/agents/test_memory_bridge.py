"""memory_bridge 검증. 같은 run 의 Memory store 를 Food · Activity 기억 포트로 읽는다."""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.common.datetime_rules import build_observed_range
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import ObservationRow
from app.agents.memory_bridge import StoreActivityMemory, StoreFoodMemory

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)
TODAY = NOW.date()
CHILD = UUID(int=1)
PARENT = UUID(int=2)
OTHER = UUID(int=3)


async def _save(
    store: InMemoryStore,
    domain: str,
    *,
    child_id: UUID = CHILD,
    day: date = TODAY,
    **fields: Any,
) -> ObservationRow:
    return await store.create_observation(
        domain=domain,
        child_id=child_id,
        source_writer=PARENT,
        raw_text=f"{fields['subject']} 기록",
        observed_on=day,
        observed_range=build_observed_range(day),
        fields=fields,
    )


async def test_Food_포트는_저장한_food_관찰을_FoodObservation_으로_읽는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기", amount="반 개", polarity=1)
    reader = StoreFoodMemory(store)

    rows = await reader.observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    again = await reader.observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    assert len(rows) == 1
    row = rows[0]
    assert (row.child_id, row.observed_on, row.subject) == (CHILD, TODAY, "딸기")
    assert (row.amount_text, row.polarity, row.updated_at) == ("반 개", 1, NOW)
    # 근거로 인용한 id 가 다음 조회와 맞아야 한다
    assert again[0].id == row.id


async def test_Food_포트는_관찰의_출처를_같이_넘긴다() -> None:
    # 기관 식사가 daycare_meal 과 겹치는지는 출처로 가른다(영양소_계산_설계 N-10)
    store = InMemoryStore(now=NOW)
    notice = UUID(int=50)
    await _save(store, "food", subject="카레", confidence_source="parent_hearsay")
    await _save(
        store,
        "food",
        subject="미역국",
        confidence_source="institution_notice",
        source_notice_id=notice,
    )
    await _save(store, "food", subject="딸기")

    rows = await StoreFoodMemory(store).observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    by_subject = {row.subject: (row.confidence_source, row.source_notice_id) for row in rows}
    assert by_subject == {
        "카레": ("parent_hearsay", None),
        "미역국": ("institution_notice", notice),
        "딸기": (None, None),
    }


async def test_Food_는_stand_alone_까지_읽고_inactive_deleted_는_읽지_않는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기")
    once = await _save(store, "food", subject="사과")
    wrong = await _save(store, "food", subject="배")
    gone = await _save(store, "food", subject="귤")
    # once_only("이번만 그랬어요") 는 검색에 남고, wrong("잘못된 기록") 은 빠진다
    await store.update_observation(
        domain="food", observation_id=once.id, fields={"status": "stand_alone"}
    )
    await store.update_observation(
        domain="food", observation_id=wrong.id, fields={"status": "inactive"}
    )
    await store.delete_observation(domain="food", observation_id=gone.id)

    rows = await StoreFoodMemory(store).observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    assert sorted(row.subject for row in rows) == ["딸기", "사과"]


async def test_Activity_는_active_만_읽는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "activity", subject="레고", activity="레고로 성 만들기", polarity=1)
    once = await _save(store, "activity", subject="수영장", activity="수영장 놀이")
    await store.update_observation(
        domain="activity", observation_id=once.id, fields={"status": "stand_alone"}
    )

    rows = await StoreActivityMemory(store).observations(
        child_id=CHILD, date_from=TODAY, date_to=TODAY
    )

    assert [(row.subject, row.activity, row.polarity) for row in rows] == [
        ("레고", "레고로 성 만들기", 1)
    ]


async def test_다른_아이_다른_도메인_구간_밖은_읽지_않는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기")
    await _save(store, "food", subject="사과", child_id=OTHER)
    await _save(store, "activity", subject="레고", activity="레고")
    await _save(store, "food", subject="배", day=date(2026, 9, 1))

    rows = await StoreFoodMemory(store).observations(
        child_id=CHILD, date_from=date(2026, 9, 5), date_to=TODAY
    )

    assert [row.subject for row in rows] == ["딸기"]


async def test_profile_affinity_는_Memory_store_에_없어_빈_목록이다() -> None:
    store = InMemoryStore(now=NOW)

    assert await StoreFoodMemory(store).affinities(child_id=CHILD) == []
    assert await StoreActivityMemory(store).affinities(child_id=CHILD) == []


class _NoStatusStore:
    """status 를 fields 에 싣지 않는 store. 어댑터가 빠뜨려도 근거로 새면 안 된다."""

    async def query_observations(self, **_: Any) -> list[ObservationRow]:
        return [
            ObservationRow(
                id="x-1",
                domain="food",
                raw_text="배 기록",
                created_at=NOW,
                observed_on=TODAY,
                fields={"subject": "배"},
            )
        ]


async def test_status_가_없는_관찰은_읽지_않는다() -> None:
    # 모르는 상태를 active 로 보면 inactive("잘못된 기록") 가 근거로 돌아올 수 있다
    rows = await StoreFoodMemory(_NoStatusStore()).observations(  # type: ignore[arg-type]
        child_id=CHILD, date_from=TODAY, date_to=TODAY
    )

    assert rows == []
