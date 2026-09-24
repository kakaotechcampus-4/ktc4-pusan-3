"""CuratorStore 조회 조건 테스트. 인메모리 구현으로 계약을 고정한다.

DB 구현이 붙으면 같은 기대를 그쪽에도 걸어야 한다 — 여기 적힌 것이 계약이다.
    - 임베딩 대상과 연결 대상은 따로 잡힌다
    - active 만 본다 (stand_alone · inactive 는 Curator 집계 밖)
    - 저장된 순서로 돌려준다 (observed_on 순이 아니다)
    - 다른 아이 · 다른 도메인 · 다른 polarity 의 Profile 은 후보가 아니다
    - Profile 후보도 저장된 순서로 돌려준다 (동점이면 오래된 Profile 을 고르는 근거)
    - archived Profile 도 조회 후보다 (연결할지는 연결 로직이 정한다)

인메모리 계약만 본다. DB 의 날짜 정렬 · 벡터 타입 · 동시성은 DB 구현이 붙을 때 검증한다.
"""

from datetime import date
from uuid import UUID

from app.agents.curator.embedding.inmemory import InMemoryCuratorStore

CHILD = UUID("00000000-0000-0000-0000-000000000001")
OTHER_CHILD = UUID("00000000-0000-0000-0000-000000000002")
# 저장소는 차원을 검사하지 않는다. 차원은 Embedder 가 막는다
VEC = [0.1, 0.2, 0.3]
OTHER_VEC = [0.9, 0.8, 0.7]


def _ids(items: list) -> list[str]:
    return [item.id for item in items]


def _add_excluded(store: InMemoryCuratorStore, **fields: object) -> None:
    """어느 조회에도 잡히면 안 되는 관찰. fields 는 연결 대상 조건을 맞출 때 쓴다."""
    store.add_observation(
        child_id=CHILD, domain="food", id="once", subject="딸기", status="stand_alone", **fields
    )
    store.add_observation(
        child_id=CHILD, domain="food", id="wrong", subject="딸기", status="inactive", **fields
    )
    store.add_observation(child_id=OTHER_CHILD, domain="food", id="other", subject="딸기", **fields)


async def test_임베딩_대상은_active_이고_벡터가_없는_관찰이다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="new", subject="딸기")
    store.add_observation(child_id=CHILD, domain="food", id="done", subject="딸기", embedding=VEC)
    _add_excluded(store)

    assert _ids(await store.list_unembedded(child_id=CHILD)) == ["new"]


async def test_연결_대상은_active_이고_벡터는_있고_연결이_없는_관찰이다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="new", subject="딸기")
    store.add_observation(child_id=CHILD, domain="food", id="ready", subject="딸기", embedding=VEC)
    store.add_observation(
        child_id=CHILD,
        domain="food",
        id="linked",
        subject="딸기",
        embedding=VEC,
        affinity_id="profile_affinity-9",
    )
    # 벡터가 있고 연결이 없어도 active 가 아니거나 다른 아이면 빠진다
    _add_excluded(store, embedding=VEC)

    assert _ids(await store.list_unlinked(child_id=CHILD)) == ["ready"]


async def test_벡터를_저장하면_임베딩_대상에서_빠지고_연결_대상이_된다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="1", subject="딸기")
    store.add_observation(child_id=CHILD, domain="activity", id="1", subject="레고")

    await store.save_embeddings(vectors={("food", "1"): VEC})

    # 도메인이 다르면 id 가 같아도 다른 관찰이다
    assert [o.key for o in await store.list_unembedded(child_id=CHILD)] == [("activity", "1")]
    assert [o.key for o in await store.list_unlinked(child_id=CHILD)] == [("food", "1")]
    assert store.observation("food", "1").embedding == VEC  # 넘긴 벡터 그대로
    assert store.observation("activity", "1").embedding is None


