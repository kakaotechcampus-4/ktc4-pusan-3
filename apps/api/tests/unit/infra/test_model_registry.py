"""모델 등록 — app/infra/db/registry.py

🚨 DB 를 쓰는 진입점이 무엇을 import 하든 메타데이터가 온전해야 한다.

앱은 main.py 를 지나지만, 명세 §5-3 · §5-4 의 만료 행 정리 배치는 main.py 없이
session.py 의 세션 팩토리만 가져다 쓴다. 등록이 한쪽에만 걸려 있으면 그 배치에서
ForeignKey("child.id") 가 대상 테이블을 못 찾아 flush 가 NoReferencedTableError 로
죽는다 — 실제로 가입 트랜잭션이 이 이유로 깨졌었다.

같은 프로세스 안에서는 이미 다른 테스트가 모델을 다 import 해버려서 검증이 되지 않는다.
그래서 별도 프로세스를 띄워 "session.py 만 import 한 상태" 를 실제로 만든다.
"""

import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import app.infra.db.session  # noqa: F401  — 이것만 import 한다
from app.infra.db.base import Base

print(len(Base.metadata.tables))
print("child" in Base.metadata.tables)
"""


def test_importing_session_alone_registers_every_model():
    """session.py 하나만 import 해도 모든 도메인 모델이 메타데이터에 올라온다."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(API_ROOT)},
    )

    assert result.returncode == 0, result.stderr
    table_count, has_child = result.stdout.split()

    # child 는 consent.child_id 가 FK 로 가리키는 테이블이다. 이게 빠지면 가입이 죽는다.
    assert has_child == "True", "child 테이블이 등록되지 않았다 — registry import 위치 확인"
    assert int(table_count) >= 20, f"등록된 테이블이 {table_count}개뿐이다"
