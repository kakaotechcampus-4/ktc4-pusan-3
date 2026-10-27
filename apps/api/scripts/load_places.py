"""장소 적재 (#516) — 공원 · 도서관 표준데이터 → place.

    uv run python -m scripts.load_places --all            # 공원 · 도서관
    uv run python -m scripts.load_places --parks
    uv run python -m scripts.load_places --libraries
    uv run python -m scripts.load_places --all --dry-run  # 받고 세기만 하고 쓰지 않는다

- 받기를 먼저 다 끝내고 쓴다. 외부 API 를 기다리는 동안 DB 트랜잭션을 쥐지 않는다.
- 쓰기는 한 트랜잭션이다. 소스마다 (source, source_id) 로 덮고, 이번 받기에 없는 행은 지운다 —
  문 닫은 곳이 "근처에 있어요" 로 계속 나가지 않게. 하나라도 실패하면 아무것도 쓰지 않고 이전
  적재본이 남는다.
- 🚨 쓰기 전에 막는다. 소스 하나라도 남은 행이 0건이거나, 지금 실린 행의 절반이 안 되면 아무것도
  쓰지 않고 실패한다. 이 API 는 오류를 빈 결과로 주기도 해서, 그대로 실으면 지우기가 표를 비운다.
- 행 거르기 · source_id · 시군구 코드는 아래 순수 함수들이다. 버린 행은 사유별로 센다.
- dry-run 은 읽기만 한다. 지울 행 수도 지금 실린 번호와 대조해서 센다.
- 출력은 건수만이다. 키 · 장소 이름 · 좌표를 내지 않는다.

🚨 이 스크립트를 프로세스로 실행한다(`python -m scripts.load_places --all`). 서버 코드에서
import 하지 않는다 — 손으로 돌리는 배치다(`.github/workflows/load-places.yml`).
"""

import argparse
import asyncio
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity.schemas.common import PlaceCategory
from app.core.place_raw import RawPlaceRow
from app.domains.place import repository

# 이 스크립트가 싣는 소스. 어린이놀이시설(`kids_play_facility`)은 어느 설치장소를 공공으로 볼지
# PM 결정 뒤에 붙인다 (#516)
LoadedSource = Literal["city_park", "library"]

SOURCE_CATEGORY: dict[LoadedSource, PlaceCategory] = {
    "city_park": PlaceCategory.PARK,
    "library": PlaceCategory.LIBRARY,
}
# 행을 받아 온 API 이름. menu_catalog 의 source_version 처럼 "어느 데이터에서 왔나" 를 남긴다
SOURCE_VERSION: dict[LoadedSource, str] = {
    "city_park": "tn_pubr_public_cty_park_info_api",
    "library": "tn_pubr_public_lbrry_api",
}

# 한국 상자. `domains/place/models.py` KOREA_BOX_CHECK 와 같다 — 바꾸면 둘 다(+ 마이그레이션) 고친다
KOREA_LAT = (33.0, 39.0)
KOREA_LNG = (124.0, 132.0)

# 보호자가 아이와 그냥 걸어 들어갈 수 있는 도서관 종류(lbrrySe). 2026-10-27 실측 3,597행 —
# 작은도서관 2,089 · 공공도서관 1,440 · 어린이도서관 35 · 학교도서관 26 · 전문도서관 4 ·
# 장애인도서관 2 · 대학도서관 1. 뒤의 넷은 이용 대상이 정해진 곳이라 뺀다(33행)
LIBRARY_KINDS = frozenset({"공공도서관", "작은도서관", "어린이도서관"})

# 공원 종류(parkSe) 중 아이와 놀러 갈 곳이 아닌 것. 2026-10-27 실측 묘지공원 39행
PARK_EXCLUDED_KINDS = frozenset({"묘지공원"})

