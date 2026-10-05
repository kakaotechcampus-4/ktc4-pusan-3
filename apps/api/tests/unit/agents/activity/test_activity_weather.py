"""날씨 판정 — 설계 4-2 표의 칸마다 경계 양쪽을 본다.

- 모델에게 가는 값에는 수치가 없다. 판정과 등급 라벨뿐이다.
- 조회 실패를 "맑음" · "좋음"으로 메우지 않는다.
"""

from dataclasses import replace

import pytest

from app.agents.activity import weather
from app.agents.activity.readouts import READOUTS
from app.agents.activity.store.ports import (
    Advisories,
    AirQuality,
    Forecast,
    RawAdvisories,
    RawAdvisoryRow,
    RawAir,
    RawForecast,
)
from app.agents.activity.weather import (
    AFTER_SUNSET,
    AIR_BAD,
    AIR_UNCHECKED,
    COLD_ADVISORY,
    HEAT_ADVISORY,
    OUTDOOR_BLOCKED,
    OZONE_ADVISORY,
    RAIN_LIKELY,
    UV_UNCHECKED,
    UV_VERY_HIGH,
    WEATHER_UNCHECKED,
    judge_weather,
    parse_air,
    parse_pop,
    parse_precip,
    parse_uv,
    read_advisories,
    read_air,
    read_forecast,
    uv_grade,
)

CLEAR = Forecast(sky="맑음", precip_mm_per_h=0.0, pop_percent=10)
CLEAN_AIR = AirQuality(pm10=30, pm25=15, ozone_ppm=0.03)
CALM = Advisories(heat="none", cold="none", severe=False)


def judge(**kwargs):
    base = dict(forecast=CLEAR, air=CLEAN_AIR, uv_index=4, advisories=CALM, after_sunset=False)
    return judge_weather(**{**base, **kwargs})


def test_맑고_깨끗하면_야외_가능이고_안내가_없다():
    brief = judge()
    assert brief.outdoor_ok is True
    assert brief.notices == ()
    assert brief.labels == {
        "sky": "맑음",
        "rain": "비 없음",
        "pm10": "좋음",
        "pm25": "좋음",
        "uv": "보통",
    }


class TestRain:
    @pytest.mark.parametrize(("mm", "ok"), [(14.9, True), (15.0, False)])
    def test_시간당_15mm_부터_차단(self, mm, ok):
        assert judge(forecast=Forecast("흐림", mm, 90)).outdoor_ok is ok

    @pytest.mark.parametrize(("mm", "caution"), [(2.9, False), (3.0, True)])
    def test_시간당_3mm_부터_경고(self, mm, caution):
        brief = judge(forecast=Forecast("흐림", mm, 10))
        assert brief.outdoor_ok is True
        assert (RAIN_LIKELY in brief.notices) is caution

    @pytest.mark.parametrize(("pop", "caution"), [(59, False), (60, True)])
    def test_강수확률_60_부터_경고(self, pop, caution):
        brief = judge(forecast=Forecast("구름많음", 0.0, pop))
        assert (RAIN_LIKELY in brief.notices) is caution

    @pytest.mark.parametrize(
        ("mm", "label"), [(0.0, "비 없음"), (0.5, "약한 비"), (3.0, "보통 비"), (15.0, "강한 비")]
    )
    def test_강수_라벨은_기상청_구분이다(self, mm, label):
        assert judge(forecast=Forecast("흐림", mm, 10)).labels["rain"] == label


class TestAdvisories:
    @pytest.mark.parametrize("field", ["heat", "cold"])
    def test_폭염_한파_경보는_차단(self, field):
        advisories = replace(CALM, **{field: "warning"})
        assert judge(advisories=advisories).outdoor_ok is False

    @pytest.mark.parametrize(
        ("field", "notice"), [("heat", HEAT_ADVISORY), ("cold", COLD_ADVISORY)]
    )
    def test_주의보는_경고(self, field, notice):
        brief = judge(advisories=replace(CALM, **{field: "advisory"}))
        assert brief.outdoor_ok is True
        assert notice in brief.notices

    def test_강풍_호우_대설_태풍_특보는_차단(self):
        assert judge(advisories=Advisories("none", "none", severe=True)).outdoor_ok is False


