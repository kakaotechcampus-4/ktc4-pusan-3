"""연결 단계 테스트. 인메모리 저장소와 2차원 벡터로 유사도를 직접 조절한다.

_at(deg) 는 각도 deg 의 단위 벡터다. 두 벡터의 코사인 유사도는 cos(각도 차) —
0° 차이 1.0, 60° 차이 0.5, 90° 차이 0.0.
"""

import math
from datetime import date
from uuid import UUID

import pytest

from app.agents.curator.embedding.inmemory import InMemoryCuratorStore
from app.agents.curator.embedding.link_step import LinkOutcome, link_pending
from app.agents.curator.embedding.ports import CuratorDomain, ProfileItem

CHILD = UUID("00000000-0000-0000-0000-000000000001")
THRESHOLD = 0.5


def _at(deg: float) -> list[float]:
    rad = math.radians(deg)
    return [math.cos(rad), math.sin(rad)]


def _observe(
    store: InMemoryCuratorStore,
    id: str,
    subject: str,
    vector: list[float] | None,
    *,
    domain: CuratorDomain = "food",
    polarity: int = 1,
    observed_on: date = date(2026, 9, 1),
) -> None:
    store.add_observation(
        child_id=CHILD,
        domain=domain,
        id=id,
        subject=subject,
        polarity=polarity,
        embedding=vector,
        observed_on=observed_on,
    )


def _profile(
    store: InMemoryCuratorStore,
    merge_key: str,
    vector: list[float] | None,
    *,
    domain: CuratorDomain = "food",
    polarity: int | None = 1,
    state: str = "candidate",
) -> ProfileItem:
    return store.add_profile(
        child_id=CHILD,
        domain=domain,
        merge_key=merge_key,
        polarity=polarity,
        embedding=vector,
        state=state,
    )


async def _link(store: InMemoryCuratorStore) -> tuple[LinkOutcome, ...]:
    return (await link_pending(store, child_id=CHILD, threshold=THRESHOLD)).outcomes


# 1. 이름이 같은 Profile


async def test_이름이_같으면_유사도와_관계없이_연결한다() -> None:
    store = InMemoryCuratorStore()
    strawberry = _profile(store, "딸기", _at(0))
    _observe(store, "1", " 딸기 ", _at(90))  # 벡터는 멀다 (유사도 0)

    [outcome] = await _link(store)

    assert (outcome.status, outcome.match, outcome.affinity_id) == (
        "linked",
        "exact",
        strawberry.id,
    )
    assert outcome.similarity is None
    assert store.observation("food", "1").affinity_id == strawberry.id


async def test_이름이_같은_Profile_이_여럿이면_오래된_것에_연결한다() -> None:
    store = InMemoryCuratorStore()
    older = _profile(store, "딸기", _at(0))
    _profile(store, "딸기", _at(0))
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert outcome.affinity_id == older.id


# 2. 유사도


async def test_임계값_이상이면_가장_가까운_Profile_에_연결한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "블루베리", _at(50))  # 유사도 cos(50°) ≈ 0.64
    near = _profile(store, "생딸기", _at(10))  # 유사도 cos(10°) ≈ 0.98
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert (outcome.status, outcome.match, outcome.affinity_id) == ("linked", "similar", near.id)
    assert outcome.similarity == pytest.approx(math.cos(math.radians(10)))


async def test_임계값과_같으면_연결한다() -> None:
    store = InMemoryCuratorStore()
    # [1, 0] 과 [1, 1] 의 유사도는 1/√2. 부동소수점 오차 없이 이 값과 정확히 같게 나온다
    # ([0.5, √3/2] 는 길이가 0.9999999999999999 라 0.5 보다 조금 커져 경계를 못 잡는다)
    edge = _profile(store, "생딸기", [1.0, 1.0])
    _observe(store, "1", "딸기", [1.0, 0.0])

    [outcome] = (await link_pending(store, child_id=CHILD, threshold=1 / math.sqrt(2))).outcomes

    assert (outcome.status, outcome.affinity_id) == ("linked", edge.id)


