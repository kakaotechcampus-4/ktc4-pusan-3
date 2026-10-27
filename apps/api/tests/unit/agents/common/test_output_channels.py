"""출력 채널 다섯의 계약.

docs/agents/shared/Agent_공통규약.md §3 · §10
"""

import inspect
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
    DomainPending,
    EventRequest,
    MedicationDraft,
)
from app.agents.common.suggestion import (
    MAX_SUGGESTIONS,
    BundleSummary,
    SuggestionDraft,
    SuggestionRejected,
    build,
    bundle_summary,
    cannot_resume_notice,
    check_count,
    count_notice,
    empty_notice,
    merge_drafts,
)

KST = timezone(timedelta(hours=9))
TODAY = date(2026, 10, 14)


def evidence(*, kind="observation_food", polarity=1, label="당근", observed_on=date(2026, 9, 20)):
    return RankedEvidence(
        ref=Ref(kind=kind, id=uuid4()),
        tier=1,
        label=label,
        polarity=polarity,
        observed_on=observed_on,
    )


def citation(*, kind="observation_food", polarity=1, label="당근", note="지난주에 두 번 먹었다"):
    return cite(
        evidence(kind=kind, polarity=polarity, label=label),
        note=note,
    )


def general_drafts(count):
    """문서 행만 인용한 일반 추천 count 개. 개수 검사는 내용을 보지 않는다."""
    return tuple(
        build(
            agent="food",
            content=f"{i}",
            reason="",
            citations=(_doc(),),
            general_reason="또래 기준",
            today=TODAY,
        )
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
            today=TODAY,
        )
        assert draft.kind == "personalized"
        assert len(draft.citations) == 1

    def test_아이_기록도_문서도_없으면_거절한다(self):
        """일반 추천은 문서 행이 근거다 (루트 §2). 둘 다 없으면 근거 없는 추천이다."""
        with pytest.raises(SuggestionRejected, match="문서 행 근거"):
            build(
                agent="food",
                content="계란말이",
                reason="모델이 쓴 개인화 문장",
                citations=(),
                general_reason="또래 아이들이 많이 먹는 메뉴예요.",
                today=TODAY,
            )

    def test_문서_행을_인용한_일반_추천은_이유를_덮어쓴다(self):
        doc = citation(kind="food_doc", polarity=0, label="")
        draft = build(
            agent="food",
            content="계란말이",
            reason="모델이 쓴 개인화 문장",
            citations=(doc,),
            general_reason="또래 아이들이 많이 먹는 메뉴예요.",
            today=TODAY,
        )
        assert draft.kind == "general"
        assert draft.reason == "또래 아이들이 많이 먹는 메뉴예요."
        assert draft.citations == (doc,)

    def test_문서_행만_있으면_일반_추천(self):
        draft = build(
            agent="growth",
            content="숫자 세기 놀이",
            reason="모델이 쓴 문장",
            citations=(citation(kind="growth_doc", polarity=0, label=""),),
            general_reason="또래 아이들이 많이 하는 놀이예요.",
            today=TODAY,
        )
        assert draft.kind == "general"
        # 문서 행은 kind 를 바꾸지 않지만 인용은 그대로 실려 나간다
        assert len(draft.citations) == 1

    def test_근거_0행인데_일반_문구가_없으면_거절(self):
        with pytest.raises(SuggestionRejected, match="일반 추천 문구"):
            build(agent="food", content="계란말이", reason="이유", citations=(), today=TODAY)

    def test_개인화인데_이유가_비면_거절(self):
        with pytest.raises(SuggestionRejected, match="이유가 비었다"):
            build(
                agent="food", content="계란말이", reason="   ", citations=(citation(),), today=TODAY
            )


def _old(on=date(2026, 3, 1)):
    """TODAY 기준 6개월 넘은 확정 관심을 인용한다."""
    stale = evidence(kind="profile_affinity", label="딸기", observed_on=on)
    return cite(stale, note="딸기를 자주 찾았다")


