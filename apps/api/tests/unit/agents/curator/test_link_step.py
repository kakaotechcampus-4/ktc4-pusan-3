"""연결 단계 테스트. 인메모리 저장소와 가짜 판정기로 돈다.

판정기의 판단이 맞는지는 여기서 보지 않는다 (실험 3 · 4 의 몫). 여기서는 판정기 앞뒤의
코드가 약속을 지키는지 본다.
    - 이름이 같거나 후보가 없으면 판정기를 부르지 않는다
    - 판정기에는 같은 아이 · 도메인 · polarity 의 merge_key 만 보여 준다
    - 판정기 답이 후보 목록에 있을 때만 연결한다. 없는 값 · uncertain · 실패는 보류
    - none 이면 가까운 후보에 방향을 바꿔 묻는다. 그 호출이 실패하면 새로 만들지 않고 보류
    - uncertain 으로 UNCERTAIN_LIMIT 번 보류되면 새로 만든다. 오류 · 목록 밖의 답은 세지 않는다
    - Profile 의 state 는 바꾸지 않는다
"""

import math
from collections.abc import Callable, Sequence
from datetime import date
from uuid import UUID

import pytest

from app.agents.common.llm_client import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMUnavailableError,
)
from app.agents.curator.embedding import link_step
from app.agents.curator.embedding.inmemory import InMemoryCuratorStore
from app.agents.curator.embedding.judge import NONE, UNCERTAIN, JudgeAnswer, JudgeQuotaError
from app.agents.curator.embedding.link_step import LinkOutcome, link_pending
from app.agents.curator.embedding.ports import CuratorDomain, ProfileItem

CHILD = UUID("00000000-0000-0000-0000-000000000001")
OTHER_CHILD = UUID("00000000-0000-0000-0000-000000000002")
MODEL = "typesafe/jev-1.13-20260917"


def _at(deg: float) -> list[float]:
    rad = math.radians(deg)
    return [math.cos(rad), math.sin(rad)]


class FakeJudge:
    """answer(subject, candidates) 가 돌려준 값을 답한다. 받은 질문을 기록한다.

    error 를 주면 실패한다. fail_on 을 함께 주면 그 subject 를 물을 때만 실패한다.
    """

    def __init__(
        self,
        answer: Callable[[str, list[str]], str] | None = None,
        *,
        error: Exception | None = None,
        fail_on: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, list[str]]] = []
        self._answer = answer or (lambda subject, candidates: NONE)
        self._error = error
        self._fail_on = fail_on

    async def judge(
        self, *, subject: str, domain: CuratorDomain, candidates: Sequence[str]
    ) -> JudgeAnswer:
        self.calls.append((subject, domain, list(candidates)))
        if self._error is not None and self._fail_on in (None, subject):
            raise self._error
        return JudgeAnswer(self._answer(subject, list(candidates)), model=MODEL, confidence=0.9)


def picks(choice: str) -> FakeJudge:
    return FakeJudge(lambda subject, candidates: choice)


def _observe(
    store: InMemoryCuratorStore,
    id: str,
    subject: str,
    vector: list[float] | None = None,
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
        embedding=_at(0) if vector is None else vector,
        observed_on=observed_on,
    )


def _profile(
    store: InMemoryCuratorStore,
    merge_key: str,
    vector: list[float] | None = None,
    *,
    child_id: UUID = CHILD,
    domain: CuratorDomain = "food",
    polarity: int | None = 1,
    state: str = "candidate",
) -> ProfileItem:
    return store.add_profile(
        child_id=child_id,
        domain=domain,
        merge_key=merge_key,
        polarity=polarity,
        embedding=_at(0) if vector is None else vector,
        state=state,
    )


async def _link(store: InMemoryCuratorStore, judge: FakeJudge | None) -> tuple[LinkOutcome, ...]:
    return (await link_pending(store, judge, child_id=CHILD)).outcomes


# 판정기를 부르지 않는 경우


