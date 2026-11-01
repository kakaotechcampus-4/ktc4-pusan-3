"""서버가 화면으로 내보내는 딥링크 — 웹 경로 이름의 서버 쪽 유일한 출처 (#625).

값은 아이 id 를 담지 않은 상대 경로다. 웹이 지금 보는 아이 경로(`/child/{cid}/`) 뒤에 붙인다.
웹은 이 값을 주소로 그대로 쓰지 않고 어느 구역인지 힌트로만 쓴다(apps/web/CLAUDE.md §3) —
실제로 가는 곳은 웹의 code 별 표가 정한다. 그래서 새 경로가 생기면 여기와 웹의 그 표를 같이 고친다.

agents(입력 run 안내)와 api(05 guard · 동의 오류)가 같이 쓴다. FastAPI 를 import 하지 않는다.
"""

PROFILE = "profile"
"""11 아이 프로필 — 알레르기 · 건강 정보를 보호자가 직접 넣는 구역."""

PROFILE_GROWTH = "profile/growth"
"""11 아이 프로필의 성장 기록 — 키 · 몸무게를 잰 날짜와 함께 넣는 곳."""

SETTINGS = "settings"
"""10 설정 — 동의 관리가 여기 있다."""

CONSENT = SETTINGS
"""403 consent_required 가 보내는 곳. 동의 화면이 따로 없고 설정 안에 있다."""

ALL: tuple[str, ...] = (PROFILE, PROFILE_GROWTH, SETTINGS)
