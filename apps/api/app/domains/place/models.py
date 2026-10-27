"""장소 표 `place` — 공원 · 어린이놀이시설 · 도서관을 한 표에 두고 `source` 로 가른다
(Activity D9 · #516).

아이와 무관한 공용 데이터라 `child_id` 가 없다. 보호자 좌표는 이 표에 들어오지 않는다 — 요청 안에서
거리 계산에만 쓴다(`agent_reader.py`).

🚨 화이트리스트를 칸으로 강제한다. 전화번호 · 관리기관 · 주소 칸이 없어서 샐 수 없다. 칸을 늘리려면
   D9 부터 고친다. 실내 여부 칸도 없다 — `category` 가 정한다(도서관은 실내).
🚨 장소 행은 근거(`suggestion_evidence`)가 아니다. 날씨처럼 필터 조건이라 다른 표가 이 표를 가리키지
   않는다 — 그래서 대리 키 없이 (source, source_id) 가 곧 기본 키(= UNIQUE)다. 인덱스는 이것뿐이다.
   반경 검색은 인덱스 없이 실측 5~6ms 라, 실제 데이터로 EXPLAIN 을 본 뒤 단다 (#516).
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Double, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

# 마이그레이션과 같은 글자다. 값을 바꾸면 둘 다 고친다
# source 는 Activity 포트(`PlaceRow.source`)의 세 값이다. 어린이놀이시설은 아직 싣지 않는다 —
# 어느 설치장소를 공공으로 볼지 PM 결정 뒤 (#516)
SOURCE_CHECK = "source IN ('city_park', 'kids_play_facility', 'library')"
# Activity `PlaceCategory` 의 다섯 값이다 — 모델이 고르는 닫힌 enum 과 같다
CATEGORY_CHECK = (
    "category IN ('park', 'playground', 'library', 'indoor_playground', 'experience_center')"
)
SIGUNGU_CHECK = "sigungu_code IS NULL OR sigungu_code ~ '^[0-9]{5}$'"
# 적재 스크립트가 거르는 한국 상자와 같다(`scripts/load_places.py` KOREA_LAT · KOREA_LNG).
# 위경도가 뒤바뀐 행이 들어오면 거리가 조용히 틀린다
KOREA_BOX_CHECK = "lat BETWEEN 33 AND 39 AND lng BETWEEN 124 AND 132"


class Place(Base):
    """장소 한 곳. 적재 스크립트만 쓴다 — 요청 중에는 읽기만 한다."""

    __tablename__ = "place"
    __table_args__ = (
        CheckConstraint(SOURCE_CHECK, name="place_source"),
        CheckConstraint(CATEGORY_CHECK, name="place_category"),
        CheckConstraint(SIGUNGU_CHECK, name="place_sigungu_code"),
        CheckConstraint(KOREA_BOX_CHECK, name="place_korea_box"),
    )

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    # 소스 안에서 한 곳을 가리키는 값. 공원은 관리번호, 도서관은 번호 칸이 없어 이름 + 좌표로
    # 만든다(`scripts/load_places.py`)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    lat: Mapped[float] = mapped_column(Double, nullable=False)
    lng: Mapped[float] = mapped_column(Double, nullable=False)
    # 시군구 코드 5자리. 공원은 관리번호 앞 5자리, 도서관은 아직 없다(시군구 이름만 온다)
    sigungu_code: Mapped[str | None] = mapped_column(String(5))
    # 데이터기준일자 — 화면의 "공공데이터 기준 YYYY-MM" (D9). 갱신이 느려 "지금 열려 있어요" 는
    # 쓸 수 없다
    data_date: Mapped[date | None] = mapped_column(Date)
    source_version: Mapped[str | None] = mapped_column(String(32))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
