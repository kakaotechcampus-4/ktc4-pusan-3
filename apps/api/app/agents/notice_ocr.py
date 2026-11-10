"""기관 문서 사진 → 저장 전 읽기 결과. OCR 파이프라인은 Agent 가 아니다 (공통규약 §0).

대화 입력을 받지 않고, 저장도 하지 않는다. 읽은 결과는 run 에만 있다가 보호자가 저장을 눌렀을 때만
들어간다. 모델을 부르는 providers 와 규칙으로 조립하는 domains 를 여기서 잇는다 — api 는 providers
를 부르지 못한다.

한 장에 일정 · 회신 · 비용이 섞여 와서 공지 단위로 갈라 보내지 않는다. 식단표만 메뉴가 표로 있어
급식표 읽기를 한 번 더 부른다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.domains.notice.assemble import NewNotice, assemble
from app.domains.notice.models import NoticeDocKind, NoticeInputFormat
from app.providers.meal_plan.image_ocr import (
    ImageOcrMealPlanReader,
    ReadDeadlineExceeded,
    merge_outputs,
)
from app.providers.meal_plan.schema import MealPlanJSON
from app.providers.model_errors import MODEL_CALL_FAILURES
from app.providers.notice.document_text import UnreadableDocument, normalize_image
from app.providers.notice.extractor import NoticeExtractor
from app.rules.meal_plan_month import decide_year_month
from app.rules.menu_key import menu_display_name
from app.rules.notice_text import clean_text

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
MIN_TRANSCRIPT_CHARS = 10


class UnreadablePhoto(ValueError):
    """글자를 찾지 못한 사진. 빈 결과로 넘기지 않고 run 을 실패로 끝낸다."""


@dataclass(frozen=True)
class PhotoReading:
    notice: NewNotice
    meal_plan: MealPlanJSON | None
    meal_failed: bool = False  # 식단표인데 표를 못 읽었다. 공지 항목은 살아 있다
    meal_timed_out: bool = False  # 못 읽은 이유가 시간이다 — 사진을 다시 찍으라고 하지 않는다


def read_photo(
    data: bytes, *, received_at: datetime, meal_deadline: float | None = None
) -> PhotoReading:
    """동기 호출이다 — 모델을 기다리므로 스레드에서 부른다.

    meal_deadline(`time.monotonic()` 기준)이 지나면 식단표의 표 읽기를 멈추고 공지 항목만 낸다.
    시간을 쓰는 쪽이 표 읽기라서, 전체가 timeout 으로 끝나기 전에 읽은 것을 건진다 (#934 리뷰).
    """
    try:
        image, mime_type = normalize_image(data)
    except UnreadableDocument as e:
        raise UnreadablePhoto(e.reason) from e

    extractor = NoticeExtractor.from_settings(settings, max_calls=1)
    try:
        extract = extractor.read_image(image, mime_type=mime_type)
    except MODEL_CALL_FAILURES as e:
        # 글자가 많아 출력 상한에 걸리거나, 모델 호출이 시간 초과 · 429 · 인증으로 실패하면 나온다.
        # 서버 오류가 아니라 "읽지 못했어요" 다 — 같은 사진을 다시 올리면 된다
        log.warning("공지 읽기 실패 (%s)", type(e).__name__)
        raise UnreadablePhoto("읽지 못했어요") from e
    source = clean_text(extract.transcript)
    if len(source) < MIN_TRANSCRIPT_CHARS:
        raise UnreadablePhoto("글자를 찾지 못했어요")
    notice = assemble(
        extract,
        source_text=source,
        input_format=NoticeInputFormat.IMAGE,
        received_at=received_at,
        tz=KST,
        extractor_version=extractor.version,
    )
    if notice.doc_kind is not NoticeDocKind.MEAL_PLAN:
        return PhotoReading(notice=notice, meal_plan=None)
    reader = ImageOcrMealPlanReader.from_settings(settings, max_calls=2, deadline=meal_deadline)
    try:
        parts = reader.read_parts(image, mime_type=mime_type)
        # 🚨 연도는 모델에게 묻지 않는다 (§2 · §3).
        #    월은 모델이 읽은 것, 연도는 사진 글자 또는 받은 날이다
        decided = decide_year_month(
            (part.year_month for part in parts.outputs),
            transcript=source,
            received_on=received_at.astimezone(KST).date(),
        )
        if decided is None:
            # 월을 못 읽었거나 두 호출의 월이 다르다 — 어느 달인지 모르면 날짜를 하나도 쓸 수 없다
            log.warning("meal_plan 의 월을 정하지 못했다 — 공지 항목만 낸다")
            return PhotoReading(notice=notice, meal_plan=None, meal_failed=True)
        year, month = decided
        plan = names_from_raw(merge_outputs(parts.outputs, year_month=f"{year:04d}-{month:02d}"))
        return PhotoReading(notice=notice, meal_plan=plan)
    except ReadDeadlineExceeded:
        log.warning("meal_plan 읽기가 기한 안에 끝나지 않았다 — 공지 항목만 낸다")
        return PhotoReading(notice=notice, meal_plan=None, meal_failed=True, meal_timed_out=True)
    except MODEL_CALL_FAILURES as e:
        # 성공한 것은 보여 준다 (CLAUDE.md §2 부분 결과). 메시지에 메뉴가 실릴 수 있어 종류만 남긴다
        # 식단표 호출만 시간 초과 · 429 로 실패해도 읽은 공지 항목까지 버리지 않는다
        log.warning("meal_plan 읽기 실패 — 공지 항목만 낸다 (%s)", type(e).__name__)
        return PhotoReading(notice=notice, meal_plan=None, meal_failed=True)


def names_from_raw(plan: MealPlanJSON) -> MealPlanJSON:
    """메뉴 이름을 모델의 `name` 대신 원문(`raw`)에서 코드로 다시 만든다 (#908).

    화면 제목과 저장 키가 이 이름으로 만들어진다(`photo_run._meal_entries` → `meal_row`).
    모델 이름에 찌꺼기가 붙은 적이 있어서(" Riot" · "/Optional?") 원문에서 번호 · 기호만 지운다.
    응답 스키마는 그대로 둔다 — `name` 을 빼면 프롬프트 지문이 바뀐다.
    급식표 eval(`scripts/meal_ocr_eval.py`)도 이 함수를 거쳐 운영과 같은 이름을 채점한다.
    """
    fixed = plan.model_copy(deep=True)
    for day in fixed.days:
        for meal in day.meals:
            for item in meal.items:
                item.name = menu_display_name(item.raw)
    return fixed