def _doc():
    return EvidenceCitation(ref=Ref(kind="food_doc", id=uuid4()), note="질감 안내")


def _draft_of(*citations):
    return build(
        agent="food",
        content="딸기 푸딩",
        reason="딸기를 좋아해서요",
        citations=citations,
        general_reason="또래 기준",
        today=TODAY,
    )


class TestStaleEvidence:
    """6개월 넘은 아이 기록은 단독 근거로 쓰지 않는다 (루트 §2 · NF-08 · #329)."""

    def test_6개월_지난_아이_기록만_인용하면_일반_추천이고_그_인용은_빠진다(self):
        doc = _doc()
        draft = _draft_of(_old(), doc)
        assert draft.kind == "general"
        assert draft.reason == "또래 기준"
        assert draft.citations == (doc,)  # 문서 행은 남고 아이 기록은 빠진다

    def test_오래된_근거와_최근_근거를_같이_인용하면_개인화이고_둘_다_남는다(self):
        old, fresh = _old(), citation()
        draft = _draft_of(old, fresh)
        assert draft.kind == "personalized"
        assert draft.citations == (old, fresh)

    @pytest.mark.parametrize(
        ("on", "kind"), [(date(2026, 4, 14), "general"), (date(2026, 4, 15), "personalized")]
    )
    def test_6개월은_달력으로_센다(self, on, kind):
        assert _draft_of(_old(on), _doc()).kind == kind

    def test_날짜를_모르는_아이_기록은_개인화_근거로_세지_않는다(self):
        undated = EvidenceCitation(ref=Ref(kind="observation_food", id=uuid4()), note="먹었다")
        assert _draft_of(undated, _doc()).kind == "general"

    def test_cite_는_근거의_날짜를_옮기고_payload_에는_싣지_않는다(self):
        item = citation()
        assert item.observed_on == date(2026, 9, 20)
        assert set(item.to_payload()) == {"source_kind", "source_id", "note"}

    def test_build_의_today_는_기본값이_없다(self):
        # 기본값이 있으면 날짜를 안 넘긴 호출이 조용히 6개월 판정을 건너뛴다
        today = inspect.signature(build).parameters["today"]
        assert today.default is inspect.Parameter.empty


class TestNoteRequired:
    """`note` 가 비면 그 후보만 뺀다. 묶음은 거절하지 않는다."""

    def test_note_가_비면_그_후보를_거절한다(self):
        with pytest.raises(SuggestionRejected, match="note"):
            build(
                agent="food",
                content="계란말이",
                reason="계란을 잘 먹어서요",
                citations=(citation(note="   "),),
                today=TODAY,
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
                today=TODAY,
            )