async def test_유사도가_같으면_오래된_Profile_에_연결한다() -> None:
    store = InMemoryCuratorStore()
    older = _profile(store, "생딸기", _at(20))
    _profile(store, "딸기잼", _at(-20))  # 반대쪽으로 같은 각도 → 같은 유사도
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert outcome.affinity_id == older.id


# 3. 새 candidate


async def test_임계값_미만이면_새_candidate_를_만든다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "레고", _at(80))  # 유사도 ≈ 0.17
    _observe(store, "1", " 딸기 ", _at(0), polarity=1, observed_on=date(2026, 9, 15))

    [outcome] = await _link(store)

    assert (outcome.status, outcome.match, outcome.created) == ("created", "new", True)
    created = next(p for p in store.profiles if p.id == outcome.affinity_id)
    assert (created.merge_key, created.polarity, created.state) == ("딸기", 1, "candidate")
    assert created.embedding == _at(0)  # 관찰 벡터를 그대로 쓴다
    assert store.observation("food", "1").affinity_id == created.id


async def test_후보가_없으면_새_candidate_를_만든다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert outcome.status == "created"
    assert len(store.profiles) == 1


# 같은 실행 안에서


async def test_같은_실행에서_같은_대상이_두_번_나오면_Profile_은_하나다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))
    _observe(store, "2", "딸기", _at(0))

    first, second = await _link(store)

    assert (first.status, second.status, second.match) == ("created", "linked", "exact")
    assert second.affinity_id == first.affinity_id
    assert len(store.profiles) == 1


async def test_같은_실행에서_비슷한_대상은_앞에서_만든_Profile_에_연결한다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))
    _observe(store, "2", "생딸기", _at(10))

    first, second = await _link(store)

    assert (second.status, second.match, second.affinity_id) == (
        "linked",
        "similar",
        first.affinity_id,
    )


async def test_먼저_저장된_관찰의_subject_가_merge_key_가_된다() -> None:
    store = InMemoryCuratorStore()
    # 관찰 날짜는 뒤지만 먼저 저장됐다
    _observe(store, "1", "생딸기", _at(10), observed_on=date(2026, 9, 20))
    _observe(store, "2", "딸기", _at(0), observed_on=date(2026, 9, 1))

    await _link(store)

    assert [p.merge_key for p in store.profiles] == ["생딸기"]


# 격리


async def test_polarity_가_다르면_연결하지_않는다() -> None:
    store = InMemoryCuratorStore()
    like = _profile(store, "딸기", _at(0), polarity=1)
    _observe(store, "1", "딸기", _at(0), polarity=-1)

    [outcome] = await _link(store)

    assert outcome.status == "created"
    assert outcome.affinity_id != like.id
    dislike = next(p for p in store.profiles if p.id == outcome.affinity_id)
    assert dislike.polarity == -1


async def test_도메인이_다르면_연결하지_않는다() -> None:
    store = InMemoryCuratorStore()
    food = _profile(store, "딸기", _at(0), domain="food")
    _observe(store, "1", "딸기", _at(0), domain="activity")

    [outcome] = await _link(store)

    assert outcome.status == "created"
    assert outcome.affinity_id != food.id


async def test_중립_관찰은_중립_Profile_에만_연결한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기", _at(0), polarity=None)  # 방향을 모름 — 중립이 아니다
    _profile(store, "딸기", _at(0), polarity=1)
    neutral = _profile(store, "딸기", _at(0), polarity=0)
    _observe(store, "1", "딸기", _at(0), polarity=0)

    [outcome] = await _link(store)

    assert outcome.affinity_id == neutral.id


# archived


async def test_archived_에도_연결하고_state_는_그대로_둔다() -> None:
    store = InMemoryCuratorStore()
    archived = _profile(store, "딸기", _at(0), state="archived")
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert outcome.affinity_id == archived.id
    assert store.profiles == [archived]  # state 를 포함해 Profile 이 바뀌지 않았다


