"""약 이름 대조 — 등록된 코스 이름과 보호자가 말한 약을 코드가 가른다.

부분 일치를 어디까지 받는지가 전부다. 어긋나면 투약이 두 번 남거나 먹인 약 기록이 사라진다.
"""

from app.rules.medicine_name import is_generic_medicine, mentions_medicine, same_medicine


def test_same_medicine():
    assert same_medicine("해열 시럽", "해열시럽") and same_medicine("시럽", "해열 시럽")
    assert not same_medicine("해열제", "해열 시럽") and not same_medicine("", "해열 시럽")
    assert not same_medicine("해열 시럽", "")


def test_mentions_medicine():
    assert mentions_medicine("해열 시럽 먹였어", "해열시럽") and not mentions_medicine(
        "약 먹였어", "감기약"
    )
    assert not mentions_medicine("해열 시럽 먹였어", "")


def test_is_generic_medicine():
    assert (
        is_generic_medicine("그 약")
        and is_generic_medicine("약")
        and is_generic_medicine("먹는 약")
    )
    assert not is_generic_medicine("타이레놀") and not is_generic_medicine("")