class TestAvoidanceMustBeStated:
    def test_기피를_인용했으면_이유에_밝혀야_한다(self):
        with pytest.raises(SuggestionRejected, match="무엇을 피했는지"):
            build(
                agent="food",
                content="오므라이스",
                reason="단백질이 들어 있어요",
                citations=(citation(polarity=-1, label="브로콜리"),),
                today=TODAY,
            )

    def test_관찰은_문장_label_대신_대상_subject_를_말해도_통과(self):
        """관찰의 label 은 활동 문장이라 이유에 그대로 나오지 않는다 (Activity 라이브 eval)."""
        ranked = RankedEvidence(
            ref=Ref(kind="observation_activity", id=uuid4()),
            tier=3,
            label="물감 놀이 하다가 울었음",
            subject="물감",
            polarity=-1,
            observed_on=date(2026, 10, 5),
        )
        draft = build(
            agent="activity",
            content="종이컵 탑 쌓기",
            reason="물감은 피해서 종이컵으로 골랐어요",
            citations=(cite(ranked, note="물감 놀이를 싫어했어요"),),
            today=TODAY,
        )
        assert draft.kind == "personalized"
        with pytest.raises(SuggestionRejected, match="무엇을 피했는지"):
            build(
                agent="activity",
                content="종이컵 탑 쌓기",
                reason="집에서 바로 할 수 있어요",
                citations=(cite(ranked, note="물감 놀이를 싫어했어요"),),
                today=TODAY,
            )

    @pytest.mark.parametrize(
        ("reason", "stated"),
        [
            ("물감으로 손도장을 찍어 봐요", False),  # "물" 이 들어 있을 뿐 다른 낱말이다
            ("물가 대신 모래밭에서 놀아요", False),
            ("물은 피해서 모래로 골랐어요", True),
            ("물놀이를 싫어해서 모래로 골랐어요", True),
            ("물 대신 모래로 골랐어요", True),
        ],
    )
    def test_한_글자_대상은_낱말로만_센다(self, reason, stated):
        """#305 · #341 의 한 글자 이름 문제와 같다 (#359 리뷰). 두 글자 이상은 그대로다."""
        ranked = RankedEvidence(
            ref=Ref(kind="profile_affinity", id=uuid4()),
            tier=1,
            label="물",
            subject="물",
            polarity=-1,
            observed_on=date(2026, 10, 5),
        )
        citations = (cite(ranked, note="물을 싫어해요"),)
        if stated:
            assert build(
                agent="activity",
                content="모래 놀이",
                reason=reason,
                citations=citations,
                today=TODAY,
            )
        else:
            with pytest.raises(SuggestionRejected, match="무엇을 피했는지"):
                build(
                    agent="activity",
                    content="모래 놀이",
                    reason=reason,
                    citations=citations,
                    today=TODAY,
                )

    def test_밝히면_통과(self):
        draft = build(
            agent="food",
            content="오므라이스",
            reason="브로콜리를 기피해서 계란 맛이 강한 걸로 골랐어요",
            citations=(citation(polarity=-1, label="브로콜리"),),
            today=TODAY,
        )
        assert draft.kind == "personalized"

    def test_기피가_없으면_검사하지_않는다(self):
        draft = build(
            agent="food",
            content="계란말이",
            reason="계란을 잘 먹어서요",
            citations=(citation(polarity=1, label="계란"),),
            today=TODAY,
        )
        assert draft.kind == "personalized"


class TestSuggestionLists:
    """`allergens` · `items` 는 판정에 관여하지 않고 그대로 실린다.

    채우는 쪽은 각 Agent 의 출력 tool 이다.
    """

    def test_기본은_빈_목록(self):
        draft = build(
            agent="food",
            content="된장찌개",
            reason="",
            citations=(_doc(),),
            general_reason="또래 기준",
            today=TODAY,
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
            citations=(_doc(),),
            general_reason="또래 기준",
            allergens=("대두",),
            items=("된장", "두부"),
            today=TODAY,
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
            today=TODAY,
        )
        assert draft.kind == "personalized"
        assert draft.items == ("수영복",)