# 관리번호가 겹친 공원을 같은 곳으로 보는 기준 (2026-10-27 실측, 아래 `clean`).
# - 이름이 같거나 한쪽이 다른 쪽을 품고("수랑" · "수랑어린이공원") 300m 안
# - 지번(동 · 번지)이 같고 1km 안 — 광주처럼 시도 이름 · 공원 이름이 함께 바뀐 경우
# - 이름은 달라도 50m 안 — 이름만 바뀐 경우
SAME_NAME_M = 300
SAME_ADDRESS_M = 1000
SAME_SPOT_M = 50
# 공원 이름 끝의 종류 말("어린이공원" · "근린공원" · "제2공원")은 떼고 비교한다
_PARK_SUFFIX = re.compile(r"(제?\d*)?(어린이|근린|소|체육|수변|문화|역사|도시농업)?공원$")
_LOT_TAIL = re.compile(r"(동|읍|면|리|가)$")

# 도서관 source_id 의 좌표 자리수. 넷째 자리는 약 11m 다 — 같은 도서관을 다시 받아도 같은 값이 되고,
# 이름이 같은 다른 도서관은 갈린다(실측 이름 209개가 두 곳 이상에 쓰인다)
SOURCE_ID_DECIMALS = 4

# 관리번호 "시군구코드5자리-일련번호" 의 앞 5자리
_SIGUNGU = re.compile(r"([0-9]{5})-")


class Drop(StrEnum):
    """버린 사유. 값이 그대로 출력에 찍힌다."""

    NO_NAME = "이름 없음"
    NO_ID = "관리번호 없음"
    NO_COORDS = "좌표 없음"
    OUTSIDE_KOREA = "한국 밖"
    KIND_EXCLUDED = "제외 종류"
    DUPLICATE = "중복"


@dataclass(frozen=True)
class PlaceRecord:
    source: LoadedSource
    source_id: str
    name: str
    category: PlaceCategory
    lat: float
    lng: float
    sigungu_code: str | None
    data_date: date | None
    # 관리번호가 겹친 공원을 가릴 때만 쓴다. 싣지 않는다 — `place_values` 에 없고 표에 주소 칸이
    # 없다
    address: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Cleaned:
    records: tuple[PlaceRecord, ...]
    received: int
    dropped: Mapping[Drop, int]


@dataclass(frozen=True)
class Loaded:
    source: LoadedSource
    received: int  # API 에서 받은 행
    kept: int  # 거르고 남아 실은(dry-run 은 실을) 행
    dropped: Mapping[Drop, int]
    stored: int  # 적재 전 그 소스로 실려 있던 행
    deleted: int  # 이번 받기에 없어 지운 행. dry-run 은 지울 행


def parse_coord(text: str | None) -> float | None:
    """좌표 글자 → 숫자. 비었거나 못 읽으면 None 이다 — 0 으로 채우지 않는다."""
    if text is None:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def in_korea(lat: float, lng: float) -> bool:
    return KOREA_LAT[0] <= lat <= KOREA_LAT[1] and KOREA_LNG[0] <= lng <= KOREA_LNG[1]


def parse_data_date(text: str | None) -> date | None:
    """데이터기준일자("2026-06-30"). 못 읽으면 None — 날짜 하나 때문에 장소를 버리지 않는다."""
    if text is None:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def sigungu_code(manage_no: str | None) -> str | None:
    """공원 관리번호 앞 5자리(시군구 코드). 모양이 "숫자5자리-" 가 아니면 None 이다."""
    if manage_no is None:
        return None
    found = _SIGUNGU.match(manage_no.strip())
    return found.group(1) if found else None


def library_source_id(name: str, lat: float, lng: float) -> str:
    """도서관 표준데이터에는 번호 칸이 없어 이름과 좌표로 만든다.

    좌표가 고쳐지면(넷째 자리 넘게) 다른 번호가 된다 — 옛 번호는 "이번 받기에 없는 행" 으로 지워지고
    새 번호로 다시 실린다.
    """
    return f"{name}|{lat:.{SOURCE_ID_DECIMALS}f}|{lng:.{SOURCE_ID_DECIMALS}f}"


