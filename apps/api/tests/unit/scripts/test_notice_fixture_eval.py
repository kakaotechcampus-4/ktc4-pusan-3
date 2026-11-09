"""scripts/notice_fixture_eval.py — 판정 로직 (네트워크 없음)."""

from datetime import datetime

from app.domains.notice.assemble import NewNoticeItem
from app.domains.notice.models import NoticeItemHandling, NoticeItemTarget
from scripts.notice_fixture_eval import (
    CHECKS,
    DOCS,
    FIXTURE_DIR,
    KST,
    Outcome,
    never_on,
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
        source_quote=title,
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
    assert no_diagnosis((_item("감기 의심 — 하원"),)) is Outcome.C1


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
