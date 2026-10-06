"""출력 채널 다섯의 계약.

docs/agents/shared/Agent_공통규약.md §3 · §10
"""

from datetime import date, datetime, timedelta, timezone
from typing import get_args
from uuid import uuid4

import pytest

from app.agents.common import refs
from app.agents.common.evidence import RankedEvidence, cite
from app.agents.common.readout import Readout, ReadoutCatalog, ReadoutText
from app.agents.common.refs import (
    ChildRecordKind,
    DocKind,
    EvidenceCitation,
    Ref,
    count_child_records,
)
from app.agents.common.result import (
    DomainAgentResult,
    EventRequest,
    MedicationDraft,
)
from app.agents.common.suggestion import (
    MAX_SUGGESTIONS,
    SuggestionDraft,
    SuggestionRejected,
    build,
    check_count,
    count_notice,
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


def citation(*, kind="observation_food", polarity=1, label="당근", note="지난주에 두 번 먹었다"):
    return cite(
        evidence(kind=kind, polarity=polarity, label=label),
        note=note,
    )


def general_drafts(count):
    """근거 없는 일반 추천 count 개. 개수 검사는 내용을 보지 않는다."""
    return tuple(
        build(agent="food", content=f"{i}", reason="", citations=(), general_reason="또래 기준")
        for i in range(count)
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

    def test_근거_종류와_개인화_판정_목록이_같다(self):
        """refs.py 에 같은 목록이 두 번 적혀 있다. 한쪽만 고치면 근거가 조용히 안 세지거나,
        타입에서 뺀 종류가 계속 개인화로 세진다."""
        assert refs._CHILD_RECORD_KINDS == frozenset(get_args(ChildRecordKind))
        for kind in get_args(ChildRecordKind):
            assert Ref(kind=kind, id=uuid4()).is_child_record is True, kind
        for kind in get_args(DocKind):
            assert Ref(kind=kind, id=uuid4()).is_child_record is False, kind


class TestSuggestionBuild:
    def test_근거가_있으면_개인화(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="지난주에 계란을 잘 먹어서요",
            citations=(citation(),),
        )
        assert draft.kind == "personalized"
        assert len(draft.citations) == 1

    def test_근거_0행이면_일반_추천이고_이유를_덮어쓴다(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="모델이 쓴 개인화 문장",
            citations=(),
            general_reason="또래 아이들이 많이 먹는 메뉴예요.",
        )
        assert draft.kind == "general"
        assert draft.reason == "또래 아이들이 많이 먹는 메뉴예요."
        assert draft.citations == ()

    def test_문서_행만_있으면_일반_추천(self):
        draft = build(
            agent="growth",
            content="숫자 세기 놀이",
            reason="모델이 쓴 문장",
            citations=(citation(kind="growth_doc", polarity=0, label=""),),
            general_reason="또래 아이들이 많이 하는 놀이예요.",
        )
        assert draft.kind == "general"
        # 문서 행은 kind 를 바꾸지 않지만 인용은 그대로 실려 나간다
        assert len(draft.citations) == 1

    def test_근거_0행인데_일반_문구가_없으면_거절(self):
        with pytest.raises(SuggestionRejected, match="일반 추천 문구"):
            build(agent="food", content="계란말이", reason="이유", citations=())

    def test_개인화인데_이유가_비면_거절(self):
        with pytest.raises(SuggestionRejected, match="이유가 비었다"):
            build(agent="food", content="계란말이", reason="   ", citations=(citation(),))


class TestNoteRequired:
    """`note` 가 비면 그 후보만 뺀다. 묶음은 거절하지 않는다."""

    def test_note_가_비면_그_후보를_거절한다(self):
        with pytest.raises(SuggestionRejected, match="note"):
            build(
                agent="food",
                content="계란말이",
                reason="계란을 잘 먹어서요",
                citations=(citation(note="   "),),
            )

    def test_문서_행도_note_가_필요하다(self):
        with pytest.raises(SuggestionRejected, match="note"):
            build(
                agent="growth",
                content="숫자 세기 놀이",
                reason="모델이 쓴 문장",
                citations=(
                    EvidenceCitation(
                        ref=Ref(kind="growth_doc", id=uuid4()),
                        note="",
                    ),
                ),
                general_reason="또래 아이들이 많이 하는 놀이예요.",
            )


class TestAvoidanceMustBeStated:
    def test_기피를_인용했으면_이유에_밝혀야_한다(self):
        with pytest.raises(SuggestionRejected, match="무엇을 피했는지"):
            build(
                agent="food",
                content="오므라이스",
                reason="단백질이 들어 있어요",
                citations=(citation(polarity=-1, label="브로콜리"),),
            )

    def test_밝히면_통과(self):
        draft = build(
            agent="food",
            content="오므라이스",
            reason="브로콜리를 기피해서 계란 맛이 강한 걸로 골랐어요",
            citations=(citation(polarity=-1, label="브로콜리"),),
        )
        assert draft.kind == "personalized"

    def test_기피가_없으면_검사하지_않는다(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="계란을 잘 먹어서요",
            citations=(citation(polarity=1, label="계란"),),
        )
        assert draft.kind == "personalized"


class TestSuggestionLists:
    """`allergens` · `items` 는 판정에 관여하지 않고 그대로 실린다.

    채우는 쪽은 각 Agent 의 출력 tool 이다.
    """

    def test_기본은_빈_목록(self):
        draft = build(
            agent="food", content="된장찌개", reason="", citations=(), general_reason="또래 기준"
        )
        assert draft.allergens == ()
        assert draft.items == ()
        assert draft.to_payload()["allergens"] == []
        assert draft.to_payload()["items"] == []

    def test_일반_추천도_그대로_싣는다(self):
        draft = build(
            agent="food",
            content="된장찌개",
            reason="",
            citations=(),
            general_reason="또래 기준",
            allergens=("대두",),
            items=("된장", "두부"),
        )
        assert draft.kind == "general"
        payload = draft.to_payload()
        assert payload["allergens"] == ["대두"]
        assert payload["items"] == ["된장", "두부"]

    def test_개인화_추천도_그대로_싣는다(self):
        draft = build(
            agent="activity",
            content="수영하기",
            reason="물놀이를 좋아해서요",
            citations=(citation(kind="observation_activity", label="물놀이"),),
            items=("수영복",),
        )
        assert draft.kind == "personalized"
        assert draft.items == ("수영복",)


class TestCount:
    """추천은 최대 3개 (Tool_공통.md §5-2). 동작은 C-8 닫힘 전과 같고 이름 · 문구만 바뀌었다."""

    def test_3개면_통과(self):
        check_count(general_drafts(MAX_SUGGESTIONS))

    @pytest.mark.parametrize("count", [1, 2, 3])
    def test_더_채울_길이_없으면_1개부터_통과(self, count):
        check_count(general_drafts(count), exhausted=True)

    @pytest.mark.parametrize("count", [1, 2])
    def test_채울_수_있는데_모자라면_거절(self, count):
        """여기서 통과시키면 더 채우지 않고 끝나서, "최대" 가 "아무 개수나" 가 된다."""
        with pytest.raises(SuggestionRejected, match="채울 수 있는데"):
            check_count(general_drafts(count))

    @pytest.mark.parametrize("exhausted", [False, True])
    def test_0개는_거절(self, exhausted):
        """0개는 추천이 아니다. 호출부가 추천 없이 안내(count_notice)로 끝낸다."""
        with pytest.raises(SuggestionRejected, match="0개"):
            check_count((), exhausted=exhausted)

    @pytest.mark.parametrize("exhausted", [False, True])
    @pytest.mark.parametrize("count", [4, 5])
    def test_넘치면_거절(self, count, exhausted):
        with pytest.raises(SuggestionRejected, match="최대 3개"):
            check_count(general_drafts(count), exhausted=exhausted)


class TestCountNotice:
    """다 못 채운 추천에 붙는 안내 (Tool_공통.md §5-2). 상수 문구라 글자 단위로 비교한다."""

    # Food 의 0개 문구(pool.empty)와 같은 모양. Food 의 상수 카탈로그는 아직 코드에 없다
    POOL_EMPTY = ReadoutText(key="pool.empty", template="조건에 맞는 메뉴가 없어요.", kind="notice")

    def test_다_채우면_안내가_없다(self):
        assert count_notice(MAX_SUGGESTIONS) is None

    @pytest.mark.parametrize("count", [1, 2])
    def test_모자라면_나간_개수를_알린다(self, count):
        notice = count_notice(count)
        assert notice is not None
        assert notice.body == f"조건에 맞는 추천을 {count}개 준비했어요."
        assert notice.kind == "notice"
        assert notice.authored_by == "code"

    def test_0개면_추천이_없다고_알린다(self):
        notice = count_notice(0)
        assert notice is not None
        assert notice.body == "조건에 맞는 추천이 없어요."
        assert notice.kind == "notice"
        assert notice.authored_by == "code"

    def test_0개_문구가_따로_있는_Agent_는_그_문구를_쓴다(self):
        notice = count_notice(0, empty=self.POOL_EMPTY)
        assert notice is not None
        assert notice.body == "조건에 맞는 메뉴가 없어요."

    def test_0개_문구는_모자랄_때_쓰지_않는다(self):
        notice = count_notice(2, empty=self.POOL_EMPTY)
        assert notice is not None
        assert notice.body == "조건에 맞는 추천을 2개 준비했어요."

    @pytest.mark.parametrize("count", [-1, MAX_SUGGESTIONS + 1])
    def test_개수_밖이면_ValueError(self, count):
        """check_count 를 거친 개수만 들어온다. 밖이면 호출부 버그다."""
        with pytest.raises(ValueError, match="추천 개수"):
            count_notice(count)


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


class TestReadoutYaml:
    """`*.readout.yaml` 로더. 모양이 틀리면 import 때 바로 실패한다."""

    def test_키마다_문구와_종류를_읽는다(self, tmp_path):
        path = tmp_path / "x.readout.yaml"
        path.write_text(
            "closed.consent:\n  kind: closed\n  template: 동의가 필요해요.\n", encoding="utf-8"
        )
        text = ReadoutCatalog.from_yaml(path).get("closed.consent")
        assert (text.key, text.kind, text.template) == (
            "closed.consent",
            "closed",
            "동의가 필요해요.",
        )

    @pytest.mark.parametrize(
        "body",
        [
            "",
            "a:\n  template: 가\n",  # kind 없음
            "a:\n  kind: closed\n  template: '  '\n",  # 빈 문구
            "a:\n  kind: closed\n  template: 가\n  extra: 1\n",  # 모르는 칸
            "a: 가\n",
        ],
    )
    def test_모양이_틀리면_실패한다(self, tmp_path, body):
        path = tmp_path / "x.readout.yaml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError):
            ReadoutCatalog.from_yaml(path)


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
