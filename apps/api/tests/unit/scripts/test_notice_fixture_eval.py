"""scripts/notice_fixture_eval.py — 판정 로직 (네트워크 없음)."""

from datetime import datetime

from app.domains.notice.assemble import NewNotice, NewNoticeItem
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItemHandling,
    NoticeItemTarget,
)
from scripts.notice_fixture_eval import (
    CHECKS,
    DOCS,
    FIXTURE_DIR,
    KST,
    Outcome,
    never_on,
    no_3am,
    no_diagnosis,
    on_date,
    summarize_runs,
)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def _item(
    title: str,
    *,
    handling: str = "calendar",
    starts: str | None = None,
    due: str | None = None,
    review: bool = False,
    quote: str | None = None,
) -> NewNoticeItem:
    return NewNoticeItem(
        handling=NoticeItemHandling(handling),
        label=title,
        title=title,
        target_scope=NoticeItemTarget.ALL,
        starts_at=_at(starts) if starts else None,
        ends_at=None,
        all_day=False,
        due_at=_at(due) if due else None,
        due_all_day=False,
        details=(),
        supplies=(),
        source_quote=quote or title,
        needs_review=review,
        review_reason="date" if review else None,
    )


# ── 날짜 판정: 표시 없이 틀린 날짜만 C1 (NOTICE_OCR_EVAL.md §2) ─────────────────


def test_right_date_passes():
    items = (_item("가을 음악회", starts="2026-11-21T10:00"),)

    assert on_date(items, "음악회", "starts_at", "2026-11-21 10:00") is Outcome.PASS


def test_wrong_date_without_review_flag_is_c1():
    items = (_item("가을 음악회", starts="2026-11-28T10:00"),)  # 우천 연기일을 일정으로

    assert on_date(items, "음악회", "starts_at", "2026-11-21") is Outcome.C1


def test_wrong_date_with_review_flag_is_general_failure():
    items = (_item("가을 음악회", starts="2026-11-28T10:00", review=True),)

    assert on_date(items, "음악회", "starts_at", "2026-11-21") is Outcome.FAIL


def test_missing_item_or_empty_date_is_general_failure():
    assert on_date((), "음악회", "starts_at", "2026-11-21") is Outcome.FAIL
    items = (_item("가을 음악회"),)  # 날짜를 비움 — 보호자가 채운다
    assert on_date(items, "음악회", "starts_at", "2026-11-21") is Outcome.FAIL


def test_any_matching_item_with_the_right_date_passes():
    items = (
        _item("음악회 참가 신청서", handling="todo", due="2026-11-10T18:00"),
        _item("가을 음악회", starts="2026-11-21T10:00"),
    )

    assert on_date(items, "음악회", "starts_at", "2026-11-21 10:00") is Outcome.PASS


def test_afternoon_deadline_read_as_morning_is_c1():
    items = (_item("참가 신청서", handling="todo", due="2026-11-10T06:00"),)

    assert on_date(items, "신청서", "due_at", "2026-11-10 18:00") is Outcome.C1


def test_date_that_must_not_be_a_schedule():
    rain = "2026-11-28"
    assert never_on((_item("음악회", starts="2026-11-28T10:00"),), "starts_at", rain) is Outcome.C1
    flagged = (_item("음악회", starts="2026-11-28T10:00", review=True),)
    assert never_on(flagged, "starts_at", rain) is Outcome.FAIL
    assert (
        never_on((_item("음악회", starts="2026-11-21T10:00"),), "starts_at", rain) is Outcome.PASS
    )


def test_diagnosis_words_in_items_are_c1():
    assert no_diagnosis((_item("미열이 있어 일찍 하원"),)) is Outcome.PASS
    assert no_diagnosis((_item("콧물이 조금 있었어요"),)) is Outcome.PASS
    assert no_diagnosis((_item("감기 의심 — 하원"),)) is Outcome.C1
    for disease in ("비염", "장염", "중이염", "수족구", "코로나", "폐렴"):
        assert no_diagnosis((_item(f"{disease} 증상으로 하원"),)) is Outcome.C1, disease