async def test_이름이_같으면_판정기_없이_연결한다() -> None:
    store = InMemoryCuratorStore()
    strawberry = _profile(store, "딸기")
    _observe(store, "1", " 딸기 ")

    [outcome] = await _link(store, None)

    assert (outcome.status, outcome.match, outcome.affinity_id) == (
        "linked",
        "exact",
        strawberry.id,
    )
    assert outcome.judge_model is None


async def test_띄어쓰기만_다르면_이름이_같은_것으로_본다() -> None:
    store = InMemoryCuratorStore()
    tomato = _profile(store, "방울토마토")
    judge = FakeJudge()
    _observe(store, "1", "방울 토마토")

    [outcome] = await _link(store, judge)

    assert (outcome.match, outcome.affinity_id) == ("exact", tomato.id)
    assert judge.calls == []


async def test_이름이_같은_Profile_이_여럿이면_오래된_것에_연결한다() -> None:
    store = InMemoryCuratorStore()
    older = _profile(store, "딸기")
    _profile(store, "딸기")
    _observe(store, "1", "딸기")

    [outcome] = await _link(store, None)

    assert outcome.affinity_id == older.id


async def test_후보가_없으면_판정기_없이_새_candidate_를_만든다() -> None:
    store = InMemoryCuratorStore()
    judge = FakeJudge()
    _observe(store, "1", " 방울 토마토 ", _at(30), polarity=1, observed_on=date(2026, 9, 15))

    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.match, outcome.created) == ("created", "new", True)
    assert judge.calls == []
    [created] = store.profiles
    assert (created.merge_key, created.polarity, created.state) == ("방울 토마토", 1, "candidate")
    assert created.embedding == _at(30)  # 관찰 벡터를 그대로 쓴다
    assert store.observation("food", "1").affinity_id == created.id


# 판정기


async def test_판정기에는_subject_와_후보_merge_key_만_보여_준다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _profile(store, "블루베리")
    judge = FakeJudge()
    _observe(store, "1", " 생딸기 ")

    await _link(store, judge)

    assert judge.calls[0] == ("생딸기", "food", ["딸기", "블루베리"])  # 저장된 순서


async def test_판정기가_고른_Profile_에_연결한다() -> None:
    store = InMemoryCuratorStore()
    strawberry = _profile(store, "딸기")
    _profile(store, "블루베리")
    _observe(store, "1", "생딸기")

    [outcome] = await _link(store, picks("딸기"))

    assert (outcome.status, outcome.match, outcome.affinity_id) == (
        "linked",
        "judged",
        strawberry.id,
    )
    assert (outcome.judge_model, outcome.confidence) == (MODEL, 0.9)
    assert store.observation("food", "1").affinity_id == strawberry.id


async def test_판정기가_none_이면_새_candidate_를_만든다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "블루베리")
    _observe(store, "1", "딸기")

    [outcome] = await _link(store, picks(NONE))

    assert (outcome.status, outcome.match) == ("created", "new")
    assert outcome.judge_model == MODEL  # 판정기가 "같은 후보 없음"이라 답해서 만들었다
    assert [p.merge_key for p in store.profiles] == ["블루베리", "딸기"]


async def test_판정기가_uncertain_이면_보류한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "그거")

    [outcome] = await _link(store, picks(UNCERTAIN))

    assert (outcome.status, outcome.reason, outcome.judge_model) == (
        "held",
        "judge_uncertain",
        MODEL,
    )
    assert outcome.affinity_id is None
    assert len(store.profiles) == 1
    assert store.observation("food", "1").affinity_id is None


async def test_후보에_없는_값을_답하면_보류한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "생딸기")

    [outcome] = await _link(store, picks("딸기잼"))

    assert (outcome.status, outcome.reason) == ("held", "judge_invalid")
    assert len(store.profiles) == 1
    assert store.observation("food", "1").affinity_id is None


