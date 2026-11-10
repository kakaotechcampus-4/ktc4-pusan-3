"""사진 읽기 — 급식표 메뉴 이름은 모델의 `name` 이 아니라 원문(`raw`)에서 코드가 만든다 (#908).

모델 이름 끝에 찌꺼기가 붙어도(" Riot" · "/Optional?" · `}{"`) 원문은 맞게 읽혔다. 화면 제목과
저장 키가 이 이름으로 만들어지므로(`photo_run._meal_entries` → `meal_row`) 읽기 결과에서 고친다.
입력은 저장소 정답셋 급식표(2025-01)를 읽은 모델 출력에서 깨진 항목만 옮겼다.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agents import notice_ocr
from app.domains.notice.models import NoticeDocKind
from app.providers.meal_plan.image_ocr import OcrOutput, ReadParts
from app.providers.meal_plan.schema import MealPlanJSON, MealPlanSource

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _plan() -> MealPlanJSON:
    def item(raw: str, name: str) -> dict:
        return {"raw": raw, "name": name}

    return MealPlanJSON.model_validate(
        {
            "source": MealPlanSource.IMAGE,
            "year_month": "2025-01",
            "days": [
                {
                    "day": 16,
                    "meals": [
                        {
                            "meal_type": "lunch",
                            "items": [
                                item("흑미밥", "흑미밥"),
                                item("백깍두기9(백김치9)", "백깍두기(백김치Base/Optional?)"),
                            ],
                        }
                    ],
                },
                {
                    "day": 17,
                    "meals": [
                        {
                            "meal_type": "snack_pm",
                            "items": [item("떡(꿀설기)", '떡(꿀설기)}{"'), item("우유2", "우유")],
                        }
                    ],
                },
                {
                    "day": 3,
                    "meals": [
                        {
                            "meal_type": "snack_pm",
                            "items": [item("떡(증편),", "떡(증편 Riot")],
                        }
                    ],
                },
            ],
        }
    )


class _Extractor:
    version = "test"

    def read_image(self, image: bytes, *, mime_type: str) -> SimpleNamespace:
        return SimpleNamespace(transcript="2025년 1월 영양식단 — 지어낸 글자 스무 자 이상")


class _MealReader:
    def read_parts(self, image: bytes, *, mime_type: str) -> ReadParts:
        output = OcrOutput.model_validate(_plan().model_dump(exclude={"source"}))
        return ReadParts(outputs=(output,))


@pytest.fixture
def reading(monkeypatch: pytest.MonkeyPatch) -> notice_ocr.PhotoReading:
    monkeypatch.setattr(notice_ocr, "normalize_image", lambda data: (data, "image/png"))
    monkeypatch.setattr(
        notice_ocr.NoticeExtractor,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _Extractor()),
    )
    monkeypatch.setattr(
        notice_ocr.ImageOcrMealPlanReader,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _MealReader()),
    )
    monkeypatch.setattr(
        notice_ocr,
        "assemble",
        lambda extract, **kwargs: SimpleNamespace(doc_kind=NoticeDocKind.MEAL_PLAN),
    )
    return notice_ocr.read_photo(b"image", received_at=NOW)


def _names(plan: MealPlanJSON) -> dict[tuple[int, str], list[str]]:
    return {
        (d.day, m.meal_type.value): [it.name for it in m.items] for d in plan.days for m in d.meals
    }


def test_메뉴_이름은_원문에서_만든다(reading: notice_ocr.PhotoReading) -> None:
    assert reading.meal_plan is not None
    assert _names(reading.meal_plan) == {
        (16, "lunch"): ["흑미밥", "백깍두기(백김치)"],
        (17, "snack_pm"): ["떡(꿀설기)", "우유"],
        (3, "snack_pm"): ["떡(증편)"],
    }


def test_원문은_그대로_둔다(reading: notice_ocr.PhotoReading) -> None:
    # 원문은 사람 검수의 근거라 지우지 않는다 (schema.MenuItem.raw)
    assert reading.meal_plan is not None
    raws = [it.raw for d in reading.meal_plan.days for m in d.meals for it in m.items]
    # 합치기(`merge_outputs`)가 날짜 순으로 놓는다 — 3일 · 16일 · 17일
    assert raws == ["떡(증편),", "흑미밥", "백깍두기9(백김치9)", "떡(꿀설기)", "우유2"]