class TestAir:
    @pytest.mark.parametrize(
        ("pm10", "pm25", "ok", "caution"),
        [
            (80, 15, True, False),
            (81, 15, True, True),
            (150, 15, True, True),
            (151, 15, False, False),
            (30, 35, True, False),
            (30, 36, True, True),
            (30, 75, True, True),
            (30, 76, False, False),
        ],
    )
    def test_미세먼지_나쁨은_경고_매우나쁨은_차단(self, pm10, pm25, ok, caution):
        brief = judge(air=AirQuality(pm10=pm10, pm25=pm25, ozone_ppm=0.03))
        assert brief.outdoor_ok is ok
        assert (AIR_BAD in brief.notices) is caution

    @pytest.mark.parametrize(
        ("ppm", "ok", "caution"),
        [(0.119, True, False), (0.12, True, True), (0.299, True, True), (0.30, False, False)],
    )
    def test_오존_주의보는_경고_경보는_차단(self, ppm, ok, caution):
        brief = judge(air=AirQuality(pm10=30, pm25=15, ozone_ppm=ppm))
        assert brief.outdoor_ok is ok
        assert (OZONE_ADVISORY in brief.notices) is caution

    def test_등급_라벨은_환경부_구간이다(self):
        brief = judge(air=AirQuality(pm10=31, pm25=16, ozone_ppm=None))
        assert (brief.labels["pm10"], brief.labels["pm25"]) == ("보통", "보통")


class TestUv:
    @pytest.mark.parametrize(
        ("index", "grade"),
        [
            (0, "낮음"),
            (2, "낮음"),
            (3, "보통"),
            (5, "보통"),
            (6, "높음"),
            (7, "높음"),
            (8, "매우높음"),
            (10, "매우높음"),
            (11, "위험"),
            (15, "위험"),
        ],
    )
    def test_지수는_기상청_등급표로_나눈다(self, index, grade):
        """API 는 숫자만 준다. 경계 양쪽을 본다."""
        assert uv_grade(index) == grade
        assert judge(uv_index=index).labels["uv"] == grade

    @pytest.mark.parametrize(("index", "ok"), [(10, True), (11, False)])
    def test_위험은_차단(self, index, ok):
        assert judge(uv_index=index).outdoor_ok is ok

    @pytest.mark.parametrize(("index", "caution"), [(7, False), (8, True)])
    def test_매우높음은_경고(self, index, caution):
        brief = judge(uv_index=index)
        assert brief.outdoor_ok is True
        assert (UV_VERY_HIGH in brief.notices) is caution


class TestFailures:
    """조회 실패를 기본값으로 메우지 않는다."""

    def test_예보가_실패하면_실내만(self):
        brief = judge(forecast=None)
        assert brief.outdoor_ok is False
        assert brief.notices == (WEATHER_UNCHECKED,)

    def test_강수량을_못_읽었으면_예보_실패와_같다(self):
        brief = judge(forecast=Forecast("맑음", None, 0))
        assert brief.outdoor_ok is False
        assert brief.notices == (WEATHER_UNCHECKED,)

    def test_특보가_실패하면_실내만(self):
        """특보는 차단 신호라 모르면 야외를 내지 않는다."""
        assert judge(advisories=None).outdoor_ok is False

    def test_미세먼지만_실패하면_야외는_허용하고_알린다(self):
        brief = judge(air=None)
        assert brief.outdoor_ok is True
        assert AIR_UNCHECKED in brief.notices
        assert "pm10" not in brief.labels  # "좋음"으로 채우지 않는다

    def test_미세먼지_값이_둘_다_비면_실패와_같다(self):
        brief = judge(air=AirQuality(pm10=None, pm25=None, ozone_ppm=0.03))
        assert AIR_UNCHECKED in brief.notices

    @pytest.mark.parametrize("index", [None, -1])
    def test_자외선을_모르면_허용하고_알린다(self, index):
        brief = judge(uv_index=index)
        assert brief.outdoor_ok is True
        assert UV_UNCHECKED in brief.notices
        assert "uv" not in brief.labels