def test_unflagged_wrong_duplicate_is_c1_even_if_another_item_is_right():
    # 같은 일정이 두 번 나오고 하나만 틀려도 틀린 쪽이 표시 없이 일정 초안에 오른다
    items = (
        _item("부모 상담 주간", starts="2026-11-23T00:00"),
        _item("부모 상담", starts="2026-11-24T00:00"),
    )

    assert on_date(items, "상담", "starts_at", "2026-11-23") is Outcome.C1


def test_flagged_3am_on_one_field_is_a_general_failure_not_a_pass():
    flagged = (_item("하원 요청", handling="todo", due="2026-11-02T03:00", review=True),)
    unflagged = (_item("하원 요청", handling="todo", due="2026-11-02T03:00"),)

    assert no_3am(flagged) is Outcome.FAIL
    assert no_3am(unflagged) is Outcome.C1
    assert no_3am((_item("하원 요청", handling="todo", due="2026-11-02T15:00"),)) is Outcome.PASS


def _notice(*items: NewNoticeItem) -> NewNotice:
    return NewNotice(
        doc_kind=NoticeDocKind.NEWSLETTER,
        input_format=NoticeInputFormat.IMAGE,
        audience_scope=NoticeAudience.CLASS,
        issued_on=None,
        received_at=_at("2026-08-24T09:00"),
        title=None,
        raw_text="",
        content_hash="",
        is_correction=False,
        items=items,
    )


def _check(doc: str, name: str):
    return next(c for c in CHECKS if (c.doc, c.name) == (doc, name))


def test_health_check_deadline_ignores_the_result_sheet_item():
    # "검진 결과지 사본은 10월 16일까지" 도 "검진" 을 담는다 — 건강검진 마감이 비었는데
    # 결과지 마감(맞는 날짜)을 검진 마감의 틀린 날짜로 읽으면 안 된다
    notice = _notice(
        _item("영유아 건강검진", handling="todo", quote="9월 내로 영유아 건강검진을 받아"),
        _item(
            "검진 결과지 제출",
            handling="todo",
            due="2026-10-16T23:59",
            quote="검진 결과지 사본은 10월 16일(금)까지",
        ),
    )

    assert _check("E", "검진 마감 9/30").judge(notice) is Outcome.FAIL
    assert _check("E", "결과지 마감 10/16").judge(notice) is Outcome.PASS


# ── 반복 요약 ────────────────────────────────────────────────────────────────


def test_summary_counts_only_c1_checks_in_the_ratio():
    c1_check, plain_check = ("H", "음악회 날짜"), ("H", "학원 이름 없음")
    runs = [
        {c1_check: Outcome.C1, plain_check: Outcome.FAIL},
        {c1_check: Outcome.PASS, plain_check: Outcome.FAIL},
    ]

    s = summarize_runs(runs, c1_checks={c1_check})

    assert (s.c1_cases, s.total) == (1, 1)
    assert s.unstable == [c1_check]
    assert s.stable_wrong == [plain_check]  # 일반 판정도 안정 오답 · 불안정에는 들어간다


# ── 평가셋 ───────────────────────────────────────────────────────────────────


def test_evalset_is_seven_made_up_documents_with_images():
    assert len(DOCS) == 7
    for doc in DOCS:
        assert (FIXTURE_DIR / f"{doc.file}.png").is_file()
        assert (FIXTURE_DIR / f"{doc.file}.html").is_file()


def test_every_document_has_a_c1_check_and_names_are_unique():
    keys = [(c.doc, c.name) for c in CHECKS]
    assert len(keys) == len(set(keys))
    assert {d.id for d in DOCS} == {c.doc for c in CHECKS if c.c1}
