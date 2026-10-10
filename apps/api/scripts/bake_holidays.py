"""특일정보 API 로 관공서 공휴일을 받아 `app/rules/holidays_data.py` 로 굽는다.

    cd apps/api && uv run python -m scripts.bake_holidays 2026 2027

1년에 한 번, 다음 해 월력요항이 나온 뒤(매년 6월쯤) 돌린다. 서비스는 런타임에 API 를 부르지 않고
구운 파일만 읽는다 — API 가 죽어도 날짜 계산이 죽지 않게 (설계 D8).

- 인증키 `DATA_GO_KR_SERVICE_KEY` 는 환경변수나 `apps/api/.env` 에서 읽는다 — 날씨 조회와 같은
  공공데이터포털 키다. "일반 인증키(Decoding)" 를 넣는다 — 이 스크립트가 주소에 넣을 때 인코딩한다.
  서버 설정 전체(`Settings`)를 읽지 않고 이 키 한 줄만 읽는다 — 굽는 데 다른 설정이 필요 없다.
- 넘긴 해만 새로 받고, 이미 구워 둔 다른 해는 그대로 둔다.
- 결과에서 `isHoliday=Y` 인 날만 남긴다. 기념일 · 24절기처럼 쉬지 않는 날은 다른 API 다.
- 바뀐 날(새로 생기거나 빠진 날)을 출력한다. 굽고 나면 `git diff` 로 확인하고 커밋한다.

🚨 인증키를 출력하거나 예외 메시지에 넣지 않는다. 저장소가 public 이다 (루트 CLAUDE.md §9).
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.rules.holidays_data import HOLIDAYS

ENDPOINT = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
OUTPUT = Path(__file__).resolve().parents[1] / "app" / "rules" / "holidays_data.py"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
KEY_NAME = "DATA_GO_KR_SERVICE_KEY"
TIMEOUT_SECONDS = 10.0

_HEADER = '''"""관공서 공휴일 날짜. `scripts/bake_holidays.py` 가 만든다 — 직접 고치지 않는다.

출처: 한국천문연구원 특일정보 `SpcdeInfoService/getRestDeInfo` 의 `isHoliday=Y` 행.
다음 해 것은 그해 월력요항이 나온 뒤(매년 6월쯤) 스크립트를 한 번 돌려 더한다.
"""

from datetime import date

'''


class BakeError(Exception):
    """API 가 정상 결과를 주지 않았다. 메시지에 인증키를 넣지 않는다."""


def parse_rest_days(payload: dict[str, Any], year: int) -> dict[date, str]:
    """`getRestDeInfo` JSON 응답 → {날짜: 이름}. 쉬는 날(`isHoliday=Y`)만.

    결과가 한 건이면 `item` 이 목록이 아니라 객체로, 0건이면 `items` 가 빈 문자열로 온다.
    """
    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") != "00":
        raise BakeError(f"{year}: resultCode={header.get('resultCode')} {header.get('resultMsg')}")
    items = payload["response"].get("body", {}).get("items") or {}
    rows = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(rows, dict):
        rows = [rows]

    days: dict[date, str] = {}
    for row in rows:
        if row.get("isHoliday") != "Y":
            continue
        raw = str(row["locdate"])
        day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        if day.year != year:
            raise BakeError(f"{year}: 다른 해의 날짜가 섞여 왔다 ({day.isoformat()})")
        days[day] = days[day] + " · " + row["dateName"] if day in days else row["dateName"]
    if not days:
        raise BakeError(f"{year}: 공휴일이 0건이다 — 아직 발표 전인 해일 수 있다")
    return days


def render(holidays: dict[int, dict[date, str]]) -> str:
    """구운 파일 본문. 같은 입력이면 늘 같은 글이다 (해 · 날짜 순)."""
    lines = [_HEADER + "HOLIDAYS: dict[int, dict[date, str]] = {"]
    for year in sorted(holidays):
        lines.append(f"    {year}: {{")
        for day in sorted(holidays[year]):
            name = holidays[year][day].replace('"', "'")
            lines.append(f'        date({day.year}, {day.month}, {day.day}): "{name}",')
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def changes(before: dict[date, str], after: dict[date, str]) -> list[str]:
    """새로 생기거나 빠진 날. 이름만 바뀐 날은 세지 않는다."""
    added = [f"+ {day.isoformat()} {after[day]}" for day in sorted(after.keys() - before.keys())]
    removed = [f"- {day.isoformat()} {before[day]}" for day in sorted(before.keys() - after.keys())]
    return added + removed


def read_key(env_file: Path = ENV_FILE) -> str:
    """환경변수가 먼저, 없으면 `.env` 의 `KEY=값` 줄. 값은 돌려주기만 하고 찍지 않는다."""
    if value := os.environ.get(KEY_NAME, "").strip():
        return value
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_NAME:
            return value.strip().strip("'\"")
    return ""


def fetch(year: int, key: str, client: httpx.Client) -> dict[date, str]:
    params: dict[str, str | int] = {
        "serviceKey": key,
        "solYear": year,
        "numOfRows": 100,
        "_type": "json",
    }
    try:
        response = client.get(ENDPOINT, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        # 오류 문자열에 요청 주소(인증키 포함)가 들어갈 수 있어 종류만 남긴다
        raise BakeError(f"{year}: 호출 실패 ({type(error).__name__})") from None
    return parse_rest_days(payload, year)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("years", nargs="+", type=int, help="받을 해. 예: 2026 2027")
    args = parser.parse_args()

    key = read_key()
    if not key:
        print(f"{KEY_NAME} 가 없다 — apps/api/.env 에 넣는다.", file=sys.stderr)
        return 1

    baked = {year: dict(days) for year, days in HOLIDAYS.items()}
    try:
        with httpx.Client() as client:
            for year in args.years:
                fresh = fetch(year, key, client)
                diff = changes(baked.get(year, {}), fresh)
                print(f"{year}: {len(fresh)}일" + ("" if diff else " (바뀐 날 없음)"))
                for line in diff:
                    print(f"  {line}")
                baked[year] = fresh
    except BakeError as error:
        print(f"멈춤 — {error}", file=sys.stderr)
        return 1

    OUTPUT.write_text(render(baked), encoding="utf-8", newline="\n")
    print(
        f"썼다: {OUTPUT.relative_to(Path.cwd()) if OUTPUT.is_relative_to(Path.cwd()) else OUTPUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