def _record(source: LoadedSource, raw: RawPlaceRow) -> PlaceRecord | Drop:
    name = (raw.name or "").strip()
    if not name:
        return Drop.NO_NAME
    kind = (raw.kind or "").strip()
    if source == "library" and kind not in LIBRARY_KINDS:
        return Drop.KIND_EXCLUDED
    if source == "city_park" and kind in PARK_EXCLUDED_KINDS:
        return Drop.KIND_EXCLUDED
    lat, lng = parse_coord(raw.latitude), parse_coord(raw.longitude)
    if lat is None or lng is None:
        return Drop.NO_COORDS
    if not in_korea(lat, lng):
        return Drop.OUTSIDE_KOREA
    if source == "city_park":
        source_id = (raw.manage_no or "").strip()
        if not source_id:
            return Drop.NO_ID
        sigungu = sigungu_code(source_id)
    else:
        # 도서관은 시군구 이름만 오고 코드가 없다. 이름 → 코드 대응은 후속이다
        source_id, sigungu = library_source_id(name, lat, lng), None
    return PlaceRecord(
        source=source,
        source_id=source_id,
        name=name,
        category=SOURCE_CATEGORY[source],
        lat=lat,
        lng=lng,
        sigungu_code=sigungu,
        data_date=parse_data_date(raw.reference_date),
        address=raw.address,
    )


def _newer(record: PlaceRecord, kept: PlaceRecord) -> bool:
    """데이터기준일자가 더 새로우면 바꾼다. 같거나 모르면 먼저 온 행을 둔다."""
    if record.data_date is None:
        return False
    return kept.data_date is None or record.data_date > kept.data_date


def clean(source: LoadedSource, rows: Sequence[RawPlaceRow]) -> Cleaned:
    """받은 행을 거르고 같은 source_id 는 데이터기준일자가 가장 새로운 행 하나만 남긴다.

    공원 관리번호는 표준데이터 안에서 겹친다(2026-10-27 실측 880개 · 2,281행). 대부분 같은 공원을
    다른 기관이 다른 날짜로 다시 올린 것이지만, 기관이 번호를 새로 매기면서 같은 번호가 다른
    공원을 가리키기도 한다(광주 · 대구 · 인천). 그래서 밀려난 행을 남은 공원 전체와 다시 대조해,
    같은 곳으로 볼 수 없으면(`same_park`) 따로 싣는다 — 실측 밀려난 1,392행 중 168행.
    """
    dropped: Counter[Drop] = Counter()
    kept: dict[str, PlaceRecord] = {}
    pushed_out: list[PlaceRecord] = []
    for raw in rows:
        record = _record(source, raw)
        if isinstance(record, Drop):
            dropped[record] += 1
            continue
        old = kept.get(record.source_id)
        if old is None:
            kept[record.source_id] = record
        elif _newer(record, old):
            kept[record.source_id] = record
            pushed_out.append(old)
        else:
            pushed_out.append(record)
    if source == "city_park":
        nearby = _Grid(kept.values())
        for record in sorted(pushed_out, key=lambda r: r.data_date or date.min, reverse=True):
            if any(same_park(record, other) for other in nearby.around(record)):
                dropped[Drop.DUPLICATE] += 1
                continue
            # 번호가 같은 다른 공원 — 이름을 붙여 번호를 가른다. 이름까지 같으면 중복이다
            distinct = replace(record, source_id=f"{record.source_id}|{record.name}")
            if distinct.source_id in kept:
                dropped[Drop.DUPLICATE] += 1
                continue
            kept[distinct.source_id] = distinct
            nearby.add(distinct)
    elif pushed_out:
        dropped[Drop.DUPLICATE] += len(pushed_out)
    return Cleaned(records=tuple(kept.values()), received=len(rows), dropped=dict(dropped))