async def test_관찰_날짜가_아니라_저장된_순서대로_돌려준다() -> None:
    store = InMemoryCuratorStore()
    # 나중에 저장된 것일수록 관찰 날짜가 이르다 ("지난주에 ~했어")
    for id, day in (("a", date(2026, 9, 20)), ("b", date(2026, 9, 10)), ("c", date(2026, 9, 1))):
        store.add_observation(child_id=CHILD, domain="food", id=id, subject=id, observed_on=day)

    assert _ids(await store.list_unembedded(child_id=CHILD)) == ["a", "b", "c"]

    await store.save_embeddings(vectors={("food", id): VEC for id in ("a", "b", "c")})
    assert _ids(await store.list_unlinked(child_id=CHILD)) == ["a", "b", "c"]


async def test_Profile_후보는_같은_아이_도메인_polarity_만이다() -> None:
    store = InMemoryCuratorStore()
    like = store.add_profile(
        child_id=CHILD, domain="food", merge_key="딸기", polarity=1, embedding=VEC
    )
    store.add_profile(child_id=CHILD, domain="food", merge_key="딸기", polarity=-1, embedding=VEC)
    store.add_profile(child_id=CHILD, domain="food", merge_key="딸기", polarity=0, embedding=VEC)
    store.add_profile(
        child_id=CHILD, domain="activity", merge_key="딸기", polarity=1, embedding=VEC
    )
    store.add_profile(
        child_id=OTHER_CHILD, domain="food", merge_key="딸기", polarity=1, embedding=VEC
    )

    found = await store.list_profiles(child_id=CHILD, domain="food", polarity=1)
    assert _ids(found) == [like.id]


async def test_Profile_후보는_저장된_순서대로_돌려준다() -> None:
    """연결 단계가 동점일 때 앞의 것(오래된 Profile)을 고르는 근거다."""
    store = InMemoryCuratorStore()
    ids = [
        store.add_profile(
            child_id=CHILD, domain="food", merge_key=key, polarity=1, embedding=VEC
        ).id
        for key in ("생딸기", "딸기", "딸기잼")
    ]

    assert _ids(await store.list_profiles(child_id=CHILD, domain="food", polarity=1)) == ids


async def test_중립_관찰은_중립_Profile_만_찾는다() -> None:
    store = InMemoryCuratorStore()
    neutral = store.add_profile(
        child_id=CHILD, domain="food", merge_key="딸기", polarity=0, embedding=VEC
    )
    # polarity 가 빈 Profile 은 "중립" 이 아니라 "방향을 모름" 이다
    for polarity in (None, 1, -1):
        store.add_profile(
            child_id=CHILD, domain="food", merge_key="딸기", polarity=polarity, embedding=VEC
        )

    found = await store.list_profiles(child_id=CHILD, domain="food", polarity=0)
    assert _ids(found) == [neutral.id]


async def test_archived_Profile_도_후보다() -> None:
    store = InMemoryCuratorStore()
    archived = store.add_profile(
        child_id=CHILD, domain="food", merge_key="딸기", polarity=1, embedding=VEC, state="archived"
    )

    found = await store.list_profiles(child_id=CHILD, domain="food", polarity=1)
    assert _ids(found) == [archived.id]


async def test_새_Profile_은_candidate_로_만들어지고_바로_후보가_된다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(
        child_id=CHILD, domain="food", id="1", subject="딸기", polarity=1, embedding=OTHER_VEC
    )

    created = await store.create_profile(
        child_id=CHILD,
        domain="food",
        polarity=1,
        merge_key="딸기",
        embedding=OTHER_VEC,
        last_observed_on=store.observation("food", "1").observed_on,
    )
    await store.link(domain="food", observation_id="1", affinity_id=created.id)

    assert created.state == "candidate"
    assert (created.domain, created.merge_key, created.polarity) == ("food", "딸기", 1)
    assert created.embedding == OTHER_VEC
    # 만든 즉시 후보로 잡혀야 순차 처리에서 다음 관찰이 이 Profile 을 찾는다.
    # 이것만으로 중복이 막히지는 않는다 — 연결 로직(S3)과 DB 동시성 처리가 함께 필요하다
    assert await store.list_profiles(child_id=CHILD, domain="food", polarity=1) == [created]
    assert store.observation("food", "1").affinity_id == created.id
    assert await store.list_unlinked(child_id=CHILD) == []
