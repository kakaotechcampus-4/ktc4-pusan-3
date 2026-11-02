"""행사 원문 읽기 · 거르기 규칙 — 어댑터는 원문만 넘기고 여기서 읽는다 (`events.py`).

- 못 읽는 행은 빼고, 온라인 · 술 행사는 아이와 갈 곳으로 내지 않는다.
- 관람 연령이 아이보다 높으면 뺀다. 보호자와 함께 들어갈 수 있으면 둔다. 못 읽는 조건은 뺀다.
"""

from datetime import date

import pytest

from app.agents.activity.areas import PROVINCE_ALIASES
from app.agents.activity.events import (
    TOUR_REGION_CODES,
    EventKind,
    age_allows,
    core_name,
    distance_m,
    in_province,
    open_on,
    read_event,
)
from app.agents.activity.store.ports import CoarseLocation
from app.core.event_raw import RawEvent


def raw(**overrides: str) -> RawEvent:
    values = {
        "content_id": "100",
        "title": "부산 바다 축제",
        "start": "20261101",
        "end": "20261109",
        "mapx": "129.16",
        "mapy": "35.16",
        "kind_code": "EV01",
        "progress": "",
        "region_code": "26",
    }
    values.update(overrides)
    return RawEvent(**values)


class TestReadEvent:
    def test_원문을_날짜_좌표_종류로_읽는다(self):
        event = read_event(raw())

        assert event is not None
        assert event.name == "부산 바다 축제"
        assert event.kind is EventKind.FESTIVAL
        assert (event.starts_on, event.ends_on) == (date(2026, 11, 1), date(2026, 11, 9))
        assert (event.lat, event.lon) == (35.16, 129.16)

    @pytest.mark.parametrize(
        ("code", "kind"),
        [("EV01", EventKind.FESTIVAL), ("EV02", EventKind.PERFORMANCE), ("EV03", EventKind.EVENT)],
    )
    def test_종류는_분류_코드로_정한다(self, code, kind):
        event = read_event(raw(kind_code=code))

        assert event is not None
        assert event.kind is kind

    @pytest.mark.parametrize(
        "broken",
        [
            {"title": " "},
            {"start": ""},
            {"end": "2026-11-09"},
            {"start": "20261110", "end": "20261101"},
            {"mapx": ""},
            {"mapy": "북위"},
            {"mapx": "0", "mapy": "0"},
            {"kind_code": "EV09"},
        ],
    )
    def test_못_읽는_행은_뺀다(self, broken):
        assert read_event(raw(**broken)) is None

    def test_온라인_행사는_갈_곳이_없어_뺀다(self):
        assert read_event(raw(progress="온라인")) is None

    @pytest.mark.parametrize(
        "title",
        [
            "거제맥주축제",
            "고양 푸드앤비어페스티벌",
            "전주막걸리축제",
            "춘천 술 페스타",
            "2026 대한민국 우리술 대축제",
            "와인 페스티벌",
            "전통주 갤러리 시음회",
        ],
    )
    def test_술_행사는_아이와_할_활동으로_내지_않는다(self, title):
        assert read_event(raw(title=title)) is None

    @pytest.mark.parametrize(
        "title",
        [
            "부산종합민속예술제",
            "2026 달성 대구현대미술제",
            "변용의 기술",
            "어린이 마술 공연",
            "술래잡기 한마당",
        ],
    )
    def test_술이_낱말_안에_든_이름은_술_행사가_아니다(self, title):
        assert read_event(raw(title=title)) is not None


class TestOpenOn:
    def test_그날이_기간_안이면_열린다(self):
        event = read_event(raw(start="20261101", end="20261109"))

        assert open_on(event, [date(2026, 11, 1)])
        assert open_on(event, [date(2026, 11, 9)])
        assert not open_on(event, [date(2026, 11, 10)])

    def test_여러_날_중_하루라도_열리면_열린다(self):
        event = read_event(raw(start="20261108", end="20261108"))

        assert open_on(event, [date(2026, 11, 7), date(2026, 11, 8)])
        assert not open_on(event, [date(2026, 11, 7)])


class TestCoreName:
    """모델은 긴 공식 이름의 연도 · 회차 · 괄호 · 부제를 떼고 쓴다 — 그 이름으로도 알아본다."""

    @pytest.mark.parametrize(
        ("title", "core"),
        [
            ("2026 제5회 바다 비엔날레 : 물결의 기술", "바다 비엔날레"),
            ("2026년 대한민국 정원박람회", "대한민국 정원박람회"),
            ("골목 작품투어 (시민예술프로젝트)", "골목 작품투어"),
            ("제12회 별빛 축제 - 가을밤", "별빛 축제"),
            ("바다 일러스트페어V.7", "바다 일러스트페어"),  # 뒤의 판 번호
            ("어린이 그림책 축제 Vol.3", "어린이 그림책 축제"),
            ("방송국 TV2 공개방송", "방송국 TV2 공개방송"),  # 영문 낱말 안의 V 는 판 번호가 아니다
            ("가을 국악 공연", "가을 국악 공연"),
        ],
    )
    def test_연도_회차_괄호_부제를_뗀다(self, title, core):
        assert core_name(title) == core

    def test_다_떼고_남는_게_없으면_원래_이름이다(self):
        assert core_name("2026 (가칭)") == "2026 (가칭)"