def same_park(a: PlaceRecord, b: PlaceRecord) -> bool:
    """관리번호가 겹친 두 공원 행이 같은 곳인가. 기준은 `SAME_*` 상수."""
    meters = distance_m(a.lat, a.lng, b.lat, b.lng)
    name_a, name_b = _park_core(a.name), _park_core(b.name)
    if meters <= SAME_NAME_M and (name_a in name_b or name_b in name_a):
        return True
    lot_a, lot_b = _lot(a.address), _lot(b.address)
    if meters <= SAME_ADDRESS_M and lot_a is not None and lot_a == lot_b:
        return True
    return meters <= SAME_SPOT_M


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 점 사이 거리(m) — haversine. 어댑터 SQL(`nearby_places.sql`)과 같은 식이다."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * repository.EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _park_core(name: str) -> str:
    """공백 · 문장부호를 빼고 끝의 종류 말을 뗀 이름. 다 떼어 비면 원래 이름을 쓴다."""
    compact = re.sub(r"[\s()\[\]·\-_.]", "", name)
    return _PARK_SUFFIX.sub("", compact) or compact


def _lot(address: str | None) -> str | None:
    """주소의 동(읍 · 면 · 리) 낱말과 번지. 시도 · 시군구 이름이 바뀌어도 같게 나온다."""
    words = (address or "").replace(",", " ").split()
    for index, word in enumerate(words[1:], start=1):
        if _LOT_TAIL.search(word) and index + 1 < len(words):
            return f"{word} {words[index + 1]}"
    return None


class _Grid:
    """가까운 공원만 대조하려고 위경도 0.02도(약 2km) 칸으로 나눈다 — `SAME_ADDRESS_M` 보다 넓다."""

    def __init__(self, records: Iterable[PlaceRecord]) -> None:
        self._cells: dict[tuple[int, int], list[PlaceRecord]] = {}
        for record in records:
            self.add(record)

    def add(self, record: PlaceRecord) -> None:
        self._cells.setdefault(_cell(record), []).append(record)

    def around(self, record: PlaceRecord) -> Iterator[PlaceRecord]:
        row, col = _cell(record)
        for d_row in (-1, 0, 1):
            for d_col in (-1, 0, 1):
                yield from self._cells.get((row + d_row, col + d_col), ())


def _cell(record: PlaceRecord) -> tuple[int, int]:
    return round(record.lat * 50), round(record.lng * 50)


def place_values(record: PlaceRecord, *, now: datetime) -> dict[str, Any]:
    return {
        "source": record.source,
        "source_id": record.source_id,
        "name": record.name,
        "category": record.category.value,
        "lat": record.lat,
        "lng": record.lng,
        "sigungu_code": record.sigungu_code,
        "data_date": record.data_date,
        "source_version": SOURCE_VERSION[record.source],
        "synced_at": now,
    }


async def run(
    *,
    parks: bool,
    libraries: bool,
    fetch_parks: Callable[[], Awaitable[Sequence[RawPlaceRow]]],
    fetch_libraries: Callable[[], Awaitable[Sequence[RawPlaceRow]]],
    open_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    now: datetime,
    dry_run: bool = False,
) -> list[Loaded]:
    fetched: list[tuple[LoadedSource, Sequence[RawPlaceRow]]] = []
    if parks:
        fetched.append(("city_park", await fetch_parks()))
    if libraries:
        fetched.append(("library", await fetch_libraries()))
    cleaned = [(source, clean(source, rows)) for source, rows in fetched]
    # 쓰기 전에 막는다. 건수만 말한다
    for source, result in cleaned:
        if not result.records:
            raise RuntimeError(
                f"{source} 에서 남은 행이 0건이다(받은 행 {result.received}). 적재하지 않는다"
            )

    async with open_session() as session:
        try:
            stored = {
                source: await repository.stored_ids(session, source=source) for source, _ in cleaned
            }
            # 소스 하나라도 걸리면 다른 소스도 쓰지 않는다 — 다 확인한 뒤에 쓴다
            for source, result in cleaned:
                if len(result.records) * 2 < len(stored[source]):
                    raise RuntimeError(
                        f"{source} 에서 남은 행 {len(result.records)}건이 지금 실린 "
                        f"{len(stored[source])}건의 절반이 안 된다. 적재하지 않는다"
                    )
            loaded = []
            for source, result in cleaned:
                keep = {record.source_id for record in result.records}
                if dry_run:
                    deleted = len(stored[source] - keep)
                else:
                    await repository.upsert_places(
                        session, rows=[place_values(record, now=now) for record in result.records]
                    )
                    deleted = await repository.delete_missing(session, source=source, keep_ids=keep)
                loaded.append(
                    Loaded(
                        source=source,
                        received=result.received,
                        kept=len(result.records),
                        dropped=result.dropped,
                        stored=len(stored[source]),
                        deleted=deleted,
                    )
                )
        except BaseException:
            await session.rollback()
            raise
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    return loaded