class TestBlocked:
    def test_해가_지면_실내만(self):
        brief = judge(after_sunset=True)
        assert brief.outdoor_ok is False
        assert brief.notices[-1] == AFTER_SUNSET

    def test_막히면_주의_안내는_빼고_확인_못_한_것만_남긴다(self):
        """야외를 막았는데 "우산 챙기세요"가 붙으면 이상하다."""
        brief = judge(forecast=Forecast("비", 20.0, 90), air=None, uv_index=9)
        assert brief.outdoor_ok is False
        assert brief.notices == (AIR_UNCHECKED, OUTDOOR_BLOCKED)


class TestModelPayload:
    def test_모델에게는_수치가_가지_않는다(self):
        """모델이 "나쁨이지만 잠깐이면 괜찮아요"를 쓰지 못하게 한다 (4-2)."""
        brief = judge(
            forecast=Forecast("흐림", 4.2, 70),
            air=AirQuality(pm10=95, pm25=40, ozone_ppm=0.13),
            uv_index=9,
        )
        payload = brief.to_model_payload()
        assert payload["outdoor_ok"] is True
        for value in payload.values():
            if isinstance(value, str):
                assert not any(ch.isdigit() for ch in value), value

    def test_안내_문구는_카탈로그에서_읽는다(self):
        brief = judge(forecast=Forecast("흐림", 4.0, 70), air=None, uv_index=None)
        assert brief.notice_texts() == tuple(READOUTS.get(key).template for key in brief.notices)
        assert "미세먼지는 확인하지 못했어요." in brief.notice_texts()

    def test_weather_py_의_문구_키가_카탈로그에_모두_있다(self):
        """키만 있고 문구가 없으면 판정이 그 키를 낼 때 KeyError 다 — 부팅 전에 잡는다."""
        keys = {v for k, v in vars(weather).items() if k.isupper() and isinstance(v, str)}
        notice_keys = {key for key in keys if key.startswith("weather.")}
        assert len(notice_keys) == 11
        for key in notice_keys:
            assert READOUTS.get(key).kind == "notice"


class TestParsePrecip:
    @pytest.mark.parametrize(
        ("raw", "mm"),
        [
            ("강수없음", 0.0),
            ("0", 0.0),  # 발표 3일 뒤 시각부터는 "강수없음" 대신 이렇게 온다 (09-30 실호출)
            ("1.0mm 미만", 0.5),
            ("3.0mm", 3.0),
            ("30.0~50.0mm", 30.0),
            ("50.0mm 이상", 50.0),
            ("7", 7.0),
            (" 12.5mm ", 12.5),
        ],
    )
    def test_기상청_형식을_읽는다(self, raw, mm):
        assert parse_precip(raw) == mm

    @pytest.mark.parametrize("raw", [None, "", "-", "비 많음", "mm", "-3.0mm"])
    def test_못_읽으면_없음이_아니라_None(self, raw):
        """ "강수없음"(0)과 "모름"(None)은 다르다. 파싱 실패를 맑음으로 넘기지 않는다."""
        assert parse_precip(raw) is None

    def test_1mm_미만은_약한_비로_판정된다(self):
        """1.0 으로 읽으면 안 된다 — 그러면 경계 근처에서 판정이 흔들린다."""
        mm = parse_precip("1.0mm 미만")
        assert mm is not None and 0 < mm < 1


class TestParsePop:
    @pytest.mark.parametrize(("raw", "pop"), [("0", 0), ("60", 60), ("100", 100)])
    def test_정수로_읽는다(self, raw, pop):
        assert parse_pop(raw) == pop

    @pytest.mark.parametrize("raw", [None, "", "101", "-1", "60%"])
    def test_범위_밖이나_형식이_다르면_None(self, raw):
        assert parse_pop(raw) is None


