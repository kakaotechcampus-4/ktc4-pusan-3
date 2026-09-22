"""출력 채널 다섯의 계약.

docs/agents/shared/Agent_공통규약.md §3 · §10
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.agents.common.evidence import RankedEvidence
from app.agents.common.readout import Readout, ReadoutCatalog, ReadoutText
from app.agents.common.refs import Ref, count_child_records
from app.agents.common.result import (
    DomainAgentResult,
    EventRequest,
    MedicationDraft,
)
from app.agents.common.suggestion import (
    REQUIRED_COUNT,
    SuggestionDraft,
    SuggestionRejected,
    build,
    check_count,
)

KST = timezone(timedelta(hours=9))


def evidence(*, kind="observation_food", polarity=1, label="당근"):
    return RankedEvidence(
        ref=Ref(kind=kind, id=uuid4()),
        tier=1,
        label=label,
        polarity=polarity,
        observed_on=date(2026, 9, 20),
    )


class TestRefs:
    def test_문서_행은_개인화_근거로_세지_않는다(self):
        """문서 행만 달고 나간 개인화 추천은 근거 0행과 같다."""
        refs = (Ref(kind="food_doc", id=uuid4()), Ref(kind="growth_doc", id=uuid4()))
        assert count_child_records(refs) == 0

    def test_아이_기록은_센다(self):
        refs = (Ref(kind="observation_food", id=uuid4()), Ref(kind="food_doc", id=uuid4()))
        assert count_child_records(refs) == 1

    @pytest.mark.parametrize(
        "kind", ["observation_routine", "profile_affinity", "child_growth_log", "daycare_meal"]
    )
    def test_아이_기록_종류(self, kind):
        assert Ref(kind=kind, id=uuid4()).is_child_record is True


class TestSuggestionBuild:
    def test_근거가_있으면_개인화(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="지난주에 계란을 잘 먹어서요",
            evidence=(evidence(),),
        )
        assert draft.kind == "personalized"
        assert len(draft.source_refs) == 1

    def test_근거_0행이면_일반_추천이고_이유를_덮어쓴다(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="모델이 쓴 개인화 문장",
            evidence=(),
            general_reason="또래 아이들이 많이 먹는 메뉴예요.",
        )
        assert draft.kind == "general"
        assert draft.reason == "또래 아이들이 많이 먹는 메뉴예요."
        assert draft.source_refs == ()

    def test_문서_행만_있으면_일반_추천(self):
        draft = build(
            agent="growth",
            content="숫자 세기 놀이",
            reason="모델이 쓴 문장",
            evidence=(evidence(kind="growth_doc"),),
            general_reason="또래 아이들이 많이 하는 놀이예요.",
        )
        assert draft.kind == "general"

    def test_근거_0행인데_일반_문구가_없으면_거절(self):
        with pytest.raises(SuggestionRejected, match="일반 추천 문구"):
            build(agent="food", content="계란말이", reason="이유", evidence=())

    def test_개인화인데_이유가_비면_거절(self):
        with pytest.raises(SuggestionRejected, match="이유가 비었다"):
            build(agent="food", content="계란말이", reason="   ", evidence=(evidence(),))


class TestAvoidanceMustBeStated:
    def test_기피를_인용했으면_이유에_밝혀야_한다(self):
        with pytest.raises(SuggestionRejected, match="무엇을 피했는지"):
            build(
                agent="food",
                content="오므라이스",
                reason="단백질이 들어 있어요",
                evidence=(evidence(polarity=-1, label="브로콜리"),),
            )

    def test_밝히면_통과(self):
        draft = build(
            agent="food",
            content="오므라이스",
            reason="브로콜리를 기피해서 계란 맛이 강한 걸로 골랐어요",
            evidence=(evidence(polarity=-1, label="브로콜리"),),
        )
        assert draft.kind == "personalized"

    def test_기피가_없으면_검사하지_않는다(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="계란을 잘 먹어서요",
            evidence=(evidence(polarity=1, label="계란"),),
        )
        assert draft.kind == "personalized"


class TestCount:
    def test_정확히_3개(self):
        drafts = tuple(
            build(agent="food", content=f"{i}", reason="", evidence=(), general_reason="또래 기준")
            for i in range(REQUIRED_COUNT)
        )
        check_count(drafts)

    @pytest.mark.parametrize("count", [0, 1, 2, 4, 5])
    def test_개수가_다르면_거절(self, count):
        drafts = tuple(
            build(agent="food", content=f"{i}", reason="", evidence=(), general_reason="또래 기준")
            for i in range(count)
        )
        with pytest.raises(SuggestionRejected, match="정확히 3개"):
            check_count(drafts)

    def test_재호출_뒤_모자라면_남은_만큼만(self):
        drafts = (
            build(agent="food", content="하나", reason="", evidence=(), general_reason="또래 기준"),
        )
        check_count(drafts, after_retry=True)

    def test_재호출_뒤에도_0개면_거절(self):
        with pytest.raises(SuggestionRejected):
            check_count((), after_retry=True)


class TestReadout:
    def test_코드가_쓴_문구는_그대로_나간다(self):
        text = ReadoutText(
            key="unsupported.milk_meal", template="이 시기에는 모유나 분유만 먹어요."
        )
        readout = text.render()
        assert readout.authored_by == "code"
        assert readout.body == "이 시기에는 모유나 분유만 먹어요."

    def test_자리_채우기(self):
        text = ReadoutText(key="symptom.repeat", template="최근 2주 동안 {n}번 기록됐어요.")
        assert text.render(n=3).body == "최근 2주 동안 3번 기록됐어요."

    def test_본문이_비면_거절(self):
        with pytest.raises(ValueError, match="본문이 비었다"):
            Readout(kind="unsupported", body="  ")

    def test_정의되지_않은_키는_즉시_실패(self):
        catalog = ReadoutCatalog({"a": ReadoutText(key="a", template="가")})
        assert catalog.render("a").body == "가"
        with pytest.raises(KeyError, match="정의되지 않은 readout 키"):
            catalog.render("b")


class TestDomainAgentResult:
    def test_되묻기는_하나만(self):
        with pytest.raises(ValueError, match="되묻기는 하나만"):
            DomainAgentResult(
                agent="growth",
                task_type="routine_coaching",
                status="completed",
                needs_observation=("언제 그러나요?", "요즘도 그러나요?"),
            )

    def test_기본값은_전부_비어_있다(self):
        result = DomainAgentResult(agent="health", task_type="place_lookup", status="completed")
        assert result.suggestions == ()
        assert result.readouts == ()
        assert result.medication_drafts == ()
        assert result.model_calls == 0

    def test_채널을_섞어_담을_수_있다(self):
        result = DomainAgentResult(
            agent="food",
            task_type="meal_recommendation",
            status="degraded",
            suggestions=(SuggestionDraft(agent="food", kind="general", content="a", reason="b"),),
            readouts=(Readout(kind="unsupported", body="안내"),),
        )
        assert result.status == "degraded"
        assert len(result.suggestions) == 1


class TestMedicationDraft:
    def _draft(self, **kwargs):
        base = dict(
            draft_id="m1",
            op="create",
            source="utterance",
            schedule={"title": "항생제"},
            doses=({"scheduled_time": "08:30"},),
            notice_times=("08:30",),
        )
        return MedicationDraft(**{**base, **kwargs})

    def test_알림_시각이_없으면_거절(self):
        """보호자가 승인하는 것은 발화가 아니라 실제로 울릴 시각이다."""
        with pytest.raises(ValueError, match="실제 알림 시각이 없다"):
            self._draft(notice_times=())

    def test_수정인데_대상이_없으면_거절(self):
        with pytest.raises(ValueError, match="schedule_id"):
            self._draft(op="update")

    def test_빈_필수_칸을_실어_보낸다(self):
        draft = self._draft(missing=("starts_on",))
        assert draft.missing == ("starts_on",)


class TestEventRequest:
    def test_검진_알림(self):
        request = EventRequest(
            title="영유아 건강검진",
            starts_at=datetime(2026, 10, 1, 9, 0, tzinfo=KST),
            category="health",
            all_day=True,
        )
        assert request.category == "health"
        assert request.ends_at is None
