"""scripts/notice_fixture_eval.py — 판정 로직 (네트워크 없음)."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.domains.notice.assemble import NewNotice, NewNoticeItem
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItemHandling,
    NoticeItemTarget,
)
from scripts import notice_fixture_eval
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
    ends: str | None = None,
    all_day: bool = False,
    due: str | None = None,
    due_all_day: bool = False,
    review: bool = False,
    quote: str | None = None,
) -> NewNoticeItem:
    return NewNoticeItem(
        handling=NoticeItemHandling(handling),
        label=title,
        title=title,
        target_scope=NoticeItemTarget.ALL,
        starts_at=_at(starts) if starts else None,
        ends_at=_at(ends) if ends else None,
        all_day=all_day,
        due_at=_at(due) if due else None,
        due_all_day=due_all_day,
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


def _notice(*items: NewNoticeItem, raw_text: str = "") -> NewNotice:
    return NewNotice(
        doc_kind=NoticeDocKind.NEWSLETTER,
        input_format=NoticeInputFormat.IMAGE,
        audience_scope=NoticeAudience.CLASS,
        issued_on=None,
        received_at=_at("2026-08-24T09:00"),
        title=None,
        raw_text=raw_text,
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


# ── 항목 찾기: 화면 글(제목 · 이름 · 딸린 정보)에서만 — 근거 문장은 여러 항목이 같이 인용한다 ──


def test_two_deadlines_quoted_together_are_judged_by_their_own_titles():
    # 모델이 3번 · 4번 문장을 한 번에 인용하면 두 항목의 근거 문장에 "신청" · "체험비" 가 다 든다
    quote = "3. 참가 신청서는 11월 9일(월)까지 제출해 주세요. 4. 체험비 5,000원은 11월 12일(목)까지"
    notice = _notice(
        _item("참가 신청서 제출", handling="todo", due="2026-11-09T23:59", quote=quote),
        _item("체험비 5,000원 납부", handling="todo", due="2026-11-12T23:59", quote=quote),
    )

    assert _check("G", "신청서 마감 11/9").judge(notice) is Outcome.PASS
    assert _check("G", "체험비 마감 11/12").judge(notice) is Outcome.PASS


def test_concert_date_ignores_the_application_todo():
    # 신청서 할 일이 마감 시각을 시작에도 갖고 있어도 음악회 일정의 날짜가 아니다
    notice = _notice(
        _item("가을 음악회", starts="2026-11-21T10:00"),
        _item(
            "음악회 참가 신청서 제출",
            handling="todo",
            starts="2026-11-10T18:00",
            due="2026-11-10T18:00",
        ),
    )

    assert _check("H", "음악회 11/21 10시").judge(notice) is Outcome.PASS


def test_health_check_deadline_ignores_a_result_sheet_titled_with_health_check():
    notice = _notice(
        _item("영유아 건강검진 받기", handling="todo", due="2026-09-30T23:59"),
        _item("영유아 건강검진 결과지 사본 제출", handling="todo", due="2026-10-16T23:59"),
    )

    assert _check("E", "검진 마감 9/30").judge(notice) is Outcome.PASS


def test_health_check_as_a_schedule_is_not_saved_by_the_result_sheet_todo():
    # 건강검진을 일정으로 잘못 나눴는데 결과지 제출(할 일)이 있다고 통과하면 안 된다
    notice = _notice(
        _item("영유아 건강검진", starts="2026-09-30T00:00", all_day=True),
        _item("검진 결과지 사본 제출", handling="todo", due="2026-10-16T23:59"),
    )

    assert _check("E", "건강검진이 할 일").judge(notice) is Outcome.FAIL


# ── 반별 일정: 어느 반이 어느 날인지까지 본다 ─────────────────────────────────


def test_each_class_must_be_on_its_own_day():
    # 근거 문장에는 세 반이 다 들어 있다 — 반 이름은 제목에서 찾는다
    quote = "1. 일정: 11월 17일(화) 해님반, 11월 18일(수) 달님반, 11월 19일(목) 별님반"
    check = _check("G", "반별 일정 11/17 · 18 · 19")
    right = _notice(
        _item("해님반 고구마 캐기 체험", starts="2026-11-17T10:00", quote=quote),
        _item("달님반 고구마 캐기 체험", starts="2026-11-18T10:00", quote=quote),
        _item("별님반 고구마 캐기 체험", starts="2026-11-19T10:00", quote=quote),
    )
    swapped = _notice(
        _item("해님반 고구마 캐기 체험", starts="2026-11-18T10:00", quote=quote),
        _item("달님반 고구마 캐기 체험", starts="2026-11-17T10:00", quote=quote),
        _item("별님반 고구마 캐기 체험", starts="2026-11-19T10:00", quote=quote),
    )

    assert check.judge(right) is Outcome.PASS
    assert check.judge(swapped) is Outcome.C1  # 해님반 보호자에게 틀린 날이 표시 없이 오른다


# ── 나오면 안 되는 날짜: 끝 날짜 · 안내 항목도 화면에 실린다 ───────────────────


def test_rain_date_as_the_end_of_the_concert_is_c1():
    # 11/21 에 시작해 11/28 에 끝나는 8일짜리 일정 — 우천 연기일이 끝 날짜로 들어갔다
    notice = _notice(_item("가을 음악회", starts="2026-11-21T10:00", ends="2026-11-28T23:59"))

    assert _check("H", "우천 연기일 11/28 이 일정이 안 됨").judge(notice) is Outcome.C1


def test_3am_on_an_info_item_is_c1():
    # 안내 항목은 확인 표시가 붙지 않고 시각이 화면에 그대로 실린다
    assert no_3am((_item("하원 요청", handling="info", starts="2026-11-02T03:00"),)) is Outcome.C1


# ── 시각만 비운 날짜: 보호자가 채운다 — 일반 실패 (§2 표시 없이 빈 값) ───────────


def test_right_day_without_a_time_is_general_failure_not_c1():
    concert = (_item("가을 음악회", starts="2026-11-21T00:00", all_day=True),)
    deadline = (_item("참가 신청서", handling="todo", due="2026-11-10T23:59", due_all_day=True),)

    assert on_date(concert, "음악회", "starts_at", "2026-11-21 10:00") is Outcome.FAIL
    assert on_date(deadline, "신청서", "due_at", "2026-11-10 18:00") is Outcome.FAIL


def test_wrong_day_without_a_time_is_still_c1():
    items = (_item("가을 음악회", starts="2026-11-28T00:00", all_day=True),)

    assert on_date(items, "음악회", "starts_at", "2026-11-21 10:00") is Outcome.C1


# ── 이름 가리기: 성 없이 부른 이름도 본다 ─────────────────────────────────────


def test_given_name_left_in_the_saved_text_fails_the_masking_check():
    check = _check("A", "아이 · 담임 이름 없음")
    left = _notice(raw_text="오늘 점심 후 하늘이가 37.8도 미열이 있습니다.")
    masked = _notice(raw_text="오늘 점심 후 [이름]이가 37.8도 미열이 있습니다.")

    assert check.judge(left) is Outcome.FAIL
    assert check.judge(masked) is Outcome.PASS


def test_daily_note_masking_check_finds_our_child_but_not_sky_blue():
    check = _check("B", "아이 · 담임 이름 없음")
    left = _notice(raw_text="하늘이는 [이름]이와 낙엽을 밟았어요. 내일 하늘색 여벌 옷")
    masked = _notice(raw_text="[이름]이는 [이름]이와 낙엽을 밟았어요. 내일 하늘색 여벌 옷")

    assert check.judge(left) is Outcome.FAIL
    assert check.judge(masked) is Outcome.PASS


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


# ── 실행: 모델을 부르지 않는 read_photo 로 main 을 돈다 ─────────────────────────


def _run_main(monkeypatch, tmp_path, read_photo, *argv: str) -> tuple[int, int]:
    """돌려주는 것: (종료 코드, 실행 메타를 쓴 횟수)."""
    wrote = []
    monkeypatch.setattr(settings, "MEAL_OCR_BASE_URL", "http://mlapi.test")
    monkeypatch.setattr(settings, "MEAL_OCR_API_KEY", "test-key")
    monkeypatch.setattr(notice_fixture_eval, "read_photo", read_photo)
    monkeypatch.setattr(notice_fixture_eval, "_write_meta", lambda args: wrote.append(args))
    return notice_fixture_eval.main(["--out", str(tmp_path), *argv]), len(wrote)


def test_repeat_beyond_the_call_budget_is_refused_before_any_read(monkeypatch, tmp_path):
    # 7장 × 50회 = 350회. 예산을 넘기면 팀 키가 삭제된다
    reads = []

    def read_photo(data, *, received_at):
        reads.append(received_at)
        raise RuntimeError("부르면 안 된다")

    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, read_photo, "--repeat", "50")

    assert reads == []


def test_when_every_read_fails_nothing_is_recorded(monkeypatch, tmp_path):
    # 읽기 실패는 일반 실패라 C1 0 으로 나온다 — 잰 것이 없는데 실행 메타가 남으면 안 된다
    def read_photo(data, *, received_at):
        raise RuntimeError("키가 틀렸다")

    assert _run_main(monkeypatch, tmp_path, read_photo) == (1, 0)


def test_reads_that_succeed_are_recorded(monkeypatch, tmp_path):
    def read_photo(data, *, received_at):
        return SimpleNamespace(notice=_notice())

    assert _run_main(monkeypatch, tmp_path, read_photo) == (0, 1)
