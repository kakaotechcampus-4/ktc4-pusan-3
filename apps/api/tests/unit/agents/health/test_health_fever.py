"""열 타임라인 `build_fever_timeline` 과 `infant_fever` (모델 0회).

- 체온 · 체크된 복약 회차 · 보호자가 기록한 먹인 약을 나열만 한다. 약 성분 · 계열 ·
  "투약 가능 시각" · 경과 해석이 없다. 다음 예정은 등록된 일정(회차 행)에서만 나온다
- 체크된 회차는 회차 예정 시각, 먹인 약은 보호자가 말한 시각이라 문구가 다르다
- 체크 안 된 회차는 나열하지 않는다 — "안 먹였다" 로 읽힌다
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import count
from uuid import UUID

from app.agents.common.refs import Ref
from app.agents.health.readouts import READOUTS
from app.agents.health.store.inmemory import course_with_doses
from app.agents.health.store.ports import DoseSlot, HealthObservation
from app.agents.health.tools import fever
from app.agents.health.tools.fever import (
    FEVER_TEMPERATURE,
    INFANT_FEVER_BELOW_MONTH,
    build_fever_timeline,
    infant_fever,
)
from tests.unit.agents.health.support import KST

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=KST)
DAY = timedelta(days=1)
D14 = date(2026, 10, 14)
D15 = date(2026, 10, 15)
PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
_ids = count(7000)


def reading(
    temperature: str | None = "38.5",
    *,
    at: datetime | None = None,
    measured: datetime | None = None,
    site: str | None = None,
    day: date | None = None,
    symptoms: tuple[str, ...] = (),
    **kwargs,
) -> HealthObservation:
    """체온 기록 한 건. `measured` 는 잰 시각(measured_at), `at` 은 말한 시각(observed_time)."""
    moment = measured or at
    when = day or (moment.astimezone(KST).date() if moment else D15)
    return HealthObservation(
        id=UUID(int=next(_ids)),
        observed_from=when,
        observed_to=when,
        observed_time=at,
        symptoms=symptoms,
        temperature=Decimal(temperature) if temperature is not None else None,
        measured_at=measured,
        measure_site=site,
        **kwargs,
    )


def given(
    title: str = "물약", *, at: datetime | None = None, day: date | None = None, **kwargs
) -> HealthObservation:
    """먹인 약 행. 증상 · 체온이 없다."""
    when = day or (at.astimezone(KST).date() if at else D15)
    return HealthObservation(
        id=UUID(int=next(_ids)),
        observed_from=when,
        observed_to=when,
        observed_time=at,
        symptoms=(),
        medication_given=title,
        **kwargs,
    )


def kst(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=KST)


def build(observations=(), courses=(), doses=()):
    return build_fever_timeline(observations, courses, doses, now=NOW, timezone=KST)


def bodies(readouts) -> list[str]:
    return [r.body for r in readouts]


def test_상수는_정해진_값이다():
    assert Decimal("38.0") == FEVER_TEMPERATURE
    assert INFANT_FEVER_BELOW_MONTH == 3
    assert fever.INFANT_FEVER_BELOW_MONTH == 3


def test_시간순_나열과_최고_최저():
    late = reading("38.2", measured=kst(10, 15, 7, 30))
    high = reading("39.1", measured=kst(10, 14, 20, 0))
    early = reading("37.6", measured=kst(10, 14, 8, 15))

    out = bodies(build([late, high, early]))

    assert out == [
        "10/14 08:15 37.6℃",
        "10/14 20:00 39.1℃",
        "10/15 07:30 38.2℃",
        "가장 높았던 체온 39.1℃ (10/14 20:00)",
        "가장 낮았던 체온 37.6℃ (10/14 08:15)",
    ]


def test_체온이_하나면_최저는_내지_않는다():
    out = bodies(build([reading("38.4", measured=kst(10, 14, 22, 0))]))

    assert out == ["10/14 22:00 38.4℃", "가장 높았던 체온 38.4℃ (10/14 22:00)"]


def test_48시간_밖_기록은_뺀다():
    old = reading("40.0", measured=kst(10, 13, 8, 59))  # now - 48h 보다 1분 이르다
    edge = reading("38.1", measured=kst(10, 13, 9, 0))

    out = bodies(build([old, edge]))

    assert out[0] == "10/13 09:00 38.1℃"
    assert all("40.0" not in line for line in out)


def test_부위가_있으면_값_옆에_보여준다():
    ear = reading("38.3", measured=kst(10, 14, 21, 0), site="ear")
    plain = reading("38.0", measured=kst(10, 14, 22, 0))

    out = bodies(build([ear, plain]))

    assert out[0] == "10/14 21:00 38.3℃ (귀)"
    assert out[1] == "10/14 22:00 38.0℃"


def test_시각은_아이_타임존으로():
    # UTC 10/14 15:30 = KST 10/15 00:30. UTC 그대로 찍으면 날짜가 하루 어긋난다
    utc_moment = datetime(2026, 10, 14, 15, 30, tzinfo=UTC)
    out = bodies(build([reading("38.6", measured=utc_moment)]))

    assert out[0] == "10/15 00:30 38.6℃"
    assert out[1] == "가장 높았던 체온 38.6℃ (10/15 00:30)"


def test_잰_시각이_없으면_말한_시각으로():
    said = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)  # KST 10/14 22:00
    both = reading(
        "38.9",
        at=datetime(2026, 10, 14, 15, 0, tzinfo=UTC),  # KST 10/15 00:00
        measured=datetime(2026, 10, 14, 14, 0, tzinfo=UTC),  # KST 10/14 23:00 — 이쪽이 먼저다
    )

    out = bodies(build([reading("38.2", at=said), both]))

    assert out[:2] == ["10/14 22:00 38.2℃", "10/14 23:00 38.9℃"]


def test_체온_기록이_없으면_한_줄():
    no_temperature = reading(None, at=kst(10, 14, 22, 0), symptoms=("열나요",))
    out = build([no_temperature])

    assert bodies(out) == [READOUTS.get("fever.no_record").template]
    assert out[0].kind == "fever_timeline"
    assert bodies(build([])) == [READOUTS.get("fever.no_record").template]


def test_48시간_안에_체온이_없으면_한_줄():
    old = reading("39.0", measured=kst(10, 12, 12, 0))

    assert bodies(build([old])) == [READOUTS.get("fever.no_record").template]


def test_마지막_복약은_체크된_회차에서():
    # 체크는 몰아서 눌렀다 — 12:30 회차를 22:50 에, 08:30 회차를 23:50 에 눌렀다
    course, doses = course_with_doses(
        "항생제",
        PATTERN,
        D14 - DAY,
        D15,
        checked={
            (D14, time(12, 30)): kst(10, 14, 22, 50),
            (D14, time(8, 30)): kst(10, 14, 23, 50),
        },
    )

    out = bodies(build([reading(measured=kst(10, 14, 20, 0))], [course], doses))

    # 마지막 = 가장 늦은 회차 예정 시각이다 — 가장 늦게 누른 것이 아니다. 누른 시각은 쓰지 않는다
    assert "마지막으로 체크된 복약 항생제 (10/14 12:30 회차)" in out
    assert not any("22:50" in line or "23:50" in line for line in out)


def test_어제_끝난_코스의_마지막_복약도_나온다():
    course, doses = course_with_doses(
        "연고약", [DoseSlot.of("breakfast", 30)], D14 - 2 * DAY, D14, checked={(D14, time(8, 30))}
    )

    out = bodies(build([reading(measured=kst(10, 14, 20, 0))], [course], doses))

    assert "마지막으로 체크된 복약 연고약 (10/14 08:30 회차)" in out


def test_코스마다_한_줄():
    first, first_doses = course_with_doses(
        "항생제", PATTERN, D14, D15, checked={(D14, time(19, 30))}
    )
    second, second_doses = course_with_doses(
        "점비약", [DoseSlot.of("breakfast", 30)], D14, D15, checked={(D15, time(8, 30))}
    )

    out = bodies(
        build(
            [reading(measured=kst(10, 14, 20, 0))],
            [first, second],
            [*first_doses, *second_doses],
        )
    )

    assert "마지막으로 체크된 복약 항생제 (10/14 19:30 회차)" in out
    assert "마지막으로 체크된 복약 점비약 (10/15 08:30 회차)" in out


def test_체크_안_된_회차는_나열하지_않는다():
    course, doses = course_with_doses("항생제", PATTERN, D14 - DAY, D15)  # 하나도 체크 안 됨

    out = bodies(build([reading(measured=kst(10, 14, 20, 0))], [course], doses))

    assert not any("마지막으로 체크된 복약" in line for line in out)
    # 지난 회차가 줄로 나오지 않는다 — 다음 예정 한 줄만 더해진다
    assert len(out) == 3  # 체온 한 줄 + 최고 + 다음 예정
    assert not any(word in " ".join(out) for word in ("안 먹", "못 먹", "빼먹", "놓쳤", "체크 안"))


def test_다음_예정은_등록_회차에서만():
    course, doses = course_with_doses("항생제", PATTERN, D14, D15)
    reading_obs = [reading(measured=kst(10, 14, 20, 0))]

    # 지금(10/15 09:00) 이후 가장 이른 회차 — 10/15 12:30
    assert "등록된 일정상 다음 예정 10/15 12:30" in bodies(build(reading_obs, [course], doses))
    # 회차 행이 없으면 줄이 없다 — 패턴에서 짐작해 만들지 않는다
    assert not any("다음 예정" in line for line in bodies(build(reading_obs, [course], [])))
    # 등록된 코스가 없어도 없다
    assert not any("다음 예정" in line for line in bodies(build(reading_obs)))


def test_다음_예정은_남은_회차가_없으면_없다():
    course, doses = course_with_doses("항생제", PATTERN, D14 - DAY, D15)
    # 오늘 회차가 전부 지났다고 보는 시각
    late = datetime(2026, 10, 15, 21, 0, tzinfo=KST)
    obs = [reading(measured=kst(10, 15, 7, 0))]

    out = bodies(build_fever_timeline(obs, [course], doses, now=late, timezone=KST))

    assert not any("다음 예정" in line for line in out)


def test_멈춘_코스는_다음_예정을_내지_않는다():
    course, doses = course_with_doses("항생제", PATTERN, D14, D15 + DAY, status="stopped")

    out = bodies(build([reading(measured=kst(10, 14, 20, 0))], [course], doses))

    assert not any("다음 예정" in line for line in out)


def test_미리_체크한_미래_회차는_마지막도_다음도_아니다():
    # 지금은 10/15 09:00. 10/15 12:30 회차를 미리 눌렀다 — 아직 오지 않은 회차다
    course, doses = course_with_doses(
        "항생제",
        PATTERN,
        D14,
        D15,
        checked={(D14, time(8, 30)), (D15, time(12, 30))},
    )

    out = bodies(build([reading(measured=kst(10, 14, 20, 0))], [course], doses))

    assert "마지막으로 체크된 복약 항생제 (10/14 08:30 회차)" in out
    assert not any("10/15 12:30" in line for line in out)
    # 다음 예정은 체크 안 된 회차 중 지금 뒤 가장 이른 것
    assert "등록된 일정상 다음 예정 10/15 19:30" in out


def test_먹인_약은_체크된_회차와_다른_문구로():
    course, doses = course_with_doses("항생제", PATTERN, D14, D15, checked={(D14, time(12, 30))})
    pill = given("물약", at=kst(10, 14, 21, 0))

    out = bodies(build([reading(measured=kst(10, 14, 20, 0)), pill], [course], doses))

    assert "10/14 21:00 물약 먹였다고 기록하셨어요" in out  # 보호자가 말한 시각
    assert "마지막으로 체크된 복약 항생제 (10/14 12:30 회차)" in out  # 회차 예정 시각
    given_line = next(line for line in out if "물약" in line)
    assert "회차" not in given_line
    assert "체크" not in given_line


def test_먹인_약은_시각순이고_시각을_말하지_않은_것은_그날_끝에():
    no_time = given("시럽", day=D14)
    evening = given("물약", at=kst(10, 14, 21, 0))
    morning = given("가루약", at=kst(10, 14, 7, 0))
    next_day = given("점안액", day=D15)

    out = bodies(build([reading(measured=kst(10, 14, 20, 0)), next_day, no_time, evening, morning]))

    texts = [line for line in out if "기록하셨어요" in line]
    assert texts == [
        "10/14 07:00 가루약 먹였다고 기록하셨어요",
        "10/14 21:00 물약 먹였다고 기록하셨어요",
        "10/14 시럽 먹였다고 기록하셨어요",
        "10/15 점안액 먹였다고 기록하셨어요",
    ]
    # 체온 줄과 같은 시간 흐름 안에 있다 — 20:00 체온은 07:00 약 뒤, 21:00 약 앞이다
    assert out.index("10/14 07:00 가루약 먹였다고 기록하셨어요") < out.index("10/14 20:00 38.5℃")
    assert out.index("10/14 20:00 38.5℃") < out.index("10/14 21:00 물약 먹였다고 기록하셨어요")


def test_연결된_먹인_약은_증상_줄_아래에_묶음으로():
    first = reading("38.9", measured=kst(10, 14, 18, 0), symptoms=("열나요",))
    second = reading("38.1", measured=kst(10, 14, 22, 0))
    linked = given("물약", at=kst(10, 14, 23, 30), linked_symptom_id=first.id)

    out = bodies(build([second, linked, first]))

    # 시각만 보면 둘째 체온 줄이 먼저 오겠지만, 묶음은 연결된 증상 줄 바로 아래다
    assert out[:3] == [
        "10/14 18:00 38.9℃",
        "10/14 23:30 물약 먹였다고 기록하셨어요",
        "10/14 22:00 38.1℃",
    ]
    assert "때문" not in " ".join(out)
    assert out.count("10/14 23:30 물약 먹였다고 기록하셨어요") == 1  # 두 번 나오지 않는다


def test_연결된_증상이_보이지_않으면_시각순에_둔다():
    # 연결된 증상 행이 체온 줄로 나오지 않는다 — 약 줄을 버리지 않고 시간 흐름에 둔다
    symptom_only = reading(None, at=kst(10, 14, 18, 0), symptoms=("열나요",))
    linked = given("물약", at=kst(10, 14, 19, 0), linked_symptom_id=symptom_only.id)

    out = bodies(build([symptom_only, linked, reading("38.2", measured=kst(10, 14, 20, 0))]))

    assert out[:2] == ["10/14 19:00 물약 먹였다고 기록하셨어요", "10/14 20:00 38.2℃"]


def test_약_계열_단어가_없다():
    first = reading("39.0", measured=kst(10, 14, 18, 0), site="rectal", symptoms=("열나요",))
    course, doses = course_with_doses(
        "항생제", PATTERN, D14 - DAY, D15 + DAY, checked={(D14, time(8, 30))}
    )
    linked = given("물약", at=kst(10, 14, 19, 0), linked_symptom_id=first.id)
    loose = given("시럽", day=D15)

    out = " / ".join(bodies(build([first, linked, loose], [course], doses)))

    forbidden = (
        "성분",
        "계열",
        "투약 가능",
        "먹여도",
        "다시 먹일",
        "때문",
        "잡히고",
        "나아",
        "심해",
        "아세트아미노펜",
        "이부프로펜",
        "해열",
        "간격",
        "정상 범위",
        "정상이",
        "위험",
        "의심",
    )
    assert not [word for word in forbidden if word in out]


def test_전부_코드가_쓴_fever_timeline_이다():
    course, doses = course_with_doses("항생제", PATTERN, D14, D15, checked={(D14, time(8, 30))})
    obs = reading("38.5", measured=kst(10, 14, 20, 0))

    out = build([obs, given("물약", at=kst(10, 14, 21, 0))], [course], doses)

    assert out
    assert {r.kind for r in out} == {"fever_timeline"}
    assert {r.authored_by for r in out} == {"code"}


def test_줄마다_근거_기록을_가리킨다():
    course, doses = course_with_doses("항생제", PATTERN, D14, D15, checked={(D14, time(8, 30))})
    obs = reading("38.5", measured=kst(10, 14, 20, 0))
    pill = given("물약", at=kst(10, 14, 21, 0))

    out = {r.body: r.source_refs for r in build([obs, pill], [course], doses)}

    assert out["10/14 20:00 38.5℃"] == (Ref("observation_health", obs.id),)
    assert out["10/14 21:00 물약 먹였다고 기록하셨어요"] == (Ref("observation_health", pill.id),)
    assert out["마지막으로 체크된 복약 항생제 (10/14 08:30 회차)"] == (
        Ref("medication_schedule", course.id),
    )


class TestInfantFever:
    def check(self, observations, *, months=2) -> bool:
        return infant_fever(observations, months=months, now=NOW, timezone=KST)

    def test_3개월_미만_열_증상이면_참(self):
        obs = reading(None, at=kst(10, 14, 22, 0), symptoms=("열나요",))

        assert self.check([obs]) is True

    def test_3개월_미만_체온이_기준_이상이면_참(self):
        assert self.check([reading("38.0", measured=kst(10, 14, 22, 0))]) is True

    def test_체온이_기준_미만이면_거짓(self):
        assert self.check([reading("37.9", measured=kst(10, 14, 22, 0))]) is False

    def test_3개월_이상이면_거짓(self):
        obs = reading("39.5", measured=kst(10, 14, 22, 0), symptoms=("열나요",))

        assert self.check([obs], months=3) is False
        assert self.check([obs], months=2) is True

    def test_48시간_밖이면_거짓(self):
        old = reading("39.5", measured=kst(10, 12, 12, 0), symptoms=("열나요",))

        assert self.check([old]) is False

    def test_열이_없다고_한_기록은_거짓(self):
        obs = reading(None, at=kst(10, 14, 22, 0), symptoms=("열이 없어요",))

        assert self.check([obs]) is False

    def test_다른_증상만이면_거짓(self):
        obs = reading(None, at=kst(10, 14, 22, 0), symptoms=("기침",))

        assert self.check([obs]) is False

    def test_시각을_말하지_않은_기록은_날짜로_본다(self):
        obs = reading(None, day=D14, symptoms=("열나요",))

        assert self.check([obs]) is True

    def test_기록이_없으면_거짓(self):
        assert self.check([]) is False

    def test_타임존_UTC_시각을_아이_날짜로(self):
        # UTC 10/13 00:30 = KST 10/13 09:30 — 48시간 경계(10/13 09:00) 안이다
        obs = reading("38.5", measured=datetime(2026, 10, 13, 0, 30, tzinfo=UTC))

        assert self.check([obs]) is True