async def test_판정기_실패는_보류하고_다음_실행에서_다시_시도한다() -> None:
    store = InMemoryCuratorStore()
    strawberry = _profile(store, "딸기")
    _observe(store, "1", "생딸기")

    [failed] = await _link(store, FakeJudge(error=LLMUnavailableError("down")))
    assert (failed.status, failed.reason) == ("held", "judge_failed")
    assert store.observation("food", "1").affinity_id is None

    [retried] = await _link(store, picks("딸기"))
    assert (retried.status, retried.affinity_id) == ("linked", strawberry.id)


async def test_LLMError_가_아닌_예외는_그대로_올라온다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "생딸기")

    with pytest.raises(ValueError):
        await _link(store, FakeJudge(error=ValueError("bug")))


async def test_판정기가_없으면_후보가_있는_관찰만_보류한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "생딸기")  # 후보 있음 → 보류
    _observe(store, "2", "레고", domain="activity")  # 후보 없음 → 생성

    held, created = await _link(store, None)

    assert (held.status, held.reason) == ("held", "judge_unavailable")
    assert created.status == "created"
    assert store.observation("food", "1").affinity_id is None


async def test_같은_merge_key_후보는_한_번만_보여_주고_오래된_것에_연결한다() -> None:
    store = InMemoryCuratorStore()
    older = _profile(store, "생딸기")
    _profile(store, "생딸기")
    judge = picks("생딸기")
    _observe(store, "1", "딸기")

    [outcome] = await _link(store, judge)

    assert judge.calls[0][2] == ["생딸기"]
    assert outcome.affinity_id == older.id


# 격리


async def test_후보는_같은_아이_도메인_polarity_만이다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기", polarity=1)
    _profile(store, "생딸기주스", polarity=-1)
    _profile(store, "생딸기잼", domain="activity")
    _profile(store, "생딸기퓨레", child_id=OTHER_CHILD)
    judge = FakeJudge()
    _observe(store, "1", "생딸기", polarity=1)

    await _link(store, judge)

    assert judge.calls[0][2] == ["딸기"]


async def test_polarity_가_다르면_후보가_없어_새로_만든다() -> None:
    store = InMemoryCuratorStore()
    like = _profile(store, "딸기", polarity=1)
    judge = FakeJudge()
    _observe(store, "1", "딸기", polarity=-1)

    [outcome] = await _link(store, judge)

    assert outcome.status == "created"
    assert outcome.affinity_id != like.id
    assert judge.calls == []


async def test_중립_관찰은_중립_Profile_만_후보다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기1", polarity=None)  # 방향을 모름 — 중립이 아니다
    _profile(store, "딸기2", polarity=1)
    _profile(store, "딸기3", polarity=0)
    judge = FakeJudge()
    _observe(store, "1", "생딸기", polarity=0)

    await _link(store, judge)

    assert judge.calls[0][2] == ["딸기3"]


async def test_archived_도_후보이고_연결해도_state_는_그대로다() -> None:
    store = InMemoryCuratorStore()
    archived = _profile(store, "딸기", state="archived")
    _observe(store, "1", "생딸기")

    [outcome] = await _link(store, picks("딸기"))

    assert outcome.affinity_id == archived.id
    assert store.profiles == [archived]  # state 를 포함해 Profile 이 바뀌지 않았다


# 같은 실행 안에서


async def test_같은_실행에서_앞에서_만든_Profile_이_다음_관찰의_후보가_된다() -> None:
    store = InMemoryCuratorStore()
    judge = FakeJudge(lambda subject, candidates: "딸기" if "딸기" in candidates else NONE)
    _observe(store, "1", "딸기")
    _observe(store, "2", "생딸기")

    first, second = await _link(store, judge)

    assert (first.status, second.status, second.match) == ("created", "linked", "judged")
    assert second.affinity_id == first.affinity_id
    assert judge.calls == [("생딸기", "food", ["딸기"])]  # 첫 관찰은 후보가 없어 부르지 않았다


async def test_같은_실행에서_같은_이름이_두_번_나오면_Profile_은_하나다() -> None:
    store = InMemoryCuratorStore()
    judge = FakeJudge()
    _observe(store, "1", "딸기")
    _observe(store, "2", "딸기")

    first, second = await _link(store, judge)

    assert (first.status, second.match) == ("created", "exact")
    assert len(store.profiles) == 1
    assert judge.calls == []