class TestParseUv:
    @pytest.mark.parametrize(("raw", "index"), [("0", 0), ("6", 6), ("11", 11), (" 7 ", 7)])
    def test_정수로_읽는다(self, raw, index):
        assert parse_uv(raw) == index

    @pytest.mark.parametrize("raw", [None, "", " ", "-1", "6.5", "높음"])
    def test_비었거나_형식이_다르면_None(self, raw):
        """먼 시각 칸은 "" 로 온다. "낮음"으로 채우지 않는다."""
        assert parse_uv(raw) is None


class TestParseAir:
    @pytest.mark.parametrize(("raw", "value"), [("19", 19.0), ("0.056", 0.056), ("0", 0.0)])
    def test_숫자로_읽는다(self, raw, value):
        assert parse_air(raw) == value

    @pytest.mark.parametrize(
        ("raw", "flag"),
        [
            ("-", "통신장애"),  # 09-30 실호출에서 나온 모양
            ("-", None),
            ("19", "통신장애"),  # 사유가 붙으면 숫자가 있어도 믿지 않는다
            ("", None),
            (None, None),
            ("-3", None),
            ("nan", None),
        ],
    )
    def test_측정기가_멈춘_값은_None(self, raw, flag):
        """0 으로 읽으면 고장 난 날이 "좋음"이 된다."""
        assert parse_air(raw, flag) is None

    def test_멈춘_값은_판정에서_확인_못_함이다(self):
        air = AirQuality(pm10=parse_air("-", "통신장애"), pm25=parse_air("-"), ozone_ppm=None)
        brief = judge(air=air)
        assert AIR_UNCHECKED in brief.notices
        assert "pm10" not in brief.labels


class TestReadForecast:
    def test_원문을_판정_입력으로_바꾼다(self):
        forecast = read_forecast(RawForecast(sky="3", pcp="1.0mm 미만", pop="60"))
        assert forecast == Forecast(sky="구름많음", precip_mm_per_h=0.5, pop_percent=60)

    def test_모르는_하늘_코드는_라벨을_비운다(self):
        """하늘상태는 라벨일 뿐 판정에 쓰지 않는다. 지어내지 않고 비운다."""
        assert read_forecast(RawForecast(sky="9", pcp="강수없음", pop="0")).sky is None

    def test_강수량을_못_읽으면_실내만(self):
        forecast = read_forecast(RawForecast(sky="1", pcp="비 많이", pop="90"))
        assert judge(forecast=forecast).notices == (WEATHER_UNCHECKED,)


class TestReadAir:
    def test_원문을_판정_입력으로_바꾼다(self):
        raw = RawAir(pm10="45", pm10_flag=None, pm25="19", pm25_flag=None, o3="0.056", o3_flag=None)
        assert read_air(raw) == AirQuality(pm10=45.0, pm25=19.0, ozone_ppm=0.056)

    def test_측정기가_멈춘_값만_비운다(self):
        """실응답 모양 — PM10 만 "-" + "통신장애" 이고 PM2.5 는 정상이었다."""
        raw = RawAir(
            pm10="-", pm10_flag="통신장애", pm25="19", pm25_flag=None, o3="0.056", o3_flag=None
        )
        air = read_air(raw)
        assert air.pm10 is None
        assert air.pm25 == 19.0


def row(warn_var="2", stress="0", command="1", tm_fc="202609290650", seq="131", area="L1073120"):
    return RawAdvisoryRow(
        area_code=area,
        warn_var=warn_var,
        warn_stress=stress,
        command=command,
        tm_fc=tm_fc,
        tm_seq=seq,
    )


def advisories(*rows, code="00"):
    return read_advisories(RawAdvisories(result_code=code, rows=rows))


CLEAR_SKY = Advisories(heat="none", cold="none", severe=False)


