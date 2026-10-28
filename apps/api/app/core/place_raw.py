"""장소 표준데이터 원문 계약. 클라이언트(`app/integrations/place/`)가 싣고 적재 스크립트가
읽는 모양.

`app/integrations/` 는 core 만 import 할 수 있어서 둘이 함께 보는 모양은 여기 둔다
(`core/mfds_nutri_raw.py` 와 같은 자리). 값은 전부 API 원문 문자열이다 — 좌표 읽기 · 거르기 ·
source_id 만들기는 `scripts/load_places.py` 가 테스트와 함께 맡는다.

🚨 칸이 곧 화이트리스트다. 전화번호 · 관리기관 · 주소는 응답에 있어도 이 모양에 칸이 없어서
   클라이언트 밖으로 나오지 못한다 (Activity D9).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RawPlaceRow:
    """표준데이터 한 행의 원문. 빈 칸은 None 이다."""

    name: str | None  # 공원명(parkNm) · 도서관명(lbrryNm)
    kind: str | None  # 공원구분(parkSe) · 도서관유형(lbrrySe)
    latitude: str | None
    longitude: str | None
    reference_date: str | None  # 데이터기준일자(referenceDate) — "2026-06-30"
    # 소스 안의 번호. 공원은 관리번호(manageNo) — "시군구코드5자리-일련번호5자리" 모양이고,
    # 어린이놀이시설은 시설 번호(pfctSn)다. 도서관 표준데이터에는 번호 칸이 없어 None 이다
    manage_no: str | None = None
    # 공원 지번주소(없으면 도로명). 관리번호가 겹친 행이 같은 공원인지 가리는 데만 쓰고
    # 싣지 않는다 — place 표에 주소 칸이 없다. 도서관은 None 이다
    address: str | None = None
    # 공원 유희시설(amsmtFclty) 원문. 놀이터가 있는지(`has_playground`)를 정하는 데만 쓰고 싣지
    # 않는다. 도서관은 None 이다
    amusement: str | None = None
    # 공원면적(parkAr, ㎡) 원문. 적재가 숫자로 읽어 `area_m2` 로 싣는다. 도서관은 None 이다
    area: str | None = None
    # 어린이놀이시설만 — 실내외 코드(idrodrCd: O001 실내 · O002 실외), 운영 코드(operYnCd: B001
    # 운영 · B003 이용금지), 법정동 코드 10자리(rgnCd). `kind` 에는 설치장소 코드(instlPlaceCd)가
    # 온다
    indoor: str | None = None
    operating: str | None = None
    region_code: str | None = None
    # 어린이놀이시설 안전관리 대상 제외(exfcYn == "Y"). 적재가 이런 시설을 뺀다
    safety_excluded: bool = False