# 잘못된 벡터 — 유사도 미달과 구분해 보류


@pytest.mark.parametrize(
    "vector",
    [[], [0.0, 0.0], [math.nan, 1.0], [math.inf, 1.0]],
    ids=["빈_벡터", "영벡터", "NaN", "무한대"],
)
async def test_관찰_벡터가_잘못되면_연결도_생성도_하지_않는다(vector: list[float]) -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기", _at(0))
    _observe(store, "1", "딸기", vector)  # 이름이 같아도 붙이지 않는다

    [outcome] = await _link(store)

    assert (outcome.status, outcome.reason) == ("held", "invalid_observation_embedding")
    assert outcome.affinity_id is None
    assert store.observation("food", "1").affinity_id is None
    assert len(store.profiles) == 1  # 새로 만들지 않았다


@pytest.mark.parametrize(
    "vector",
    [None, [0.0, 0.0], [math.nan, 1.0], [1.0, 0.0, 0.0]],
    ids=["없음", "영벡터", "NaN", "차원이_다름"],
)
async def test_후보_Profile_벡터가_잘못되면_보류한다(vector: list[float] | None) -> None:
    store = InMemoryCuratorStore()
    broken = _profile(store, "생딸기", vector)
    _profile(store, "레고", _at(80))
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert (outcome.status, outcome.reason) == ("held", "invalid_profile_embedding")
    assert outcome.invalid_profile_ids == (broken.id,)  # 복구할 Profile 을 알려 준다
    assert store.observation("food", "1").affinity_id is None
    assert len(store.profiles) == 2


async def test_잘못된_Profile_은_하나가_아니라_전부_알려_준다() -> None:
    store = InMemoryCuratorStore()
    first = _profile(store, "생딸기", None)
    _profile(store, "레고", _at(80))
    second = _profile(store, "딸기잼", [0.0, 0.0])
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert outcome.invalid_profile_ids == (first.id, second.id)


async def test_연결되거나_관찰_벡터로_보류되면_잘못된_Profile_목록은_비어_있다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))
    _observe(store, "2", "레고", [0.0, 0.0])

    created, held = await _link(store)

    assert created.invalid_profile_ids == ()
    assert (held.reason, held.invalid_profile_ids) == ("invalid_observation_embedding", ())


async def test_후보_벡터가_잘못돼도_이름이_같은_Profile_이_있으면_연결한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "생딸기", None)
    strawberry = _profile(store, "딸기", _at(0))
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store)

    assert (outcome.status, outcome.affinity_id) == ("linked", strawberry.id)


async def test_보류한_관찰은_다음_실행에서_다시_시도한다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", [0.0, 0.0])
    [held] = await _link(store)
    assert held.status == "held"

    await store.save_embeddings(vectors={("food", "1"): _at(0)})  # 원인이 고쳐졌다
    [retried] = await _link(store)

    assert retried.status == "created"


# 임계값 입력


@pytest.mark.parametrize(
    "threshold",
    [math.nan, math.inf, -math.inf, 1.01, -1.01],
    ids=["NaN", "무한대", "음의_무한대", "1_초과", "-1_미만"],
)
async def test_잘못된_임계값은_아무것도_하기_전에_거절한다(threshold: float) -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))

    with pytest.raises(ValueError, match="threshold"):
        await link_pending(store, child_id=CHILD, threshold=threshold)

    # NaN 이 통과하면 모든 비교가 거짓이라 새 candidate 가 생겼을 것이다
    assert store.profiles == []
    assert store.observation("food", "1").affinity_id is None


@pytest.mark.parametrize("threshold", [-1.0, 1.0])
async def test_코사인_범위의_끝값은_받는다(threshold: float) -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))

    [outcome] = (await link_pending(store, child_id=CHILD, threshold=threshold)).outcomes

    assert outcome.status == "created"


# 대상


async def test_이미_연결된_관찰은_다시_연결하지_않는다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기", _at(0))
    await _link(store)

    assert await _link(store) == ()