# 만 나이(개월). 3살 아이
THREE_YEARS = 36


class TestAgeAllows:
    @pytest.mark.parametrize("text", ["", "  ", "전 연령", "전연령", "누구나", "제한 없음"])
    def test_제한이_없으면_된다(self, text):
        assert age_allows(text, THREE_YEARS)

    @pytest.mark.parametrize("text", ["만 13세 이상", "만 18세 이상", "8세 이상", "48개월 이상"])
    def test_아이보다_높은_연령이면_뺀다(self, text):
        assert not age_allows(text, THREE_YEARS)

    @pytest.mark.parametrize("text", ["만 3세 이상", "36개월 이상", "24개월 이상"])
    def test_아이가_그_연령이면_된다(self, text):
        assert age_allows(text, THREE_YEARS)

    @pytest.mark.parametrize(
        "text",
        [
            "만 13세(중학생) 이상 ※ 13세 미만의 경우 보호자 동행 필수",
            "전 연령 (만 12세 이하 성인 동반 필수)",
            "전 연령※ 미성년자는 보호자 동반 시 입장 가능",
        ],
    )
    def test_보호자와_함께_들어갈_수_있으면_된다(self, text):
        # 이 서비스의 나들이는 보호자와 간다
        assert age_allows(text, THREE_YEARS)

    @pytest.mark.parametrize(
        "text",
        [
            "만 19세 이상 (보호자 동반 불가)",
            "만 19세 이상(보호자 동반 시에도 관람 불가)",
            "만 15세 이상 보호자 동반 여부와 관계없이 입장 불가",
            "보호자 동반 시 입장 가능, 단 48개월 미만 입장 불가",
        ],
    )
    def test_보호자_동반이_허용으로_적혀_있지_않으면_연령으로_본다(self, text):
        """동반 문구가 있다는 것만으로 들이지 않는다 — 허용이 명시돼야 한다 (#668 리뷰).
        불가 · 금지가 같이 적혀 있으면 동반 문구를 믿지 않고 연령으로 본다(못 읽으면 뺀다).
        """
        assert not age_allows(text, THREE_YEARS)

    @pytest.mark.parametrize(
        "text",
        ["만 7세 이상 (보호자 동반 시 관람가)", "만 8세 이상, 미만은 부모 동반 하에 참여 가능"],
    )
    def test_허용이_적힌_다른_모양도_읽는다(self, text):
        assert age_allows(text, THREE_YEARS)

    @pytest.mark.parametrize("text", ["초등학생 이상", "성인", "청소년 관람불가"])
    def test_못_읽는_조건은_뺀다(self, text):
        # 연령을 모르는 채 아이에게 내지 않는다 — 더 막는 쪽
        assert not age_allows(text, THREE_YEARS)


class TestDistance:
    def test_서버가_거리를_계산한다(self):
        event = read_event(raw(mapx="129.08", mapy="35.18"))
        origin = CoarseLocation(lat=35.18, lon=129.08)

        assert distance_m(event, origin) == 0

    def test_위도_0도1은_약_11km다(self):
        event = read_event(raw(mapx="129.08", mapy="35.28"))
        origin = CoarseLocation(lat=35.18, lon=129.08)

        assert 11_000 < distance_m(event, origin) < 11_300


class TestProvince:
    """시 · 도 행사 검색은 TourAPI 법정동 시도 코드(lDongRegnCd)로 거른다."""

    def test_모든_시도에_코드가_있다(self):
        assert set(TOUR_REGION_CODES) == set(PROVINCE_ALIASES)

    def test_그_시도의_행사만이다(self):
        busan = read_event(raw(region_code="26"))
        gyeongnam = read_event(raw(region_code="48"))
        assert in_province(busan, "부산광역시")
        assert not in_province(gyeongnam, "부산광역시")

    @pytest.mark.parametrize(
        ("code", "province"), [("36110", "세종특별자치시"), ("12", "전남광주통합특별시")]
    )
    def test_코드_모양이_달라도_맞춘다(self, code, province):
        """세종은 다섯 자리로 온다. 전남광주는 통합 코드다 (11-02 실측)."""
        assert in_province(read_event(raw(region_code=code)), province)

    def test_코드가_비면_어느_시도도_아니다(self):
        assert not in_province(read_event(raw(region_code="")), "부산광역시")
