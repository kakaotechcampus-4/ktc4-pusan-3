"""공지 조립 규칙. 모델이 낸 값을 믿지 않는다. 🚨 공지 글은 지어낸 것이다."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.notice_extract import ExtractedItem, ItemDetail, NoticeExtract
from app.domains.notice.assemble import (
    MAX_DETAIL_VALUE,
    MAX_DETAILS,
    assemble,
    content_hash,
    mask_names,
    same_notice,
    to_event_draft,
)
from app.domains.notice.models import NoticeInputFormat

KST = ZoneInfo("Asia/Seoul")
RECEIVED = datetime(2026, 10, 27, 3, tzinfo=UTC)


def _item(**overrides) -> ExtractedItem:
    values = {
        "handling": "calendar",
        "label": "행사",
        "title": "숲 체험",
        "target_scope": "all",
        "date_text": "11월 6일(금)",
        "due_text": None,
        "details": [],
        "supplies": [],
        "source_quote": "숲 체험",
    }
    return ExtractedItem(**{**values, **overrides})


def _build(*items: ExtractedItem, source: str = "숲 체험 안내", issued: str | None = None, **kw):
    extract = NoticeExtract(
        doc_kind="newsletter",
        audience_scope="institution",
        title=None,
        issued_text=issued,
        is_correction=False,
        person_names=kw.get("people", []),
        institution_names=kw.get("orgs", []),
        items=list(items),
    )
    return assemble(
        extract,
        source_text=source,
        input_format=NoticeInputFormat.TEXT,
        received_at=RECEIVED,
        tz=KST,
    )


def test_종일_일정은_일정_쪽_관례대로_끝까지_채운다():
    item = _build(_item()).items[0]
    assert item.all_day is True
    assert item.starts_at.astimezone(KST).hour == 0
    assert item.ends_at.astimezone(KST).hour == 23
    assert to_event_draft(item).event.ends_at == item.ends_at


def test_시각이_있으면_범위로_저장한다():
    item = _build(_item(date_text="11월 6일(금) 9:30~12:00")).items[0]
    assert item.all_day is False
    assert item.starts_at.astimezone(KST).hour == 9
    assert item.ends_at.astimezone(KST).hour == 12


def test_끝_날에_일_글자가_없는_기간은_첫날_하루가_아니다():
    item = _build(_item(date_text="11/23(월)~27(금)")).items[0]
    assert item.all_day is True
    assert item.starts_at.astimezone(KST).date() == date(2026, 11, 23)
    assert item.ends_at.astimezone(KST).date() == date(2026, 11, 27)
    assert item.needs_review is False


def test_끝이_시작보다_빠르면_끝을_버린다():
    # 제출 계약은 끝 > 시작 을 요구한다. 초안이 422 로 막히면 보호자가 고칠 곳을 모른다
    item = _build(_item(date_text="11월 6일 14:00~10:00")).items[0]
    assert item.ends_at is None


def test_시각_없는_마감은_날짜만_마감이다():
    item = _build(_item(handling="todo", date_text=None, due_text="11월 3일(화)")).items[0]
    assert item.due_all_day is True
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 3)


def test_시각_있는_마감은_시각까지_남긴다():
    item = _build(_item(handling="todo", date_text=None, due_text="11월 3일(화) 14시")).items[0]
    assert item.due_all_day is False
    assert item.due_at.astimezone(KST).hour == 14


def test_발행일에서_연도를_빌린다():
    notice = _build(_item(date_text="1월 5일"), issued="2026. 12. 20.")
    assert notice.issued_on == date(2026, 12, 20)
    assert notice.items[0].starts_at.astimezone(KST).date() == date(2027, 1, 5)


def test_달력_항목에_날짜가_없으면_확인을_요청한다():
    item = _build(_item(date_text=None)).items[0]
    assert item.needs_review is True
    assert to_event_draft(item) is None


def test_근거가_원문에_없으면_확인을_요청한다():
    item = _build(_item(source_quote="없는 문장")).items[0]
    assert item.needs_review is True


def test_표_구분_기호는_근거_대조에서_무시한다():
    item = _build(_item(source_quote="일 시 | 11월 6일"), source="일시 11월 6일").items[0]
    assert item.needs_review is False


def test_여러_줄_인용은_줄마다_대조한다():
    source = "소풍 안내\n준비물은 물통\n문의는 교무실"
    assert (
        _build(_item(source_quote="소풍 안내\n문의는 교무실"), source=source).items[0].needs_review
        is False
    )


def test_안내는_날짜를_못_읽어도_확인을_묻지_않는다():
    item = _build(_item(handling="info", date_text="3월 한 달 간")).items[0]
    assert item.needs_review is False
    assert item.starts_at is None


def test_할_일은_날짜를_못_읽으면_확인을_묻는다():
    item = _build(_item(handling="todo", date_text=None, due_text="심사 2일 전")).items[0]
    assert item.needs_review is True


def test_할_일의_날짜는_마감이다():
    item = _build(_item(handling="todo", date_text="11월 3일(화)")).items[0]
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 3)


def test_부터는_마감이_아니다():
    item = _build(_item(handling="todo", date_text="11월 3일(화) 10시부터")).items[0]
    assert item.due_at is None


def test_기간_마감은_끝_날의_시각이다():
    item = _build(
        _item(handling="todo", date_text=None, due_text="10월 27일 10:00 ~ 11월 17일 18:00")
    ).items[0]
    due = item.due_at.astimezone(KST)
    assert (due.month, due.day, due.hour) == (11, 17, 18)


def _dates(item):
    return (
        item.starts_at,
        item.ends_at,
        item.all_day,
        item.due_at,
        item.due_all_day,
        item.needs_review,
    )


def test_안내의_기간은_마감_칸에_적어도_일시_칸에_적은_것과_같다():
    # 모델은 "신청 기간 10/27 10:00 ~ 11/17 18:00" 을 마감 칸에도 적는다. 시작을 버리면 안 된다
    period = "10월 27일(화) 10:00 ~ 11월 17일(화) 18:00"
    as_when = _build(_item(handling="info", date_text=period, due_text=None)).items[0]
    as_due = _build(_item(handling="info", date_text=None, due_text=period)).items[0]
    assert as_due.starts_at.astimezone(KST) == datetime(2026, 10, 27, 10, tzinfo=KST)
    assert as_due.ends_at.astimezone(KST) == datetime(2026, 11, 17, 18, tzinfo=KST)
    assert _dates(as_due) == _dates(as_when)


def test_할_일의_마감_칸_기간은_마감으로_둔다():
    # 할 일은 마감이 분명히 보여야 한다. 시작이 생기면 화면 이름표가 "마감" 에서 "언제" 로 바뀌고,
    # 보호자가 날짜를 고쳐도 마감이 따라 바뀌지 않는다 (검토) — 전처럼 끝 날 마감만 둔다
    item = _build(
        _item(handling="todo", date_text=None, due_text="10월 27일(화) 10:00 ~ 11월 17일(화) 18:00")
    ).items[0]
    assert item.starts_at is None
    assert item.due_at.astimezone(KST) == datetime(2026, 11, 17, 18, tzinfo=KST)
    assert item.needs_review is False


def test_일정의_마감_칸_기간은_행사_기간으로_옮기지_않는다():
    # 일정 항목의 마감 칸 기간은 대개 신청 · 회신 기간이다. 기간 일정으로 바꾸면 틀린 일정이
    # 확인 없이 들어간다 — 전처럼 시작을 되묻고 끝 날을 마감으로 둔다
    item = _build(
        _item(date_text=None, due_text="10월 27일(화) 10:00 ~ 11월 17일(화) 18:00")
    ).items[0]
    assert item.starts_at is None
    assert item.needs_review is True
    assert item.review_reason == "날짜를 찾지 못했어요"
    assert item.due_at.astimezone(KST) == datetime(2026, 11, 17, 18, tzinfo=KST)


def test_끝이_열린_표현은_마감이_아니다():
    item = _build(
        _item(handling="todo", date_text=None, due_text="10월 27일 10:00 ~ 선착순마감")
    ).items[0]
    assert item.due_at is None
    assert item.starts_at.astimezone(KST).date() == date(2026, 10, 27)
    assert item.needs_review is True


def test_까지만_있는_일정은_시작으로_쓰지_않는다():
    item = _build(_item(date_text="2027년 4월 30일까지 시행")).items[0]
    assert item.starts_at is None
    assert item.due_at.astimezone(KST).date() == date(2027, 4, 30)
    assert item.needs_review is True
    assert to_event_draft(item) is None


def test_안내는_못_믿을_날짜를_남기지_않는다():
    item = _build(_item(handling="info", date_text="내일과 모레")).items[0]
    assert item.starts_at is None
    assert item.needs_review is False


def test_일반_명사는_가리지_않는다():
    masked = mask_names(
        "어린이집 · 학교 · 해바라기어린이집",
        people=[],
        institutions=["어린이집", "학교", "해바라기어린이집"],
    )
    assert masked == "어린이집 · 학교 · [기관]"


def test_한두_글자_다른_인용은_찾은_것으로_본다():
    source = "소풍은 금요일 오전에 출발합니다"
    assert (
        _build(_item(source_quote="소풍은 금요일 오천에 출발합니다"), source=source)
        .items[0]
        .needs_review
        is False
    )


def test_지어낸_인용은_찾지_못한다():
    source = "소풍은 금요일 오전에 출발합니다"
    assert (
        _build(_item(source_quote="참가비는 만 원입니다"), source=source).items[0].needs_review
        is True
    )


def test_신청이_열리는_시각은_마감이_아니다():
    quote = "상담 신청 시작일시는 11월 3일 오전 10시입니다"
    item = _build(
        _item(handling="todo", date_text="11월 3일 오전 10시", source_quote=quote), source=quote
    ).items[0]
    assert item.due_at is None
    assert item.starts_at.astimezone(KST).hour == 10


def test_준비물을_정리한다():
    item = _build(_item(supplies=[" 물통 ", "물통", "", "가" * 101])).items[0]
    assert item.supplies == ("물통",)


def test_이름과_전화번호를_가린다():
    masked = mask_names(
        "해바라기어린이집 김가나 선생님 02-123-4567",
        people=["김가나"],
        institutions=["해바라기어린이집"],
    )
    assert masked == "[기관] [이름] 선생님 [전화]"


def test_다니는_기관_이름만_가린다():
    masked = mask_names(
        "해바라기어린이집 달님반 8,000원 · 장소: 솔숲공원 · 무지개유치원",
        people=[],
        institutions=["해바라기어린이집", "달님반", "솔숲공원", "무지개유치원"],
    )
    assert masked == "[기관] 달님반 8,000원 · 장소: 솔숲공원 · [기관]"


def test_어느_반이_얼마인지는_제목에_남는다():
    title = "해님반 8,000원 / 달님반 12,000원"
    item = _build(_item(title=title, source_quote=title), source=title, orgs=["해님반", "달님반"])
    assert item.items[0].title == title


def test_끝이_다른_기관_이름은_기관_이름까지만_가린다():
    masked = mask_names(
        "무지개유치원 운영위원회 회의", people=[], institutions=["무지개유치원 운영위원회"]
    )
    assert masked == "[기관] 운영위원회 회의"


def test_목록에_있는_약칭도_가린다():
    masked = mask_names(
        "[해바라기] 해바라기 가족 여러분",
        people=[],
        institutions=["해바라기어린이집", "해바라기"],
    )
    assert masked == "[[기관]] [기관] 가족 여러분"


def test_목록에_없는_약칭도_가린다():
    masked = mask_names(
        "[해바라기] 해바라기 가족 여러분, 해바라기반", people=[], institutions=["해바라기어린이집"]
    )
    assert masked == "[[기관]] [기관] 가족 여러분, 해바라기반"


def test_두_글자_앞부분은_낱말일_수_있어_가리지_않는다():
    masked = mask_names("새싹이 돋았어요", people=[], institutions=["새싹어린이집"])
    assert masked == "새싹이 돋았어요"


def test_약칭이_반_이름을_지우지_않는다():
    masked = mask_names("해님어린이집 해님반", people=[], institutions=["해님어린이집", "해님"])
    assert masked == "[기관] 해님반"


@pytest.mark.parametrize(
    ("name", "text"),
    [("하나", "하나씩 챙겨 주세요"), ("보라", "보라색 옷"), ("지우", "지우개를 가져오세요")],
)
def test_짧은_이름이_낱말을_지우지_않는다(name, text):
    assert mask_names(text, people=[name], institutions=[]) == text


def test_짧은_이름은_호칭이나_조사가_붙어도_가린다():
    masked = mask_names(
        "하늘이가 웃어요. 지우 어린이. 민준아", people=["하늘", "지우", "민준"], institutions=[]
    )
    assert masked == "[이름]이가 웃어요. [이름] 어린이. [이름]아"


def test_글자_칸은_전부_가리기를_지난다():
    item = _build(
        _item(
            label="김가나 회신",
            date_text="김가나 상담 11월 6일(금)",
            due_text="김가나 선생님께 11월 5일까지",
            details=[ItemDetail(name="제출처", value="담임 김가나 선생님 02-123-4567")],
            supplies=["김가나 명찰"],
        ),
        people=["김가나"],
    ).items[0]
    saved = " ".join(
        [
            item.label,
            *item.supplies,
            *(detail.value for detail in item.details),
        ]
    )
    assert "김가나" not in saved
    assert "02-123-4567" not in saved


def test_details_는_빈_값과_중복을_뺀다():
    item = _build(
        _item(
            details=[
                ItemDetail(name=" 장소 ", value=" 숲체험장 "),
                ItemDetail(name="장소", value="숲체험장"),
                ItemDetail(name="비고", value="   "),
                ItemDetail(name="", value="값만 있음"),
            ]
        )
    ).items[0]
    assert [(d.name, d.value) for d in item.details] == [
        ("장소", "숲체험장"),
        ("안내", "값만 있음"),
    ]


def test_details_는_개수와_길이를_제한한다():
    item = _build(
        _item(details=[ItemDetail(name=f"이름{i}", value="값" * 500) for i in range(30)])
    ).items[0]
    assert len(item.details) == MAX_DETAILS
    assert all(len(detail.value) <= MAX_DETAIL_VALUE for detail in item.details)
    assert item.details[0].value.endswith("…")


PICNIC = "가을 소풍 안내 11월 6일(금) 숲속 놀이터. 참여는 Zoom 으로도 가능합니다."


def test_제목에_섞인_모델의_혼잣말은_괄호_앞까지만_남긴다():
    # 모델이 값을 고르며 한 생각이 제목에 들어간다 (#909 와 같은 종류)
    item = _build(
        _item(
            title="가을 소풍 (숲속 놀이터, Note 포함 Summary 겸함이나 주 일정은 소풍임.)",
            source_quote="가을 소풍 안내",
        ),
        source=PICNIC,
    ).items[0]
    assert item.title == "가을 소풍"
    assert item.needs_review is False


def test_괄호_앞에도_혼잣말이_남으면_확인을_요청한다():
    item = _build(
        _item(title="Picnic day 가을 소풍 (숲속)", source_quote="가을 소풍 안내"), source=PICNIC
    ).items[0]
    assert item.needs_review is True
    assert "제목" in item.review_reason


def test_원문에_있는_영문은_제목에_남는다():
    item = _build(
        _item(title="가을 소풍 (ZOOM 참여 가능)", source_quote="가을 소풍 안내"), source=PICNIC
    ).items[0]
    assert item.title == "가을 소풍 (ZOOM 참여 가능)"
    assert item.needs_review is False


def test_details_에_섞인_모델의_혼잣말은_그_줄을_버린다():
    item = _build(
        _item(
            source_quote="가을 소풍 안내",
            details=[
                ItemDetail(name="장소", value="숲속 놀이터 (놀이터Based on text: use exact words)"),
                ItemDetail(name="참여", value="Zoom 으로도 가능"),
            ],
        ),
        source=PICNIC,
    ).items[0]
    assert [(d.name, d.value) for d in item.details] == [("참여", "Zoom 으로도 가능")]


LEGIT = (
    "줌 학부모 상담 안내. 코로나19 예방접종. 영어 특강."
    " 카카오톡 · 키즈노트 앱으로 회신. information desk"
)


@pytest.mark.parametrize("title", ["Zoom 학부모 상담", "COVID-19 예방접종", "English 특강"])
def test_원문에_한글로만_있는_말을_영문으로_적은_제목은_그대로다(title):
    # 낱말 하나는 모델이 옮겨 적은 정당한 값일 수 있다. 혼잣말은 영어 구절로 나온다
    item = _build(_item(title=title, source_quote="줌 학부모 상담 안내"), source=LEGIT).items[0]
    assert item.title == title
    assert item.needs_review is False


@pytest.mark.parametrize("value", ["Zoom 화상 상담", "카카오톡(KakaoTalk)", "KidsNote 앱"])
def test_원문에_한글로만_있는_말을_영문으로_적은_details_는_그대로다(value):
    item = _build(
        _item(source_quote="줌 학부모 상담 안내", details=[ItemDetail(name="방법", value=value)]),
        source=LEGIT,
    ).items[0]
    assert [d.value for d in item.details] == [value]


def test_낱말_단위로_대조한다():
    # 원문의 information 때문에 Info · form 이 통과하면 안 된다
    item = _build(
        _item(title="학부모 상담 (Info form 겸함)", source_quote="줌 학부모 상담 안내"),
        source=LEGIT,
    ).items[0]
    assert item.title == "학부모 상담"


def test_전각_영문도_원문과_맞춘다():
    source = "ＺＯＯＭ ＭＥＥＴＩＮＧ 학부모 상담"
    item = _build(
        _item(title="Zoom Meeting 학부모 상담", source_quote="학부모 상담"), source=source
    ).items[0]
    assert item.title == "Zoom Meeting 학부모 상담"
    assert item.needs_review is False


def test_괄호로_시작하는_제목은_자르지_않고_확인을_요청한다():
    item = _build(
        _item(title="(필독) 겨울 방학식 Note Summary 겸함", source_quote="가을 소풍 안내"),
        source=PICNIC,
    ).items[0]
    assert item.title == "(필독) 겨울 방학식 Note Summary 겸함"
    assert item.needs_review is True


def test_날짜가_details_로_새면_확인을_요청한다():
    item = _build(
        _item(
            handling="todo",
            date_text=None,
            details=[ItemDetail(name="개강일", value="6월 24일(수) 6시 30분")],
        )
    ).items[0]
    assert item.needs_review is True
    assert item.review_reason == "날짜를 항목에 넣지 못했어요"


def test_날짜가_있는_항목의_details_날짜는_그대로_둔다():
    item = _build(_item(details=[ItemDetail(name="연기일", value="11월 20일(금)")])).items[0]
    assert item.needs_review is False


def test_안내_항목의_details_날짜는_묻지_않는다():
    item = _build(
        _item(
            handling="info",
            date_text=None,
            details=[ItemDetail(name="연기일", value="11월 20일(금)")],
        )
    ).items[0]
    assert item.needs_review is False
    assert item.starts_at is None


def test_시각이_details_로_새면_확인을_요청한다():
    item = _build(
        _item(
            date_text="11월 20일(금)",
            details=[ItemDetail(name="시간", value="9:30~12:00")],
        )
    ).items[0]
    assert item.all_day is True
    assert item.needs_review is True
    assert item.review_reason == "시각을 항목에 넣지 못했어요"


def test_등원_시각은_일정_시각이_아니다():
    item = _build(
        _item(
            date_text="11월 20일(금)",
            details=[ItemDetail(name="등교시간", value="9시까지")],
        )
    ).items[0]
    assert item.needs_review is False


def test_할_일의_일시_칸에_적힌_월_내도_마감이다():
    item = _build(_item(handling="todo", date_text="11월 내로", due_text=None)).items[0]
    assert item.needs_review is False
    assert item.starts_at is None
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 30)


def test_일정의_일시_칸에_적힌_월_내는_되묻는다():
    item = _build(_item(date_text="11월 내로")).items[0]
    assert item.needs_review is True
    assert item.starts_at is None


@pytest.mark.parametrize(
    "written",
    [{"date_text": "내일", "due_text": None}, {"date_text": None, "due_text": "내일"}],
)
def test_할_일의_하루_날짜는_어느_칸에_적혀도_같게_저장한다(written):
    item = _build(_item(handling="todo", **written)).items[0]
    assert item.starts_at is None
    assert item.due_at.astimezone(KST).date() == date(2026, 10, 28)


def test_할_일의_시각도_마감에_남는다():
    item = _build(_item(handling="todo", date_text="11월 6일(금) 14시", due_text=None)).items[0]
    assert item.starts_at is None
    assert item.due_at.astimezone(KST).hour == 14
    assert item.due_all_day is False


def test_할_일의_기간은_시작을_남긴다():
    item = _build(
        _item(handling="todo", date_text="11월 3일(화) ~ 11월 7일(토)", due_text=None)
    ).items[0]
    assert item.starts_at.astimezone(KST).date() == date(2026, 11, 3)
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 7)


@pytest.mark.parametrize("hour", [9, 18])
def test_일정의_시각_까지는_그날_하루이고_시각은_마감이다(hour):
    # "9시까지 등원" 의 9시는 행사 시작이 아니다. 시작 시각을 지어내지 않고 마감으로 둔다
    item = _build(_item(date_text=f"11월 6일(금) {hour}시까지")).items[0]
    assert item.needs_review is False
    assert item.all_day is True
    assert item.starts_at.astimezone(KST).date() == date(2026, 11, 6)
    assert item.due_at.astimezone(KST) == datetime(2026, 11, 6, hour, tzinfo=KST)
    assert item.due_all_day is False


WRITTEN = "적힌 대로"


def _written(item) -> list[str]:
    return [d.value for d in item.details if d.name == WRITTEN]


def test_일정의_시각_까지는_적힌_대로_details_에_남긴다():
    # 확인 화면은 일정의 시작만 보여 준다. 9시는 마감으로 옮겨져 "9시까지 등원" 이 화면에서 사라진다
    written = "11월 20일(금) 9시까지 등원"
    plain = _build(_item(date_text="11월 20일(금)")).items[0]
    item = _build(_item(date_text=f"  {written} ")).items[0]
    assert item.all_day is True
    assert item.starts_at.astimezone(KST).date() == date(2026, 11, 20)
    assert item.due_at.astimezone(KST) == datetime(2026, 11, 20, 9, tzinfo=KST)
    assert item.due_all_day is False
    assert item.needs_review is plain.needs_review is False
    assert _written(item) == [written]


def test_일정의_마감_칸이_따로_있어도_까지_시각은_적힌_대로_남긴다():
    # 마감 칸 값이 마감이 되고 9시는 어디에도 남지 않는다
    item = _build(_item(date_text="11월 20일(금) 9시까지 등원", due_text="11월 13일(금)")).items[0]
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 13)
    assert _written(item) == ["11월 20일(금) 9시까지 등원"]


@pytest.mark.parametrize(
    "written",
    [
        {"date_text": "11월 20일(금) 오전 10시"},  # 시작 시각이 화면에 보인다
        {"date_text": "11월 20일(금)"},  # 시각이 없다
        {"date_text": "11월 20일(금) 7시까지"},  # 7시는 비우고 되묻는다 — 옮긴 시각이 없다
        {"date_text": "4월 30일까지 시행"},  # 날짜만 있는 까지는 마감으로 보인다
    ],
)
def test_일정에서_시각이_화면에_보이거나_옮기지_않았으면_적힌_대로는_없다(written):
    assert _written(_build(_item(**written)).items[0]) == []


def test_할_일의_시각_까지는_마감으로_보여_적힌_대로를_넣지_않는다():
    item = _build(_item(handling="todo", date_text="11월 20일(금) 9시까지 제출")).items[0]
    assert item.due_at.astimezone(KST) == datetime(2026, 11, 20, 9, tzinfo=KST)
    assert _written(item) == []


def test_안내의_시각_까지는_화면에_보여_적힌_대로를_넣지_않는다():
    # 안내는 시작이 없으면 마감 날짜 · 시각을 그대로 보여 준다
    item = _build(_item(handling="info", date_text="11월 20일(금) 9시까지 등원")).items[0]
    assert item.starts_at is None
    assert item.due_at.astimezone(KST) == datetime(2026, 11, 20, 9, tzinfo=KST)
    assert _written(item) == []


def test_모델이_이미_details_에_적었으면_적힌_대로를_다시_넣지_않는다():
    item = _build(
        _item(
            date_text="11월 20일(금) 9시까지 등원",
            details=[ItemDetail(name="등원", value="11월20일 (금) 9시 까지 등원")],
        )
    ).items[0]
    assert _written(item) == []
    assert len(item.details) == 1


def test_적힌_대로는_details_개수_상한에서도_남는다():
    item = _build(
        _item(
            date_text="11월 20일(금) 9시까지 등원",
            details=[ItemDetail(name=f"이름{i}", value=f"값{i}") for i in range(30)],
        )
    ).items[0]
    assert len(item.details) == MAX_DETAILS
    assert _written(item) == ["11월 20일(금) 9시까지 등원"]


def test_적힌_대로는_시각_검사를_바꾸지_않는다():
    # 종일 일정 + 이름이 "시간" 인 details 의 시각 → 확인 요청. 적힌 대로 줄은 이 검사를 타지 않는다
    leaked = _build(
        _item(
            date_text="11월 20일(금) 9시까지 등원",
            details=[ItemDetail(name="시간", value="9:30~12:00")],
        )
    ).items[0]
    assert leaked.review_reason == "시각을 항목에 넣지 못했어요"
    assert _written(leaked) == ["11월 20일(금) 9시까지 등원"]
    quiet = _build(_item(date_text="11월 20일(금) 9시까지 등원")).items[0]
    assert quiet.needs_review is False


ARRIVE = "오전 9:00까지 등원해 주세요."
FOREST = f"숲 체험 안내\n11월 20일(금) 숲 체험이 있어요.\n{ARRIVE}\n간식은 원에서"


def _until(quote: str, **kw):
    return _build(
        _item(date_text="11월 20일(금) 9시까지", source_quote=quote, **kw), source=FOREST
    ).items[0]


@pytest.mark.parametrize(
    ("quote", "shown"),
    [
        # 무엇의 마감인지는 근거 문장에 있다. 같은 시각이면 글자 모양이 달라도(9시 · 9:00) 쓴다
        (ARRIVE, ARRIVE),
        # 여러 줄 근거는 그 시각이 든 줄만 — 다른 줄은 다른 이야기다
        (f"11월 20일(금) 숲 체험이 있어요.\n{ARRIVE}", ARRIVE),
    ],
)
def test_까지_시각을_담은_근거_문장_줄을_적힌_대로로_쓴다(quote, shown):
    item = _until(quote)
    assert _written(item) == [shown]
    assert item.needs_review is False


@pytest.mark.parametrize(
    "quote",
    [
        "11월 20일(금) 숲 체험이 있어요.",  # 시각이 없다
        "간식은 원에서 10시까지",  # 다른 시각이다 (원문에도 없어 확인을 묻는다)
        "오전 9시까지 와 주세요 꼭",  # 원문에 없는 근거 문장
    ],
)
def test_근거_문장이_그_시각을_못_담거나_원문에_없으면_날짜_칸_글을_쓴다(quote):
    assert _written(_until(quote)) == ["11월 20일(금) 9시까지"]


def test_근거_문장의_그_줄이_길이_상한을_넘으면_날짜_칸_글을_쓴다():
    # 잘리면 시각이 사라진다 — 긴 한 줄 공지의 끝에 시각이 있는 일이 실제로 있다
    line = "가" * MAX_DETAIL_VALUE + " 9시까지 등원"
    item = _build(_item(date_text="11월 20일(금) 9시까지", source_quote=line), source=line).items[0]
    assert _written(item) == ["11월 20일(금) 9시까지"]


@pytest.mark.parametrize("value", ["9시까지 등교 부탁드리며", "오전 9:00 까지 와 주세요"])
def test_모델이_같은_시각의_까지를_details_에_적었으면_적힌_대로를_넣지_않는다(value):
    item = _until(ARRIVE, details=[ItemDetail(name="등원", value=value)])
    assert [(d.name, d.value) for d in item.details] == [("등원", value)]


@pytest.mark.parametrize("value", ["9시 출발", "10시까지 하원"])
def test_details_의_시각이_까지가_아니거나_다른_시각이면_적힌_대로를_넣는다(value):
    item = _until(ARRIVE, details=[ItemDetail(name="안내", value=value)])
    assert _written(item) == [ARRIVE]


OPEN_DUE = "10월 1일(목) 10:00 ~ 선착순마감"


@pytest.mark.parametrize("handling", ["calendar", "info"])
def test_일시가_있으면_버리는_끝이_열린_마감을_적힌_대로_남긴다(handling):
    # 일시 칸의 시작을 두고 마감 칸을 버린다. 신청이 열리는 때가 사라지지 않게 원문을 남긴다
    event = {"handling": handling, "date_text": "11월 13일(금) 10:00"}
    item = _build(_item(**event, due_text=f" {OPEN_DUE}  ")).items[0]
    without = _build(_item(**event, due_text=None)).items[0]
    assert item.due_at is None
    assert _dates(item) == _dates(without)
    assert item.review_reason == without.review_reason
    assert _written(item) == [OPEN_DUE]


def test_할_일의_끝이_열린_마감도_적힌_대로_남기고_확인은_그대로_묻는다():
    raw = _item(handling="todo", date_text="10월 5일(월) 10:00", due_text=OPEN_DUE)
    item = _build(raw).items[0]
    # 할 일의 하루 날짜는 마감이 된다 — 전과 같다
    assert item.due_at.astimezone(KST) == datetime(2026, 10, 5, 10, tzinfo=KST)
    assert item.review_reason == "마감을 읽지 못했어요"
    assert _written(item) == [OPEN_DUE]


def test_일시가_없으면_끝이_열린_마감이_시작이라_적힌_대로는_없다():
    item = _build(_item(handling="todo", date_text=None, due_text=OPEN_DUE)).items[0]
    assert item.starts_at.astimezone(KST) == datetime(2026, 10, 1, 10, tzinfo=KST)
    assert _written(item) == []


def test_끝이_열린_마감을_모델이_이미_적었으면_한_줄만_남는다():
    item = _build(
        _item(
            date_text="11월 13일(금) 10:00",
            due_text=OPEN_DUE,
            details=[ItemDetail(name="신청", value="10월 1일(목) 10:00~선착순 마감")],
        )
    ).items[0]
    assert [(d.name, d.value) for d in item.details] == [("신청", "10월 1일(목) 10:00~선착순 마감")]


@pytest.mark.parametrize("count", [3, 30])
def test_끝이_열린_마감의_적힌_대로는_맨_뒤에_두고_개수_상한에서도_남는다(count):
    # 행사와 다른 날짜다. "언제" 바로 밑에 두면 날짜가 둘인 것처럼 읽힌다
    model = [ItemDetail(name=f"이름{i}", value=f"값{i}") for i in range(count)]
    item = _build(_item(date_text="11월 13일(금) 10:00", due_text=OPEN_DUE, details=model)).items[0]
    assert len(item.details) == min(count + 1, MAX_DETAILS)
    assert (item.details[-1].name, item.details[-1].value) == (WRITTEN, OPEN_DUE)
    assert item.details[0].value == "값0"


def test_까지_시각은_맨_앞_끝이_열린_마감은_맨_뒤다():
    model = [ItemDetail(name=f"이름{i}", value=f"값{i}") for i in range(30)]
    item = _build(_item(date_text="11월 20일(금) 9시까지", due_text=OPEN_DUE, details=model)).items[
        0
    ]
    assert len(item.details) == MAX_DETAILS
    assert item.details[0].value == "11월 20일(금) 9시까지"
    assert item.details[-1].value == OPEN_DUE
    assert _written(item) == ["11월 20일(금) 9시까지", OPEN_DUE]


def test_할_일의_7시는_되묻고_날짜만_남긴다():
    item = _build(_item(handling="todo", date_text="11월 6일(금) 7시까지")).items[0]
    assert item.needs_review is True
    assert item.review_reason == "오전인지 오후인지 적혀 있지 않아요"
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 6)
    assert item.due_all_day is True


def test_안내의_7시는_묻지_않고_날짜만_남긴다():
    item = _build(_item(handling="info", date_text="11월 6일(금) 7시")).items[0]
    assert item.needs_review is False
    assert item.all_day is True
    assert item.starts_at.astimezone(KST).date() == date(2026, 11, 6)


def test_일정의_날짜만_있는_까지는_되묻는다():
    item = _build(_item(date_text="4월 30일까지")).items[0]
    assert item.needs_review is True
    assert item.starts_at is None


def test_월_내_마감은_그달_말일이다():
    item = _build(_item(handling="todo", date_text=None, due_text="가급적 11월 내로")).items[0]
    assert item.needs_review is False
    assert item.due_at.astimezone(KST).date() == date(2026, 11, 30)


def test_이름이_겹쳐도_긴_이름부터_가린다():
    assert mask_names("김가나다", people=["가나", "김가나다"], institutions=[]) == "[이름]"


def test_한_글자_이름은_가리지_않는다():
    # 한 글자로 가리면 글 전체가 깨진다
    assert mask_names("김밥", people=["김"], institutions=[]) == "김밥"


def test_띄어쓰기가_달라도_같은_공지다():
    assert content_hash("숲 체험  안내") == content_hash("숲체험\n안내")


LONG_NOTICE = "숲 체험 안내입니다. 11월 13일(금) 9:30 출발합니다. 체육복과 물통을 챙겨 주세요. " * 3


def test_몇_글자_다르게_읽어도_같은_공지다():
    assert same_notice(LONG_NOTICE, LONG_NOTICE.replace("물통", "물동").replace(" ", "", 3))


def test_구분선_길이가_달라도_같은_공지다():
    assert same_notice(f"{LONG_NOTICE}\n-----\n끝", f"{LONG_NOTICE}\n---------------\n끝")


def test_날짜가_다르면_다른_공지다():
    assert not same_notice(LONG_NOTICE, LONG_NOTICE.replace("13일", "20일"))


def test_내용이_다르면_다른_공지다():
    assert not same_notice(LONG_NOTICE, "김장 체험 안내입니다. 11월 13일(금) 9:30 출발합니다.")