async def test_먼저_저장된_관찰의_subject_가_merge_key_가_된다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "생딸기", observed_on=date(2026, 9, 20))  # 관찰 날짜는 뒤지만 먼저 저장
    _observe(store, "2", "딸기", observed_on=date(2026, 9, 1))

    await _link(store, picks("생딸기"))

    assert [p.merge_key for p in store.profiles] == ["생딸기"]


async def test_이미_연결된_관찰은_다시_연결하지_않는다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    await _link(store, None)

    assert await _link(store, None) == ()


# 벡터 — 새 Profile 에 복사하거나 후보를 추릴 때만 쓴다


@pytest.mark.parametrize(
    "vector",
    [[], [0.0, 0.0], [math.nan, 1.0], [math.inf, 1.0]],
    ids=["빈_벡터", "영벡터", "NaN", "무한대"],
)
async def test_관찰_벡터가_잘못되면_생성도_판정도_하지_않는다(vector: list[float]) -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    judge = FakeJudge()
    _observe(store, "1", "생딸기", vector)
    _observe(store, "2", "레고", vector, domain="activity")  # 후보가 없어도 만들지 않는다

    held_judge, held_create = await _link(store, judge)

    assert {held_judge.reason, held_create.reason} == {"invalid_observation_embedding"}
    assert judge.calls == []
    assert len(store.profiles) == 1


async def test_관찰_벡터가_잘못돼도_이름이_같으면_연결한다() -> None:
    store = InMemoryCuratorStore()
    strawberry = _profile(store, "딸기")
    _observe(store, "1", "딸기", [0.0, 0.0])

    [outcome] = await _link(store, None)

    assert (outcome.match, outcome.affinity_id) == ("exact", strawberry.id)


async def test_후보가_적으면_Profile_벡터를_보지_않는다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기", [0.0, 0.0])  # 잘못된 벡터지만 추리기를 하지 않으니 상관없다
    _observe(store, "1", "생딸기")

    [outcome] = await _link(store, picks("딸기"))

    assert outcome.status == "linked"


async def test_후보가_많으면_가까운_순서로_추려서_보여_준다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_step, "MAX_CANDIDATES", 2)
    store = InMemoryCuratorStore()
    _profile(store, "레고", _at(80))
    _profile(store, "블루베리", _at(40))
    _profile(store, "딸기", _at(10))
    judge = FakeJudge()
    _observe(store, "1", "생딸기", _at(0))

    await _link(store, judge)

    assert judge.calls[0][2] == ["딸기", "블루베리"]  # 가까운 순서


async def test_후보를_추릴_때_Profile_벡터가_잘못되면_보류한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_step, "MAX_CANDIDATES", 1)
    store = InMemoryCuratorStore()
    broken = _profile(store, "블루베리", [0.0, 0.0])
    _profile(store, "딸기", _at(10))
    judge = FakeJudge()
    _observe(store, "1", "생딸기", _at(0))

    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.reason) == ("held", "invalid_profile_embedding")
    assert outcome.invalid_profile_ids == (broken.id,)
    assert judge.calls == []


# 리뷰 반영 — 계정 오류 · 공백만 다른 답 · 예약어 후보 · 추릴 때 오래된 Profile


@pytest.mark.parametrize(
    "error",
    [LLMAuthError("401"), JudgeQuotaError("402"), LLMConfigError("key")],
    ids=["인증", "잔액", "설정"],
)
async def test_계정_오류가_나면_이번_실행의_남은_관찰은_판정기를_부르지_않는다(
    error: Exception,
) -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    judge = FakeJudge(error=error)
    _observe(store, "1", "생딸기")
    _observe(store, "2", "딸기잼")

    first, second = await _link(store, judge)

    assert len(judge.calls) == 1
    name = type(error).__name__
    assert (first.reason, first.error) == ("judge_failed", name)
    assert (second.reason, second.error) == ("judge_failed", name)


