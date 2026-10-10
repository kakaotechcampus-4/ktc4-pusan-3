"""pick_next_step — 자립 단계 사슬에서 **바로 다음 칸**만 고른다.

- 건너뛰기 0건: 어느 도움 수준에서도 현재 칸 + 1 이다.
- 사슬 끝이면 없는 다음 칸을 지어내지 않고 `next_step.chain_end`.
- 도움 수준이 비면 추정하지 않고 되묻는다.
- 사슬이 이상하면(갈라짐 · 끊김 · 순환) 추측하지 않고 `ChainError`.
"""

import random
from uuid import UUID

import pytest

from app.agents.growth.store.ports import GrowthDocRow
from app.agents.growth.tools.routine import (
    ChainError,
    NextStep,
    ordered_chain,
    pick_next_step,
)

FULL, PARTIAL, VERBAL, INDEPENDENT = "full_assist", "partial_assist", "verbal_prompt", "independent"


def step(n: int, level: str | None, previous: int | None) -> GrowthDocRow:
    return GrowthDocRow(
        id=UUID(int=n),
        doc_key=f"routine_step.toothbrush.{n}",
        row_type="routine_step",
        title=f"{n}단계",
        body="본문",
        min_month=12,
        max_month=72,
        routine_category="self_care",
        next_step_of=UUID(int=previous) if previous is not None else None,
        tags=(f"assistance:{level}",) if level else (),
    )


def chain(*levels: str) -> list[GrowthDocRow]:
    """1 ← 2 ← 3 … 으로 이어진 사슬. 행 n 의 앞 단계는 n-1 이다."""
    return [step(i, level, i - 1 if i > 1 else None) for i, level in enumerate(levels, start=1)]


class TestPick:
    FOUR = (FULL, PARTIAL, VERBAL, INDEPENDENT)

    @pytest.mark.parametrize(
        ("level", "expected"),
        [(FULL, 2), (PARTIAL, 3), (VERBAL, 4)],
    )
    def test_현재_칸의_바로_다음_행을_고른다(self, level, expected):
        picked = pick_next_step(chain(*self.FOUR), assistance_level=level)
        assert picked.row is not None and picked.row.id == UUID(int=expected)
        assert picked.readout_key is None

    def test_혼자_하는_칸이_사슬의_끝이면_끝_안내다(self):
        picked = pick_next_step(chain(*self.FOUR), assistance_level=INDEPENDENT)
        assert picked == NextStep(readout_key="next_step.chain_end")

    def test_건너뛰기_0건(self):
        """어느 수준에서도 고른 행의 앞 단계가 현재 칸이다 — 한 칸 이상 뛰지 않는다."""
        rows = chain(FULL, FULL, PARTIAL, PARTIAL, VERBAL, INDEPENDENT)
        ordered = ordered_chain(rows)
        for level in (FULL, PARTIAL, VERBAL):
            picked = pick_next_step(rows, assistance_level=level)
            assert picked.row is not None
            current_index = next(
                i for i, row in enumerate(ordered) if row.tags == (f"assistance:{level}",)
            )
            assert ordered.index(picked.row) == current_index + 1

    def test_같은_수준_행이_여럿이면_가장_앞에서_한_칸씩_올린다(self):
        rows = chain(FULL, PARTIAL, PARTIAL, VERBAL, INDEPENDENT)
        picked = pick_next_step(rows, assistance_level=PARTIAL)
        assert picked.row is not None and picked.row.id == UUID(int=3)  # 둘째 PARTIAL

    def test_사슬에_없는_수준이면_그보다_낮은_수준의_마지막_행이_현재다(self):
        rows = chain(FULL, VERBAL, INDEPENDENT)
        picked = pick_next_step(rows, assistance_level=PARTIAL)
        assert picked.row is not None and picked.row.id == UUID(int=2)

    def test_사슬의_첫_행보다도_아래에_있으면_첫_행이_다음_칸이다(self):
        rows = chain(PARTIAL, VERBAL, INDEPENDENT)
        picked = pick_next_step(rows, assistance_level=FULL)
        assert picked.row is not None and picked.row.id == UUID(int=1)

    def test_사슬_끝_행의_수준보다_위여도_지어내지_않는다(self):
        rows = chain(FULL, PARTIAL)
        assert pick_next_step(rows, assistance_level=INDEPENDENT).readout_key == (
            "next_step.chain_end"
        )

    def test_입력_순서와_상관없이_같은_행을_고른다(self):
        rows = chain(*self.FOUR)
        shuffled = rows[:]
        random.Random(7).shuffle(shuffled)
        assert pick_next_step(shuffled, assistance_level=PARTIAL) == pick_next_step(
            rows, assistance_level=PARTIAL
        )

    @pytest.mark.parametrize("level", [None, ""])
    def test_도움_수준이_비면_추정하지_않고_되묻는다(self, level):
        picked = pick_next_step(chain(*self.FOUR), assistance_level=level)
        assert picked == NextStep(readout_key="ask.routine_current")

    def test_알_수_없는_수준은_거부한다(self):
        with pytest.raises(ValueError):
            pick_next_step(chain(*self.FOUR), assistance_level="expert")

    def test_모델이_단계를_고르지_않는다_함수에_모델_입력이_없다(self):
        import inspect

        params = inspect.signature(pick_next_step).parameters
        assert set(params) == {"chain", "assistance_level"}


