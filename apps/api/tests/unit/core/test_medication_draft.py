"""복약 초안 payload 계약. Agent · 제출 API · 화면이 같은 모양을 쓰는 한 곳이다.

초안은 DB 에 없고 payload 로만 나간다. 제출하면 백엔드가 `starts_on` ~ `ends_on` 의 날마다 `doses`
(하루 시각 패턴)를 펼쳐 회차 행을 만든다. 그래서 이 모델은 DB CHECK 를 그대로 옮긴다 —
초안을 그대로 제출했는데 DB 에서 터지면 보호자는 무엇을 고칠지 모른다.

    - 명세 `health_agent_own_table.md` §6 의 예시가 그대로 통과하고, 같은 모양으로 되돌아 나간다
    - `fixed` 와 `fixed_time` 은 함께 · fixed 의 offset 은 0 · offset 은 -120 ~ 180
    - 같은 시각 dose 둘 금지 · `notice_times` 는 dose 시각과 같다
    - `missing` 은 비어 있는 필수 칸(title · starts_on · ends_on)과 같다
    - `ends_on` 은 `starts_on` 이상
    - `symptom_codes` 는 복약 이유를 사전으로 맞춘 코드다 — Agent 가 싣고 API 는 그대로 저장한다
"""

import json
import re
from copy import deepcopy
from datetime import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.medication_draft import (
    ANCHOR_TIMES,
    OFFSET_MAX,
    OFFSET_MIN,
    DoseDraft,
    MedicationDraft,
    scheduled_time_of,
)

SPEC = (
    Path(__file__).resolve().parents[5] / "docs" / "agents" / "health" / "health_agent_own_table.md"
)


def spec_example() -> dict[str, Any]:
    """명세 §6 의 초안 payload 예시(첫 json 블록)."""
    section = SPEC.read_text(encoding="utf-8").split("## 6.")[1]
    block = re.search(r"```json\n(.+?)\n```", section, re.S)
    assert block is not None
    return json.loads(block.group(1))


def dose(anchor: str, offset: int, scheduled: str, fixed: str | None = None) -> dict[str, Any]:
    return {
        "timing_anchor": anchor,
        "offset_minutes": offset,
        "fixed_time": fixed,
        "scheduled_time": scheduled,
    }


def create_payload() -> dict[str, Any]:
    """제목 · 기간이 다 찬 create 초안 — 하루 세 번 식후."""
    return {
        "draft_id": "m1",
        "op": "create",
        "source": "utterance",
        "schedule": {
            "title": "항생제",
            "dosage": "5ml",
            "indication_text": None,
            "storage": None,
            "med_form": None,
            "note": None,
            "symptom_codes": [],
            "starts_on": "2026-09-22",
            "ends_on": "2026-09-26",
        },
        "doses": [
            dose("breakfast", 30, "08:30"),
            dose("lunch", 30, "12:30"),
            dose("dinner", 30, "19:30"),
        ],
        "notice_times": ["08:30", "12:30", "19:30"],
        "missing": [],
    }


def update_payload() -> dict[str, Any]:
    payload = create_payload()
    payload["op"] = "update"
    payload["schedule_id"] = "s1"
    payload["before"] = {
        "schedule": deepcopy(payload["schedule"]),
        "doses": deepcopy(payload["doses"]),
    }
    return payload


def test_명세_예시가_그대로_통과한다():
    example = spec_example()

    draft = MedicationDraft.model_validate(example)

    assert draft.to_payload() == example


def test_모르는_칸은_거절():
    # days_of_week 는 회차 행이 날짜별로 있어서 없앴다 — 옛 모양이 조용히 통과하면 안 된다
    payload = create_payload()
    payload["doses"][0]["days_of_week"] = []

    with pytest.raises(ValidationError):
        MedicationDraft.model_validate(payload)

    payload = create_payload()
    payload["schedule"]["symptom"] = "열"
    with pytest.raises(ValidationError):
        MedicationDraft.model_validate(payload)


def test_dose_는_1개_이상():
    payload = create_payload()
    payload["doses"] = []
    payload["notice_times"] = []

    with pytest.raises(ValidationError):
        MedicationDraft.model_validate(payload)