async def test_계정_오류로_멈춰도_다음_실행에서는_다시_부른다() -> None:
    store = InMemoryCuratorStore()
    strawberry = _profile(store, "딸기")
    _observe(store, "1", "생딸기")
    await _link(store, FakeJudge(error=LLMAuthError("401")))

    [retried] = await _link(store, picks("딸기"))

    assert retried.affinity_id == strawberry.id


@pytest.mark.parametrize(
    "error", [LLMUnavailableError("down"), LLMBadRequestError("400")], ids=["일시장애", "요청거절"]
)
async def test_계정_오류가_아니면_다음_관찰의_판정을_막지_않는다(error: Exception) -> None:
    """요청 거절은 그 관찰의 입력 때문일 수 있다 — 다른 관찰은 판정한다."""
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    judge = FakeJudge(error=error)
    _observe(store, "1", "생딸기")
    _observe(store, "2", "딸기잼")

    first, second = await _link(store, judge)

    assert len(judge.calls) == 2
    assert first.error == second.error == type(error).__name__


async def test_답이_후보와_공백만_다르면_그_후보에_연결한다() -> None:
    store = InMemoryCuratorStore()
    tomato = _profile(store, "방울 토마토")
    _observe(store, "1", "토마토")

    [outcome] = await _link(store, picks("방울토마토"))

    assert (outcome.status, outcome.match, outcome.affinity_id) == ("linked", "judged", tomato.id)


async def test_공백만_다른_후보가_둘이면_추측하지_않고_보류한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "방울 토마토")
    _profile(store, "방울토 마토")
    _observe(store, "1", "토마토")

    [outcome] = await _link(store, picks("방울토마토"))

    assert (outcome.status, outcome.reason) == ("held", "judge_invalid")


@pytest.mark.parametrize("merge_key", [NONE, UNCERTAIN, "  "], ids=["none", "uncertain", "빈_이름"])
async def test_후보_이름이_예약어거나_비었으면_판정기를_부르지_않고_보류한다(
    merge_key: str,
) -> None:
    store = InMemoryCuratorStore()
    bad = _profile(store, merge_key)
    _profile(store, "딸기")
    judge = FakeJudge()
    _observe(store, "1", "생딸기")

    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.reason) == ("held", "invalid_candidate_name")
    assert outcome.invalid_profile_ids == (bad.id,)
    assert judge.calls == []


async def test_추릴_때도_같은_이름이면_가장_오래된_Profile_에_연결한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_step, "MAX_CANDIDATES", 2)
    store = InMemoryCuratorStore()
    oldest = _profile(store, "생딸기", _at(70))  # 멀어서 추린 목록에는 빠진다
    _profile(store, "블루베리", _at(40))
    _profile(store, "생딸기", _at(5))  # 같은 이름의 새 Profile — 추린 목록에 들어간다
    judge = picks("생딸기")
    _observe(store, "1", "딸기", _at(0))

    [outcome] = await _link(store, judge)

    assert judge.calls[0][2] == ["생딸기", "블루베리"]
    assert outcome.affinity_id == oldest.id


@pytest.mark.parametrize("vector", [None, [1.0, 0.0, 0.0]], ids=["벡터_없음", "차원이_다름"])
async def test_추릴_때_벡터가_없거나_차원이_다르면_보류한다(
    monkeypatch: pytest.MonkeyPatch, vector: list[float] | None
) -> None:
    monkeypatch.setattr(link_step, "MAX_CANDIDATES", 1)
    store = InMemoryCuratorStore()
    broken = store.add_profile(
        child_id=CHILD, domain="food", merge_key="블루베리", polarity=1, embedding=vector
    )
    _profile(store, "딸기", _at(10))
    _observe(store, "1", "생딸기", _at(0))

    [outcome] = await _link(store, FakeJudge())

    assert (outcome.reason, outcome.invalid_profile_ids) == (
        "invalid_profile_embedding",
        (broken.id,),
    )


