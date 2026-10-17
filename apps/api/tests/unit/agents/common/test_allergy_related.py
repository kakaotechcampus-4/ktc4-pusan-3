"""식품 아닌 알레르기 이름 → 교육 후보에서 막을 낱말 (reference/allergy_related_terms.yaml).

표는 사람이 정한다. 이 테스트는 표를 읽는 모양과, 이름 · 낱말이 어디까지 걸리는지만 본다.
"""

import pytest

from app.agents.common.allergy import allergy_related_table, related_blocks
from app.rules.term_match import match_fields


def blocks(*labels: str):
    return related_blocks(labels, allergy_related_table())


def test_표는_세_항목이다():
    assert [entry.label for entry in allergy_related_table()] == ["꽃가루", "라텍스", "동물털"]


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("꽃가루", "꽃가루"),
        ("꽃가루 알레르기", "꽃가루"),
        ("땅콩, 꽃가루", "꽃가루"),
        ("화분증", "꽃가루"),
        ("라텍스(고무)", "라텍스"),
        ("고양이 털", "동물털"),
        ("개털 알레르기", "동물털"),
    ],
)
def test_보호자가_적은_이름에서_항목을_찾는다(label, key):
    assert [term.key for term in blocks(label)] == [key]


@pytest.mark.parametrize("label", ["강아지", "고소공포", "우유", "키위"])
def test_표에_없는_이름은_막을_낱말이_없다(label):
    """표에 없는 이름은 이름 대조(registered_names)만 한다."""
    assert blocks(label) == ()


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("꽃가루", "나뭇잎 모으기"),
        ("꽃가루", "공원 산책"),
        ("꽃가루", "풀밭 걷기"),
        ("꽃가루", "씨앗 심기"),
        ("라텍스", "풍선 불기"),
        ("라텍스", "고무줄 놀이"),
        ("동물털", "깃털 날리기"),
        ("동물털", "강아지 흉내"),
    ],
)
def test_막을_낱말이_든_문장(label, text):
    assert match_fields((text,), blocks(label))


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("꽃가루", "풀로 붙이기"),  # 접착제 — "풀" 한 글자는 표에 없다
        ("꽃가루", "꽃게 그림"),
        ("꽃가루", "밖에서 보기"),  # "밖" 은 넣지 않고 "바깥" 만
        ("꽃가루", "헝겊 공 굴리기"),
        ("동물털", "모래를 털어요"),
        ("동물털", "인형극 하기"),
    ],
)
def test_글자만_겹치는_문장은_막지_않는다(label, text):
    assert not match_fields((text,), blocks(label))
