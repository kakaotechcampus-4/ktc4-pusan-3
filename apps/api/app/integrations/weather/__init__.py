"""날씨 어댑터 — 공공데이터포털(기상청 · 에어코리아) 호출 (Activity 설계 D8 · D9).

이 패키지는 호출 · 타임아웃 · 지금 시각 칸 고르기까지만 한다. 값은 원문 문자열 그대로
`app/core/weather_raw.py` 의 모양으로 넘기고, 해석은 Activity `weather.py` 가 한다.

🚨 좌표를 받지 않는다. 기상청 격자(`WeatherGrid`)만 받는다 — 보호자 위치가 이 패키지에 아예
   들어오지 않아 밖으로 나갈 수 없다 (4-3).
🚨 인증키는 요청 주소(`serviceKey=`)에 실린다. 오류 메시지 · 로그에 주소를 남기지 않는다.

단기예보(`forecast.py`) · 대기질(`air.py`) · 자외선(`uv.py`) · 특보(`advisories.py`)를
`source.PublicWeather` 가 묶어 Activity `WeatherSource` 포트가 된다. 격자 → 시군구 · 특보구역은
구운 표(`areas_data.py`), 격자 → 대기질 측정소도 구운 표(`stations_data.py`)다.
"""