class TestCount:
    """추천은 최대 3개 (Tool_공통.md §5-2). 더 채울 길이가 없으면 0~2개도 통과한다."""

    def test_3개면_통과(self):
        check_count(general_drafts(MAX_SUGGESTIONS))

    @pytest.mark.parametrize("count", [0, 1, 2, 3])
    def test_더_채울_길이_없으면_0개부터_통과(self, count):
        """0개도 예외 없이 통과한다. 호출부가 0개를 따로 거르지 않아도 count_notice 로 끝난다.

        예외로 두면 호출부가 놓쳤을 때 pipeline 까지 올라가 Agent 실패로 세진다 (#245 리뷰).
        """
        check_count(general_drafts(count), exhausted=True)

    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_채울_수_있는데_모자라면_거절(self, count):
        """여기서 통과시키면 더 채우지 않고 끝나서, "최대" 가 "아무 개수나" 가 된다."""
        with pytest.raises(SuggestionRejected, match="채울 수 있는데"):
            check_count(general_drafts(count))

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
        assert notice.code == "fewer"

    def test_0개면_추천이_없다고_알린다(self):
        notice = count_notice(0)
        assert notice is not None
        assert notice.body == "조건에 맞는 추천이 없어요."
        assert notice.kind == "notice"
        assert notice.authored_by == "code"
        assert notice.code == "empty"

    def test_이어서_할_수_없음_안내는_코드_문구다(self):
        notice = cannot_resume_notice()
        assert notice.code == "cannot_resume"
        assert notice.authored_by == "code"
        assert notice.body == "이전 요청을 이어서 준비할 수 없어요. 홈에서 다시 골라 주세요."

    def test_0개_문구가_따로_있는_Agent_는_그_문구를_쓴다(self):
        notice = count_notice(0, empty=self.POOL_EMPTY)
        assert notice is not None
        assert notice.body == "조건에 맞는 메뉴가 없어요."
        assert notice.code == "empty"  # 문구가 달라도 화면은 code 로 가른다

    def test_0개_문구는_모자랄_때_쓰지_않는다(self):
        notice = count_notice(2, empty=self.POOL_EMPTY)
        assert notice is not None
        assert notice.body == "조건에 맞는 추천을 2개 준비했어요."
        assert notice.code == "fewer"

    def test_개수와_상관없는_안내는_code_가_없다(self):
        """날씨 안내도 kind="notice" 다. 개수 안내와 섞여 와도 code 로 가른다."""
        weather = ReadoutText(
            key="weather.unchecked",
            template="날씨를 확인하지 못해 실내 놀이만 골랐어요.",
            kind="notice",
        ).render()
        assert weather.code is None
        assert weather.to_payload()["code"] is None

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
        assert result.pending is None
        assert result.model_calls == 0

    def test_pending_이면_needs_observation_이_같은_질문이다(self):
        pending = DomainPending(
            agent="health",
            kind="medication_stop",
            question="항생제 복약 알림을 멈출까요?",
            payload={"schedule_id": "s1"},
        )

        result = DomainAgentResult(
            agent="health",
            task_type="medication",
            status="completed",
            needs_observation=(pending.question,),
            pending=pending,
        )

        assert result.pending is pending

    @pytest.mark.parametrize("asked", [(), ("다른 질문이에요",)])
    def test_pending_의_질문과_되묻기가_다르면_거절(self, asked):
        pending = DomainPending(agent="health", kind="dose_timing", question="언제요?", payload={})

        with pytest.raises(ValueError, match="pending"):
            DomainAgentResult(
                agent="health",
                task_type="medication",
                status="completed",
                needs_observation=asked,
                pending=pending,
            )

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


class TestMergeDrafts:
    """안전 필터 뒤 재호출 결과 합치기 (#245) — 처음 것 먼저, 빈자리만, 같은 것은 건너뛴다."""

    @staticmethod
    def drafts(*contents: str) -> tuple[SuggestionDraft, ...]:
        return tuple(
            SuggestionDraft(agent="activity", kind="general", content=c, reason="r")
            for c in contents
        )

    @staticmethod
    def content(draft: SuggestionDraft) -> str:
        return draft.content

    def test_처음_것을_먼저_두고_빈자리만_채운다(self):
        merged = merge_drafts(
            self.drafts("가", "나"), self.drafts("다", "라", "마"), key=self.content
        )
        assert [d.content for d in merged] == ["가", "나", "다"]

    def test_같은_추천은_Agent_가_넘긴_key_로_가린다(self):
        """Tool_공통 §5-2 — 무엇이 같은 추천인지는 Agent 가 정한다 (Food 는 menu_key)."""

        def no_spaces(draft: SuggestionDraft) -> str:
            return "".join(draft.content.split())

        first, again = self.drafts("종이컵 탑", "나"), self.drafts("종이컵탑", "다")
        assert [d.content for d in merge_drafts(first, again, key=no_spaces)] == [
            "종이컵 탑",
            "나",
            "다",
        ]
        assert len(merge_drafts(first, again, key=self.content)) == MAX_SUGGESTIONS

    def test_딸린_값은_짝으로_넘기면_순서가_같이_간다(self):
        first = tuple(zip(self.drafts("가"), [("경고",)], strict=True))
        again = tuple(zip(self.drafts("가", "나"), [(), ("다른 경고",)], strict=True))
        merged = merge_drafts(first, again, key=lambda pair: pair[0].content)
        assert [(d.content, w) for d, w in merged] == [("가", ("경고",)), ("나", ("다른 경고",))]

    def test_재호출이_비면_처음_것만(self):
        assert [d.content for d in merge_drafts(self.drafts("가"), (), key=self.content)] == ["가"]

    def test_최대_개수를_넘기지_않는다(self):
        merged = merge_drafts(self.drafts("가", "나", "다"), self.drafts("라"), key=self.content)
        assert len(merged) == MAX_SUGGESTIONS


