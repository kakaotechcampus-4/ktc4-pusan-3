"""사진 → 모델이 받는 형식. 모델은 부르지 않는다. 그림은 테스트 안에서 만든다."""

import io

import pillow_heif
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

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


GPS_IFD = 0x8825
ORIENTATION = 0x0112


def _phone_jpeg(*, orientation: int = 1, size: tuple[int, int] = (40, 30)) -> bytes:
    """폰이 찍은 것처럼 기기 이름 · 촬영 장소(GPS) · 방향이 든 JPEG."""
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[ORIENTATION] = orientation
    exif.get_ifd(GPS_IFD)[2] = (35.0, 10.0, 0.0)
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_PNG_는_PNG_로_JPEG_는_JPEG_로_보낸다():
    for fmt in ("PNG", "JPEG"):
        data, mime = normalize_image(_image(fmt))
        assert mime == f"image/{fmt.lower()}"
        with Image.open(io.BytesIO(data)) as image:
            assert (image.format, image.size) == (fmt, (40, 30))


def test_촬영_장소_같은_부가_정보는_모델로_보내기_전에_지운다():
    # 처리방침 1장 ⑧ 4호 — "사진 파일에 담긴 촬영 장소 같은 부가 정보는 전송하기 전에 지웁니다"
    data = _phone_jpeg()
    with Image.open(io.BytesIO(data)) as original:
        assert original.getexif().get_ifd(GPS_IFD)  # 시험 사진에 정말 들어 있다

    out, _ = normalize_image(data)

    with Image.open(io.BytesIO(out)) as image:
        assert len(image.getexif()) == 0
        assert "exif" not in image.info


def test_세로로_찍은_사진은_방향대로_돌려서_보낸다():
    # 폰은 가로로 저장하고 "6 = 오른쪽으로 돌려 보라" 만 적는다. 지우기 전에 돌리지 않으면 눕는다
    out, _ = normalize_image(_phone_jpeg(orientation=6, size=(40, 30)))
    with Image.open(io.BytesIO(out)) as image:
        assert image.size == (30, 40)


def test_PNG_에_적힌_글자_정보도_지운다():
    info = PngInfo()
    info.add_text("Location", "어느 동네")
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="PNG", pnginfo=info)

    out, _ = normalize_image(buffer.getvalue())

    with Image.open(io.BytesIO(out)) as image:
        assert "Location" not in image.info


def test_투명한_바탕은_흰색으로_채운다():
    # 검은 글자를 투명 바탕에 둔 공지 이미지가 검은 바탕이 되면 글자가 사라진다
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 30), (0, 0, 0, 0)).save(buffer, format="PNG")

    out, _ = normalize_image(buffer.getvalue())

    with Image.open(io.BytesIO(out)) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_화소가_너무_많은_그림은_열기_전에_거절한다(monkeypatch):
    # 0.5MB 짜리 PNG 도 펼치면 수백 MB 가 된다. 크기만 보고 펼치기 전에 막는다
    monkeypatch.setattr(document_text, "MAX_IMAGE_PIXELS", 40 * 30 - 1)
    with pytest.raises(UnreadableDocument, match="너무 커요"):
        normalize_image(_image("PNG"))


def test_JPEG_주석_칸도_모델로_보내지_않는다():
    # EXIF · XMP 는 빠지는데 주석(COM)은 Pillow 가 다시 저장할 때 들고 갔다 (#726 리뷰)
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="JPEG", comment=b"37.5665N 126.9780E")

    out, _ = normalize_image(buffer.getvalue())

    assert b"37.5665N" not in out
    with Image.open(io.BytesIO(out)) as image:
        assert "comment" not in image.info


def test_아주_가로로_긴_그림도_서버_오류로_끝나지_않는다():
    # 폭을 2,000 으로 줄이면 높이가 0 이 되어 resize 가 ValueError 를 냈다 (#726 리뷰)
    buffer = io.BytesIO()
    Image.new("RGB", (40_000, 1), "white").save(buffer, format="PNG")

    out, _ = normalize_image(buffer.getvalue())

    with Image.open(io.BytesIO(out)) as image:
        assert image.size == (2_000, 1)