class TestFixedTime:
    def test_fixed_는_fixed_time_이_있어야_한다(self):
        with pytest.raises(ValidationError):
            DoseDraft.model_validate(dose("fixed", 0, "15:00"))

    def test_fixed_가_아니면_fixed_time_이_없어야_한다(self):
        with pytest.raises(ValidationError):
            DoseDraft.model_validate(dose("breakfast", 30, "08:30", fixed="08:30"))

    def test_fixed_의_offset_은_0(self):
        with pytest.raises(ValidationError):
            DoseDraft.model_validate(dose("fixed", 30, "15:30", fixed="15:00"))

    def test_fixed_는_fixed_time_이_그대로_시각이다(self):
        assert DoseDraft.model_validate(
            dose("fixed", 0, "15:00", fixed="15:00")
        ).scheduled_time == (time(15, 0))


class TestOffset:
    @pytest.mark.parametrize("offset", [OFFSET_MIN - 1, OFFSET_MAX + 1])
    def test_offset_범위는_DB_CHECK_와_같다(self, offset):
        with pytest.raises(ValidationError):
            DoseDraft.model_validate(dose("breakfast", offset, "08:30"))

    @pytest.mark.parametrize("offset", [OFFSET_MIN, OFFSET_MAX])
    def test_범위_끝은_통과한다(self, offset):
        scheduled = scheduled_time_of("breakfast", offset, None).strftime("%H:%M")

        DoseDraft.model_validate(dose("breakfast", offset, scheduled))

    def test_범위는_DB_CHECK_상수다(self):
        assert (OFFSET_MIN, OFFSET_MAX) == (-120, 180)


def test_scheduled_time_은_anchor_계산과_같아야_한다():
    # 모델이 시각을 따로 지어내 notice_times 와 dose 가 둘 다 틀리는 것을 막는다
    with pytest.raises(ValidationError):
        DoseDraft.model_validate(dose("breakfast", 30, "09:00"))


def test_같은_시각_dose_둘은_거절():
    # UNIQUE(schedule_id, dose_date, scheduled_time) — 08:30 이 anchor 와 fixed 로 두 번
    payload = create_payload()
    payload["doses"] = [dose("breakfast", 30, "08:30"), dose("fixed", 0, "08:30", fixed="08:30")]
    payload["notice_times"] = ["08:30"]

    with pytest.raises(ValidationError):
        MedicationDraft.model_validate(payload)


class TestNoticeTimes:
    def test_notice_times_는_dose_시각과_같다(self):
        payload = create_payload()
        payload["notice_times"] = ["08:30", "12:30", "18:30"]

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_notice_times_가_모자라도_거절(self):
        payload = create_payload()
        payload["notice_times"] = ["08:30", "12:30"]

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_notice_times_는_정렬돼_있어야_한다(self):
        payload = create_payload()
        payload["notice_times"] = ["19:30", "08:30", "12:30"]

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_dose_가_시각순이_아니어도_notice_times_는_정렬이다(self):
        payload = create_payload()
        payload["doses"].reverse()

        draft = MedicationDraft.model_validate(payload)

        assert draft.notice_times == ["08:30", "12:30", "19:30"]


class TestOpShape:
    def test_update_는_schedule_id_와_before_가_있다(self):
        draft = MedicationDraft.model_validate(update_payload())
        assert draft.schedule_id == "s1" and draft.before is not None

        for key in ("schedule_id", "before"):
            payload = update_payload()
            del payload[key]
            with pytest.raises(ValidationError):
                MedicationDraft.model_validate(payload)

    def test_create_에는_schedule_id_가_없다(self):
        for key, value in (("schedule_id", "s1"), ("before", update_payload()["before"])):
            payload = create_payload()
            payload[key] = value
            with pytest.raises(ValidationError):
                MedicationDraft.model_validate(payload)

    def test_before_의_dose_도_같은_규칙을_따른다(self):
        payload = update_payload()
        payload["before"]["doses"][0]["offset_minutes"] = OFFSET_MAX + 1

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)


