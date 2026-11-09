"""run 실패 안내 문구 (#768). 이유(`FailReason`) 하나에 코드 문구 하나.

모델을 거치지 않는다 — 번역기(`api/runs/translate.py`)와 러너가 `failed` 이벤트의 `message` 에
그대로 싣고, 화면은 그 글자를 다시 쓰지 않는다. 문구는 `failure.readout.yaml` 에 있다.
"""

from pathlib import Path

from app.agents.common.errors import FailReason
from app.agents.common.readout import ReadoutCatalog

_FAILURES = ReadoutCatalog.from_yaml(Path(__file__).with_name("failure.readout.yaml"))


def failure_message(reason: FailReason | str) -> str:
    """`reason` 의 화면 문구. 모르는 값이면 `internal_error` 문구 — 문구 없는 failed 를 안 낸다."""
    key = f"failure.{reason}"
    if key not in _FAILURES.texts:
        key = "failure.internal_error"
    return _FAILURES.render(key).body


def catalog_keys() -> frozenset[str]:
    """카탈로그의 이유 목록. 테스트가 `FailReason` 과 같은지 본다."""
    return frozenset(key.removeprefix("failure.") for key in _FAILURES.texts)
