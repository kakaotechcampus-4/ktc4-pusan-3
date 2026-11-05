"""사진 → 모델이 받는 형식. 모델은 부르지 않는다. 그림은 테스트 안에서 만든다."""

import io

import pillow_heif
import pytest
from PIL import Image

from app.providers.notice import document_text
from app.providers.notice.document_text import UnreadableDocument, normalize_image


def _image(fmt: str, size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=fmt)
    return buffer.getvalue()


def _heic(size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", size, "white")).save(buffer, format="HEIF")
    return buffer.getvalue()


def test_갤럭시_고효율_사진_HEIC_를_PNG_로_바꾼다():
    data, mime = normalize_image(_heic())
    assert mime == "image/png"
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (40, 30)


def test_PNG_JPEG_는_그대로_보낸다():
    for fmt in ("PNG", "JPEG"):
        data = _image(fmt)
        assert normalize_image(data) == (data, f"image/{fmt.lower()}")


def test_화소가_너무_많은_그림은_열기_전에_거절한다(monkeypatch):
    # 0.5MB 짜리 PNG 도 펼치면 수백 MB 가 된다. 크기만 보고 펼치기 전에 막는다
    monkeypatch.setattr(document_text, "MAX_IMAGE_PIXELS", 40 * 30 - 1)
    with pytest.raises(UnreadableDocument, match="너무 커요"):
        normalize_image(_image("PNG"))