class TestChain:
    def test_앞_단계를_따라_처음부터_끝까지_세운다(self):
        rows = chain(FULL, PARTIAL, VERBAL)
        assert [row.id.int for row in ordered_chain(rows[::-1])] == [1, 2, 3]

    def test_빈_사슬은_거부한다(self):
        with pytest.raises(ChainError, match="비었다"):
            ordered_chain([])

    def test_같은_행이_둘이면_거부한다(self):
        row = step(1, FULL, None)
        with pytest.raises(ChainError, match="같은 행"):
            ordered_chain([row, row])

    def test_갈라지면_거부한다(self):
        rows = [step(1, FULL, None), step(2, PARTIAL, 1), step(3, PARTIAL, 1)]
        with pytest.raises(ChainError, match="갈라진다"):
            ordered_chain(rows)

    def test_처음이_둘이면_거부한다(self):
        """사슬이 끊겨 두 토막이 됐다."""
        rows = [step(1, FULL, None), step(2, PARTIAL, 1), step(3, VERBAL, None)]
        with pytest.raises(ChainError, match="처음이 2개"):
            ordered_chain(rows)

    def test_도는_사슬은_거부한다(self):
        rows = [step(1, FULL, 3), step(2, PARTIAL, 1), step(3, VERBAL, 2)]
        with pytest.raises(ChainError):
            ordered_chain(rows)

    def test_도움_수준_태그가_없으면_고르지_않고_거부한다(self):
        rows = [step(1, FULL, None), step(2, None, 1)]
        with pytest.raises(ChainError, match="태그가 없는"):
            pick_next_step(rows, assistance_level=FULL)

    def test_수준이_거꾸로_가는_사슬은_거부한다(self):
        rows = chain(INDEPENDENT, FULL)
        with pytest.raises(ChainError, match="나아가지 않는다"):
            pick_next_step(rows, assistance_level=FULL)

    def test_알_수_없는_태그는_거부한다(self):
        rows = [step(1, "expert", None), step(2, INDEPENDENT, 1)]
        with pytest.raises(ChainError, match="태그가 이상하다"):
            pick_next_step(rows, assistance_level=FULL)

    def test_결과는_행이나_readout_둘_중_하나다(self):
        with pytest.raises(ValueError):
            NextStep()
        with pytest.raises(ValueError):
            NextStep(row=step(1, FULL, None), readout_key="next_step.chain_end")
