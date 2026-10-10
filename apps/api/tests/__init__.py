"""테스트 패키지. pytest 가 conftest 보다 먼저 불러온다 — 그래서 여기 둔다.

🚨 테스트는 Discord 로 알림을 보내지 않는다. 설정(app.core.config)은 .env 보다 환경변수를
   먼저 보므로, 내 .env 에 웹훅 주소를 넣어 둔 채 make test 를 돌려도 500 을 내는 테스트가
   팀 채널을 울리지 않는다. conftest 가 app.main 을 불러오는 순간 핸들러가 달리기 때문에,
   그보다 먼저 비워야 한다.
   (app/core/alerts.py · tests/unit/core/test_alerts.py)
"""

import os

os.environ["ALERT_WEBHOOK_URL"] = ""
