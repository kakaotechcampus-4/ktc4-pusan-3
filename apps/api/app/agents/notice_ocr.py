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
from app.providers.meal_plan.image_ocr import ImageOcrMealPlanReader
from app.providers.meal_plan.schema import MealPlanJSON
from app.providers.model_errors import MODEL_CALL_FAILURES
from app.providers.notice.document_text import UnreadableDocument, normalize_image
from app.providers.notice.extractor import NoticeExtractor
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


def read_photo(data: bytes, *, received_at: datetime) -> PhotoReading:
    """동기 호출이다 — 모델을 기다리므로 스레드에서 부른다."""
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
    reader = ImageOcrMealPlanReader.from_settings(settings, max_calls=2)
    try:
        return PhotoReading(notice=notice, meal_plan=reader.read(image, mime_type=mime_type))
    except MODEL_CALL_FAILURES as e:
        # 성공한 것은 보여 준다 (CLAUDE.md §2 부분 결과). 메시지에 메뉴가 실릴 수 있어 종류만 남긴다
        # 식단표 호출만 시간 초과 · 429 로 실패해도 읽은 공지 항목까지 버리지 않는다
        log.warning("meal_plan 읽기 실패 — 공지 항목만 낸다 (%s)", type(e).__name__)
        return PhotoReading(notice=notice, meal_plan=None, meal_failed=True)
