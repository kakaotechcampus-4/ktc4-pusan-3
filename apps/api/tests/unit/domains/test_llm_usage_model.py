"""llm_usage 테이블 모양 (#710) — DB 없이 모델 메타데이터만 본다.

저장 · 정리 동작은 tests/integration/test_llm_usage_store.py 가 본다.
"""

from typing import get_args

from app.core import llm_usage
from app.domains.usage.models import LlmUsage


def test_source_CHECK_는_core_의_Source_값과_같다():
    check = next(c for c in LlmUsage.__table__.constraints if c.name == "ck_llm_usage_source")
    assert all(f"'{v}'" in str(check.sqltext) for v in get_args(llm_usage.Source))
    assert "meal_ocr" not in str(check.sqltext)


def test_보호자를_지우면_보호자_칸만_빈다():
    [fk] = LlmUsage.__table__.c.parent_id.foreign_keys
    assert fk.ondelete == "SET NULL"
    assert LlmUsage.__table__.c.parent_id.nullable is True