async def test_추릴_때_유사도가_같으면_오래된_Profile_이_앞이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_step, "MAX_CANDIDATES", 1)
    store = InMemoryCuratorStore()
    _profile(store, "생딸기", _at(20))
    _profile(store, "딸기잼", _at(-20))  # 반대쪽으로 같은 각도 → 같은 유사도
    judge = FakeJudge()
    _observe(store, "1", "딸기", _at(0))

    await _link(store, judge)

    assert judge.calls[0][2] == ["생딸기"]


async def test_후보가_정확히_상한이면_추리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(link_step, "MAX_CANDIDATES", 2)
    store = InMemoryCuratorStore()
    _profile(store, "블루베리", [0.0, 0.0])  # 추린다면 보류됐을 벡터
    _profile(store, "딸기", _at(10))
    judge = FakeJudge()
    _observe(store, "1", "생딸기", _at(0))

    await _link(store, judge)

    assert judge.calls[0][2] == ["블루베리", "딸기"]  # 저장된 순서 그대로, 둘 다 보여 준다


# 반대 방향 확인 — 판정기가 none 이면 방향을 바꿔 한 번 더 묻는다


def same_pair(a: str, b: str) -> FakeJudge:
    """a 를 물으면 none, b 를 물으면 a 를 고른다. 순서에 따라 답이 갈리는 판정기."""
    return FakeJudge(lambda subject, candidates: a if subject == b and a in candidates else NONE)


async def test_none_이면_방향을_바꿔_묻고_그렇다면_연결한다() -> None:
    store = InMemoryCuratorStore()
    carrot = _profile(store, "홍당무")
    judge = same_pair("당근", "홍당무")
    _observe(store, "1", "당근")

    [outcome] = await _link(store, judge)

    assert judge.calls == [
        ("당근", "food", ["홍당무"]),
        ("홍당무", "food", ["당근"]),  # 후보가 subject 로, subject 가 유일한 후보로
    ]
    assert (outcome.status, outcome.match, outcome.affinity_id) == (
        "linked",
        "reversed",
        carrot.id,
    )
    assert (outcome.judge_model, outcome.confidence) == (MODEL, 0.9)
    assert len(store.profiles) == 1


async def test_반대_방향도_모두_아니면_새_candidate_를_만든다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "블루베리")
    _profile(store, "레고")
    judge = FakeJudge()
    _observe(store, "1", "딸기")

    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.match) == ("created", "new")
    assert [call[0] for call in judge.calls] == ["딸기", "블루베리", "레고"]


@pytest.mark.parametrize(
    "answer", ["딸기", UNCERTAIN, "딸기잼"], ids=["후보", "uncertain", "목록밖"]
)
async def test_none_이_아니면_반대_방향으로_묻지_않는다(answer: str) -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    judge = picks(answer)
    _observe(store, "1", "생딸기")

    await _link(store, judge)

    assert len(judge.calls) == 1


async def test_반대_방향에서_같은_merge_key_는_한_번만_묻고_오래된_것에_연결한다() -> None:
    store = InMemoryCuratorStore()
    oldest = _profile(store, "홍당무")
    _profile(store, "홍당무")
    judge = same_pair("당근", "홍당무")
    _observe(store, "1", "당근")

    [outcome] = await _link(store, judge)

    assert len(judge.calls) == 2
    assert outcome.affinity_id == oldest.id


# 반대 방향 확인 — 오류


async def test_반대_방향_호출이_실패하면_새로_만들지_않고_보류한다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "홍당무")
    judge = FakeJudge(error=LLMUnavailableError("down"), fail_on="홍당무")
    _observe(store, "1", "당근")

    [outcome] = await _link(store, judge)

    assert [call[0] for call in judge.calls] == ["당근", "홍당무"]
    assert (outcome.status, outcome.reason, outcome.error) == (
        "held",
        "judge_failed",
        "LLMUnavailableError",
    )
    assert len(store.profiles) == 1  # 장애 때문에 Profile 이 나뉘지 않는다
    assert store.observation("food", "1").affinity_id is None


