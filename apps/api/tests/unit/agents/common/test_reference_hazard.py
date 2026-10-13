"""hazard_terms.yaml 로더 — 모양이 깨지면 읽을 때 바로 실패한다.

위험 용어 사전은 안전 필터의 근거표라 조용히 깨지면 위험한 놀이가 그대로 나간다.
그래서 축 · 용어 · 별칭의 규칙을 로더가 직접 검사한다 (설계 3-5).
"""

import copy

import pytest

from app.agents.common.reference import HazardAxis, hazard_terms, parse_hazard_terms

VALID = {
    "source": "테스트",
    "version": "1",
    "fetched_at": "2026-10-02",
    "pending_axes": {"height": "다음 판"},
    "axes": {
        "small_parts": {
            "block_below_month": 36,
            "warn_below_month": 72,
            "source": "공통안전기준 https://example.test",
            "warning_text": "작은 부품은 삼킬 수 있어요.",
        },
        "trampoline": {"block_below_month": 72, "source": "AAP https://example.test"},
    },
    "terms": [
        {"axis": "small_parts", "label": "구슬", "aliases": ["구슬", "비즈"], "guards": ["구슬땀"]},
        {"axis": "trampoline", "label": "방방", "aliases": ["방방"], "guards": []},
    ],
}


def broken(change) -> dict:
    data = copy.deepcopy(VALID)
    change(data)
    return data


class TestValid:
    def test_용어_key_는_label_이고_축을_찾을_수_있다(self):
        result = parse_hazard_terms(VALID)
        assert [term.key for term in result.terms] == ["구슬", "방방"]
        assert result.axis_of("구슬").name == "small_parts"
        assert result.axis_of("방방").block_below_month == 72

    def test_별칭과_guard_를_그대로_싣는다(self):
        (beads, _) = parse_hazard_terms(VALID).terms
        assert beads.aliases == ("구슬", "비즈")
        assert beads.guards == ("구슬땀",)

    def test_실제_사전을_읽고_한_번만_만든다(self):
        assert hazard_terms() is hazard_terms()
        assert len(hazard_terms().terms) > 0


class TestLevelAt:
    """축의 월령 두 칸을 읽는 규칙. 0–17개월은 경고도 차단이다 — Activity · Growth 가 이 판정
    하나를 읽는다 (#282 리뷰)."""

    @pytest.mark.parametrize(
        ("months", "level"), [(0, "block"), (35, "block"), (36, "warn"), (71, "warn"), (72, None)]
    )
    def test_차단과_경고_경계(self, months, level):
        assert parse_hazard_terms(VALID).axes["small_parts"].level_at(months) == level

    @pytest.mark.parametrize(("months", "level"), [(71, "block"), (72, None)])
    def test_차단만_있는_축(self, months, level):
        assert parse_hazard_terms(VALID).axes["trampoline"].level_at(months) == level

    @pytest.mark.parametrize(
        ("months", "level"), [(0, "block"), (17, "block"), (18, "warn"), (71, "warn"), (72, None)]
    )
    def test_경고만_있는_축도_17개월까지는_차단이다(self, months, level):
        """물놀이처럼 출처에 임계 월령이 없어 경고만 있는 축 (D6 규칙 ②)."""
        water = HazardAxis(
            name="water",
            block_below_month=None,
            warn_below_month=72,
            source="질병관리청 https://example.test",
            warning_text="물가에서는 손이 닿는 거리에 있어 주세요.",
        )
        assert water.level_at(months) == level


class TestBrokenAxes:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda d: d["axes"]["trampoline"].pop("block_below_month"), "월령"),
            (lambda d: d["axes"]["trampoline"].update(source="  "), "source"),
            (lambda d: d["axes"]["small_parts"].pop("warning_text"), "warning_text"),
            (lambda d: d["axes"]["small_parts"].update(warn_below_month=30), "warn"),
            (lambda d: d["axes"]["small_parts"].update(block_below_month="36"), "정수"),
            (lambda d: d["axes"]["small_parts"].update(extra=1), "칸"),
            (lambda d: d["pending_axes"].update(trampoline="중복"), "pending"),
            (
                lambda d: d["axes"].update(
                    water={"warn_below_month": 72, "source": "x", "warning_text": "x"}
                ),
                "용어가 없는 축",
            ),
        ],
    )
    def test_축이_깨지면_실패한다(self, change, message):
        with pytest.raises(ValueError, match=message):
            parse_hazard_terms(broken(change))


class TestBrokenTerms:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda d: d["terms"][0].update(axis="height"), "없는 축"),
            (lambda d: d["terms"][0].update(aliases=["비즈"]), "label"),
            (lambda d: d["terms"][0].update(block_below_month=36), "칸"),
            (lambda d: d["terms"].append(dict(d["terms"][0], aliases=["구슬"])), "label 이 겹친다"),
            (lambda d: d["terms"][1].update(aliases=["방방", "비 즈"]), "별칭이 겹친다"),
            (lambda d: d["terms"][0].update(aliases=["구슬", "알"]), "한 글자"),
        ],
    )
    def test_용어가_깨지면_실패한다(self, change, message):
        with pytest.raises(ValueError, match=message):
            parse_hazard_terms(broken(change))