async def _fetch_parks() -> Sequence[RawPlaceRow]:
    from app.core.config import settings
    from app.integrations.place.client import PlaceStdClient, place_batch_http

    async with place_batch_http() as http:
        client = PlaceStdClient(service_key=settings.DATA_GO_KR_SERVICE_KEY, http=http)
        return await client.parks()


async def _fetch_libraries() -> Sequence[RawPlaceRow]:
    from app.core.config import settings
    from app.integrations.place.client import PlaceStdClient, place_batch_http

    async with place_batch_http() as http:
        client = PlaceStdClient(service_key=settings.DATA_GO_KR_SERVICE_KEY, http=http)
        return await client.libraries()


def build_parser() -> argparse.ArgumentParser:
    # 모듈 docstring 을 그대로 쓰지 않는다 — `—` · 이모지는 Windows 콘솔(cp949)에서 못 찍어
    # `--help` 가 UnicodeEncodeError 로 죽는다
    parser = argparse.ArgumentParser(
        description="장소 적재 (#516). 공원 · 도서관 표준데이터를 place 로 싣는다. 받기를 다 끝낸 "
        "뒤 한 트랜잭션으로 덮고, 이번 받기에 없는 행은 지운다.",
        epilog=(
            "예) uv run python -m scripts.load_places --all --dry-run\n"
            "    (--parks 나 --libraries 만 따로 돌릴 수도 있다)"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--parks", action="store_true", help="전국도시공원정보표준데이터")
    parser.add_argument("--libraries", action="store_true", help="전국도서관표준데이터")
    parser.add_argument("--all", action="store_true", help="둘 다")
    parser.add_argument("--dry-run", action="store_true", help="받고 세기만 하고 쓰지 않는다")
    return parser


def summary(loaded: Sequence[Loaded], *, dry_run: bool) -> str:
    # `—` 를 쓰지 않는다 — Windows 콘솔(cp949)에서 못 찍는다. 이름 · 좌표도 찍지 않는다
    deleted = "지울 행" if dry_run else "지운 행"
    lines = []
    for item in loaded:
        drops = " · ".join(
            f"{reason} {item.dropped[reason]}" for reason in Drop if item.dropped.get(reason)
        )
        lines.append(
            f"{item.source}: 받음 {item.received} · 남김 {item.kept} · "
            f"버림 {item.received - item.kept}({drops or '없음'}) · "
            f"적재 전 {item.stored} · {deleted} {item.deleted}"
        )
    if dry_run:
        lines.append("(dry-run, 쓰지 않음)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.parks or args.libraries or args.all):
        parser.error("--parks · --libraries · --all 중 하나를 고른다")

    from app.infra.db.session import async_session_factory

    loaded = asyncio.run(
        run(
            parks=args.parks or args.all,
            libraries=args.libraries or args.all,
            fetch_parks=_fetch_parks,
            fetch_libraries=_fetch_libraries,
            open_session=async_session_factory,
            now=datetime.now(UTC),
            dry_run=args.dry_run,
        )
    )
    print(summary(loaded, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