class TestReadAdvisories:
    def test_NO_DATA_는_특보_없음이다(self):
        """실패로 읽으면 특보 없는 평범한 날이 전부 실내가 된다."""
        result = read_advisories(RawAdvisories(result_code="03"))
        assert result == CLEAR_SKY
        assert judge(advisories=result).outdoor_ok is True

    @pytest.mark.parametrize("code", ["02", "99", ""])
    def test_그_밖의_결과_코드는_실패다(self, code):
        assert advisories(code=code) is None

    def test_해제된_특보는_발효가_아니다(self):
        """실응답 모양 — 경주시동부 호우주의보가 06:50 발표 · 08:00 해제 두 행으로 왔다."""
        issued = row(command="1", tm_fc="202609290650", seq="131")
        cleared = row(command="2", tm_fc="202609290800", seq="132")
        assert advisories(issued, cleared) == CLEAR_SKY

    def test_가장_최근_행으로_본다_목록_순서가_아니다(self):
        issued = row(command="1", tm_fc="202609290650", seq="131")
        cleared = row(command="2", tm_fc="202609290800", seq="132")
        assert advisories(cleared, issued) == CLEAR_SKY

    def test_발표만_있으면_발효_중이다(self):
        assert advisories(row(warn_var="2", command="1")).severe is True

    @pytest.mark.parametrize("warn_var", ["1", "2", "5", "7", "8"])
    def test_강풍_호우_폭풍해일_태풍_대설은_차단_신호다(self, warn_var):
        result = advisories(row(warn_var=warn_var))
        assert result.severe is True
        assert judge(advisories=result).outdoor_ok is False

    @pytest.mark.parametrize(
        ("stress", "level"), [("0", "advisory"), ("1", "warning"), ("2", "warning")]
    )
    def test_폭염_등급(self, stress, level):
        """중대경보(2)는 경보보다 약하게 읽지 않는다."""
        assert advisories(row(warn_var="12", stress=stress)).heat == level

    def test_한파주의보(self):
        assert advisories(row(warn_var="3", stress="0")).cold == "advisory"

    def test_연장은_발효_중이다(self):
        issued = row(warn_var="12", command="1", tm_fc="202608010600", seq="1")
        extended = row(warn_var="12", command="3", tm_fc="202608030600", seq="5")
        assert advisories(issued, extended).heat == "advisory"

    def test_같은_구역의_주의보와_경보는_센_쪽이다(self):
        other = row(warn_var="12", stress="1", area="L1010100")
        assert advisories(row(warn_var="12", stress="0"), other).heat == "warning"

    @pytest.mark.parametrize("warn_var", ["4", "6", "9"])
    def test_일부러_빼는_특보는_판정에_영향이_없다(self, warn_var):
        """건조 · 풍랑 · 황사. 황사는 미세먼지 값으로 따로 본다."""
        result = advisories(row(warn_var=warn_var, stress="1"))
        assert result == CLEAR_SKY
        assert judge(advisories=result).outdoor_ok is True

    @pytest.mark.parametrize("warn_var", ["999", "13", "10", ""])
    def test_처음_보는_특보_종류가_발효_중이면_모른다고_한다(self, warn_var):
        """모르는 종류를 "없음"으로 넘기면 다른 조건이 좋을 때 야외가 열린다 (#194 리뷰)."""
        result = advisories(row(warn_var=warn_var, command="1", stress="1"))
        assert result is None
        assert judge(advisories=result).outdoor_ok is False

    def test_처음_보는_종류라도_해제된_행은_넘긴다(self):
        assert advisories(row(warn_var="999", command="2")) == CLEAR_SKY

    @pytest.mark.parametrize(("command", "stress"), [("5", "0"), ("1", "9")])
    def test_모르는_코드가_섞이면_모른다고_한다(self, command, stress):
        """모르는 특보를 "없음"으로 넘기지 않는다 — 실내만으로 간다."""
        result = advisories(row(command=command, stress=stress))
        assert result is None
        assert judge(advisories=result).outdoor_ok is False

    def test_정상인데_행이_없으면_특보_없음(self):
        assert advisories() == CLEAR_SKY