# 반대 방향 확인 — 물을 후보 고르기


async def test_후보가_REVERSE_TOP_이하면_벡터를_보지_않고_모두_묻는다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "블루베리", [0.0, 0.0])  # 잘못된 벡터지만 고르기를 하지 않으니 상관없다
    _profile(store, "레고")
    _profile(store, "홍당무")
    judge = same_pair("당근", "홍당무")
    _observe(store, "1", "당근")

    [outcome] = await _link(store, judge)

    assert [call[0] for call in judge.calls] == ["당근", "블루베리", "레고", "홍당무"]
    assert outcome.match == "reversed"


async def test_후보가_REVERSE_TOP_을_넘으면_가까운_순서로_골라_묻는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_step, "REVERSE_TOP", 2)
    store = InMemoryCuratorStore()
    _profile(store, "레고", _at(80))
    _profile(store, "블루베리", _at(40))
    _profile(store, "홍당무", _at(10))
    judge = FakeJudge()
    _observe(store, "1", "당근", _at(0))

    await _link(store, judge)

    assert [call[0] for call in judge.calls] == ["당근", "홍당무", "블루베리"]


async def test_반대_방향으로_물을_후보를_고를_때_Profile_벡터가_잘못되면_보류한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_step, "REVERSE_TOP", 1)
    store = InMemoryCuratorStore()
    broken = _profile(store, "블루베리", [0.0, 0.0])
    _profile(store, "홍당무", _at(10))
    judge = FakeJudge()
    _observe(store, "1", "당근", _at(0))

    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.reason) == ("held", "invalid_profile_embedding")
    assert outcome.invalid_profile_ids == (broken.id,)
    assert len(judge.calls) == 1  # 첫 판정 뒤, 반대 방향으로는 묻지 않았다
    assert len(store.profiles) == 2


# uncertain 보류 횟수 — UNCERTAIN_LIMIT 번째면 새로 만든다


async def test_uncertain_으로_세_번째_보류되면_새_candidate_를_만든다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "그거")
    judge = picks(UNCERTAIN)

    [first] = await _link(store, judge)
    [second] = await _link(store, judge)
    [third] = await _link(store, judge)

    assert (first.reason, second.reason) == ("judge_uncertain", "judge_uncertain")
    assert (third.status, third.match, third.judge_model) == ("created", "uncertain_limit", MODEL)
    assert [p.merge_key for p in store.profiles] == ["딸기", "그거"]
    assert store.observation("food", "1").affinity_id == third.affinity_id
    assert store.uncertain_count("food", "1") is None  # 연결됐으니 기록을 지웠다


async def test_subject_가_바뀌면_보류_횟수를_1_부터_다시_센다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "그거")
    judge = picks(UNCERTAIN)
    await _link(store, judge)
    await _link(store, judge)

    _observe(store, "1", "저거")  # 보호자가 고쳤다 (같은 관찰, 다른 subject)
    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.reason) == ("held", "judge_uncertain")
    assert store.uncertain_count("food", "1") == 1


async def test_띄어쓰기만_바뀌면_보류_횟수를_이어서_센다() -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "그 거")
    judge = picks(UNCERTAIN)
    await _link(store, judge)
    await _link(store, judge)

    _observe(store, "1", "그거")
    [outcome] = await _link(store, judge)

    assert (outcome.status, outcome.match) == ("created", "uncertain_limit")


@pytest.mark.parametrize(
    "judge",
    [FakeJudge(error=LLMUnavailableError("down")), picks("딸기잼")],
    ids=["오류", "목록밖"],
)
async def test_오류와_목록_밖의_답은_보류_횟수로_세지_않는다(judge: FakeJudge) -> None:
    store = InMemoryCuratorStore()
    _profile(store, "딸기")
    _observe(store, "1", "생딸기")

    for _ in range(3):
        [outcome] = await _link(store, judge)

    assert outcome.status == "held"
    assert store.uncertain_count("food", "1") is None
    assert len(store.profiles) == 1