class TestBundleSummary:
    """04 Agent 블록 머리의 묶음 칸 (#227). 모델을 부르지 않고 코드로 만든다."""

    def test_추천이_없으면_칸이_비어_있다(self):
        assert bundle_summary(()) == BundleSummary(kind=None, reason=None)

    def test_개인화는_근거로_쓴_아이_기록_수로_말한다(self):
        shared = citation()
        first = build(
            agent="food",
            content="계란말이",
            reason="r1",
            citations=(shared, citation(label="두부")),
            today=TODAY,
        )
        second = build(
            agent="food", content="두부조림", reason="r2", citations=(shared,), today=TODAY
        )

        # 같은 기록(shared)은 한 번만 센다 — 2건
        assert bundle_summary((first, second)) == BundleSummary(
            kind="personalized", reason="아이 기록 2건을 보고 골랐어요."
        )

    def test_일반은_Agent_의_코드_문구를_쓴다(self):
        assert bundle_summary(general_drafts(2)) == BundleSummary(
            kind="general", reason="또래 기준"
        )

    def test_개인화와_일반이_섞이면_개인화로_보고_개인화_근거만_센다(self):
        personal = build(
            agent="food", content="계란말이", reason="r", citations=(citation(),), today=TODAY
        )

        # 일반 추천의 문서 행 근거는 아이 기록이 아니라 세지 않는다
        assert bundle_summary((personal, *general_drafts(2))) == BundleSummary(
            kind="personalized", reason="아이 기록 1건을 보고 골랐어요."
        )


class TestEmptyNotice:
    """0개 안내는 `code` 로 찾는다 — Food 의 pool.empty 처럼 Agent 마다 문구가 다르다."""

    def test_0개_안내를_찾는다(self):
        weather = Readout(kind="notice", body="오늘은 맑아요.", authored_by="code")
        assert empty_notice((weather, count_notice(0))) == count_notice(0)

    def test_없으면_None(self):
        assert empty_notice((count_notice(2),)) is None
        assert empty_notice(()) is None


# ── Food 일반 추천의 메뉴 근거 (#442) ────────────────────────────────────
def _menu(note: str = "농식품정보원 레시피로 재료를 확인한 메뉴예요.") -> EvidenceCitation:
    return EvidenceCitation(ref=Ref(kind="recipe_catalog", id=uuid4()), note=note, label="계란말이")