class TestMissing:
    def test_빈_필수_칸은_missing_에_실린다(self):
        payload = create_payload()
        payload["schedule"]["title"] = None
        payload["schedule"]["ends_on"] = None
        payload["missing"] = ["title", "ends_on"]

        assert MedicationDraft.model_validate(payload).missing == ["title", "ends_on"]

    def test_빈_필수_칸이_있는데_missing_이_비면_거절(self):
        payload = create_payload()
        payload["schedule"]["ends_on"] = None

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_찬_칸이_missing_에_있으면_거절(self):
        payload = create_payload()
        payload["missing"] = ["ends_on"]

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_선택_칸은_비어도_missing_이_아니다(self):
        # dosage · indication_text · storage · med_form · note
        payload = create_payload()
        payload["schedule"]["dosage"] = None

        assert MedicationDraft.model_validate(payload).missing == []

    def test_starts_on_이_비어도_missing(self):
        payload = create_payload()
        payload["schedule"]["starts_on"] = None
        payload["schedule"]["ends_on"] = None
        payload["missing"] = ["starts_on", "ends_on"]

        MedicationDraft.model_validate(payload)


class TestSpan:
    def test_ends_on_은_starts_on_이상(self):
        payload = create_payload()
        payload["schedule"]["starts_on"] = "2026-09-26"
        payload["schedule"]["ends_on"] = "2026-09-25"

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_하루짜리_코스는_같은_날이다(self):
        payload = create_payload()
        payload["schedule"]["ends_on"] = payload["schedule"]["starts_on"]

        MedicationDraft.model_validate(payload)


class TestOptionalColumns:
    @pytest.mark.parametrize(("column", "value"), [("storage", "freezer"), ("med_form", "capsule")])
    def test_storage_와_med_form_은_DB_CHECK_값만(self, column, value):
        payload = create_payload()
        payload["schedule"][column] = value

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)

    def test_보호자가_적은_선택_칸은_그대로_실린다(self):
        payload = create_payload()
        payload["schedule"].update(storage="fridge", med_form="liquid", note="흔들어서 주세요")

        schedule = MedicationDraft.model_validate(payload).to_payload()["schedule"]

        assert (schedule["storage"], schedule["med_form"], schedule["note"]) == (
            "fridge",
            "liquid",
            "흔들어서 주세요",
        )


def test_to_payload_는_JSON_으로_바뀐다():
    payload = MedicationDraft.model_validate(create_payload()).to_payload()

    assert json.loads(json.dumps(payload)) == create_payload()


def test_기준_시각_상수():
    assert dict(ANCHOR_TIMES) == {
        "wake_up": time(7, 30),
        "breakfast": time(8, 0),
        "lunch": time(12, 0),
        "dinner": time(19, 0),
        "bedtime": time(20, 30),
    }
    assert scheduled_time_of("breakfast", 30, None) == time(8, 30)
    assert scheduled_time_of("lunch", 30, None) == time(12, 30)
    assert scheduled_time_of("dinner", 30, None) == time(19, 30)
    assert scheduled_time_of("dinner", -30, None) == time(18, 30)
    assert scheduled_time_of("bedtime", 0, None) == time(20, 30)
    assert scheduled_time_of("fixed", 0, time(15, 0)) == time(15, 0)


class TestSymptomCodes:
    def test_빠져_있으면_빈_목록이다(self):
        payload = create_payload()
        del payload["schedule"]["symptom_codes"]

        draft = MedicationDraft.model_validate(payload)

        assert draft.schedule.symptom_codes == []
        assert draft.to_payload()["schedule"]["symptom_codes"] == []

    def test_싣고_되돌아_나간다(self):
        payload = create_payload()
        payload["schedule"]["symptom_codes"] = ["fever", "cough"]

        draft = MedicationDraft.model_validate(payload)

        assert draft.to_payload()["schedule"]["symptom_codes"] == ["fever", "cough"]

    def test_수정_전_원본에도_실린다(self):
        payload = update_payload()
        payload["schedule"]["symptom_codes"] = ["fever"]
        payload["before"]["schedule"]["symptom_codes"] = ["fever"]

        draft = MedicationDraft.model_validate(payload)

        assert draft.before is not None and draft.before.schedule.symptom_codes == ["fever"]

    @pytest.mark.parametrize("codes", [[""], [" "], ["fever", "fever"], "fever", [None]])
    def test_빈_코드_중복_목록이_아닌_값은_거절(self, codes):
        payload = create_payload()
        payload["schedule"]["symptom_codes"] = codes

        with pytest.raises(ValidationError):
            MedicationDraft.model_validate(payload)
