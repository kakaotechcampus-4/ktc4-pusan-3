"""*_doc 시드 공통 칸 (RAG_plan §1) — 모양이 깨지면 읽을 때 바로 실패한다.

문서 행은 프롬프트 [예시] 로 들어가고 출처를 행 단위로 추적해야 해서, 출처 · 월령 · 검수 칸이
조용히 비면 안 된다.
"""

import copy
from datetime import date

import pytest

from app.agents.common.reference import check_unique_doc_keys, parse_doc_meta

ROW = {
    "doc_key": "activity.play.m18_23.cushion_crawl",
    "row_type": "play_idea",
    "title": "쿠션 길 기어 넘기",
    "body": "쿠션을 늘어놓고\n  기어서 넘어오게 한다.",
    "min_month": 18,
    "max_month": 24,
    "tags": ["gross_motor"],
    "source_title": "제4차 어린이집 표준보육과정",
    "source_org": "보건복지부",
    "source_year": 2020,
    "source_locator": "0~1세 신체운동 · 신체활동 즐기기",
    "license_basis": "public_law",
    "status": "draft",
    "authored_by": "03leedo",
    "version": 1,
    "setting": "indoor",  # 도메인 칸 — 공통 검사는 domain_keys 로 통과시킨다
}


def parse(row):
    return parse_doc_meta(
        row,
        file="t.yaml",
        key_prefix="activity.",
        row_types=frozenset({"play_idea"}),
        domain_keys=frozenset({"setting"}),
    )


def broken(**change):
    row = copy.deepcopy(ROW)
    for key, value in change.items():
        if value is ...:
            row.pop(key)
        else:
            row[key] = value
    return row


class TestValid:
    def test_월령은_이상_미만이다(self):
        meta = parse(ROW)
        assert meta.covers(18) and meta.covers(23)
        assert not meta.covers(24)

    def test_본문_줄바꿈을_한_칸으로_모은다(self):
        assert parse(ROW).body == "쿠션을 늘어놓고 기어서 넘어오게 한다."

    def test_search_text_는_title_과_tags_로_만든다(self):
        assert parse(ROW).search_text == "쿠션 길 기어 넘기 gross_motor"

    def test_approved_는_검수자와_검수일이_있으면_통과(self):
        meta = parse(
            broken(status="approved", reviewed_by="nnhhlee", reviewed_at=date(2026, 10, 7))
        )
        assert meta.status == "approved"


class TestBroken:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"extra": 1}, "칸"),
            ({"source_locator": ...}, "source_locator"),
            ({"source_org": "  "}, "source_org"),
            ({"doc_key": "food.x"}, "시작"),
            ({"row_type": "outing"}, "row_type"),
            ({"max_month": None}, "비울 수 없다"),
            ({"max_month": 18}, "min"),
            ({"max_month": 73}, "min"),
            ({"min_month": "18"}, "정수"),
            ({"source_year": "2020"}, "source_year"),
            ({"license_basis": "kogl_5"}, "license_basis"),
            ({"status": "done"}, "status"),
            ({"reviewed_by": "03leedo"}, "같다"),
            ({"status": "approved", "reviewed_by": "nnhhlee"}, "검수일"),
            ({"tags": "gross_motor"}, "tags"),
            ({"version": 0}, "version"),
        ],
    )
    def test_칸이_깨지면_실패한다(self, change, message):
        with pytest.raises(ValueError, match=message):
            parse(broken(**change))

    def test_doc_key_가_겹치면_실패한다(self):
        meta = parse(ROW)
        with pytest.raises(ValueError, match="겹친다"):
            check_unique_doc_keys([meta, meta], file="t.yaml")