class TestGeneralBasis:
    """`general_basis` 는 일반 추천일 때만 붙는 근거다. Food 는 고른 메뉴 자체를 넘긴다."""

    def test_메뉴_근거_종류는_규칙_모듈과_같다(self):
        from app.rules.menu_evidence import MENU_EVIDENCE_KIND

        assert get_args(refs.MenuKind) == (MENU_EVIDENCE_KIND,)

    def test_메뉴_근거는_개인화_근거로_세지_않는다(self):
        assert _menu().ref.is_child_record is False

    def test_문서_행이_없어도_메뉴_근거만으로_일반_추천이다(self):
        menu = _menu()
        draft = build(
            agent="food",
            content="계란말이",
            reason="모델이 쓴 문장",
            citations=(),
            general_reason="또래 아이들이 많이 먹는 메뉴예요.",
            general_basis=(menu,),
            today=TODAY,
        )

        assert draft.kind == "general"
        assert draft.citations == (menu,)

    def test_문서_행을_인용했으면_메뉴_근거와_같이_남는다(self):
        doc, menu = citation(kind="food_doc", polarity=0, label=""), _menu()
        draft = build(
            agent="food",
            content="계란말이",
            reason="모델이 쓴 문장",
            citations=(doc,),
            general_reason="또래 아이들이 많이 먹는 메뉴예요.",
            general_basis=(menu,),
            today=TODAY,
        )

        assert draft.citations == (doc, menu)

    def test_개인화_추천에는_메뉴_근거를_붙이지_않는다(self):
        record = citation()
        draft = build(
            agent="food",
            content="당근전",
            reason="당근을 잘 먹어서 골랐어요",
            citations=(record,),
            general_reason="또래 아이들이 많이 먹는 메뉴예요.",
            general_basis=(_menu(),),
            today=TODAY,
        )

        assert draft.kind == "personalized"
        assert draft.citations == (record,)

    def test_메뉴_근거도_note_가_필요하다(self):
        with pytest.raises(SuggestionRejected, match="note"):
            build(
                agent="food",
                content="계란말이",
                reason="",
                citations=(),
                general_reason="또래 아이들이 많이 먹는 메뉴예요.",
                general_basis=(_menu(note=" "),),
                today=TODAY,
            )

    def test_메뉴_근거가_없으면_여전히_문서_행이_필요하다(self):
        # Activity · Growth 는 general_basis 를 넘기지 않는다 — 지금처럼 문서 행이 근거다
        with pytest.raises(SuggestionRejected, match="근거가 없다"):
            build(
                agent="activity",
                content="블록 쌓기",
                reason="",
                citations=(),
                general_reason="또래 아이들이 많이 하는 놀이예요.",
                today=TODAY,
            )


def _book(note: str = "도서관 정보나루 검색 결과에 있는 책이에요.") -> EvidenceCitation:
    return EvidenceCitation(ref=Ref(kind="book_catalog", id=uuid4()), note=note, label="책1")


class TestBookBasis:
    """Growth 일반 도서 추천의 근거는 고른 책 자체다 — Food 메뉴(#442)처럼 `general_basis` 로만
    붙는다."""

    def test_도서_근거_종류는_규칙_모듈과_같고_문서_행이_아니다(self):
        from app.rules.book_evidence import BOOK_EVIDENCE_KIND

        assert get_args(refs.BookKind) == (BOOK_EVIDENCE_KIND,)
        assert BOOK_EVIDENCE_KIND not in get_args(DocKind)  # 문서 행 = 검수된 시드 전제를 지킨다

    def test_도서_근거는_개인화_근거로_세지_않는다(self):
        assert _book().ref.is_child_record is False

    def test_기록이_없으면_책_근거만으로_일반_추천이다(self):
        book = _book()
        draft = build(
            agent="growth",
            content="책1(가)",
            reason="모델이 쓴 이유",
            general_reason="일반 문구",
            general_basis=(book,),
            today=TODAY,
        )

        assert draft.kind == "general" and draft.reason == "일반 문구"
        assert draft.citations == (book,)

    def test_개인화면_책_근거를_버린다(self):
        draft = build(
            agent="growth",
            content="책1(가)",
            reason="공룡 그림을 오래 봐서요",
            citations=(citation(kind="observation_education"),),
            general_reason="일반 문구",
            general_basis=(_book(),),
            today=TODAY,
        )

        assert draft.kind == "personalized"
        assert [c.ref.kind for c in draft.citations] == ["observation_education"]
