"""공지 파일 → 글자. 모델을 부르지 않는다.

기관 문서는 대개 HWP 로 만들어 PDF · JPG 로 바뀌어 나온다. 글자가 들어 있는 파일은 여기서 읽고,
글자가 없는 것(사진 · 스캔 PDF)만 모델에게 넘긴다 — 공지 한 장에 쓰는 비용이 달라진다.

🚨 업로드 파일은 신뢰할 수 없다. 압축을 풀기 전에 크기를 본다.
"""

import io
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from xml.etree import ElementTree

import olefile
import pypdfium2
from openpyxl import load_workbook
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from pypdf import PdfReader

from app.rules.notice_text import clean_text

# 갤럭시 "고효율 사진" 설정은 HEIC 로 저장한다. Pillow 가 그 형식을 열 수 있게 한다
register_heif_opener()

MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
# 쪽당 이보다 글자가 적으면 스캔본으로 본다
MIN_CHARS_PER_PAGE = 40
MAX_IMAGE_WIDTH = 2_000
# 펼친 그림의 화소 수 상한. 0.5MB PNG 도 13000×13000 이면 펼칠 때 1GB 넘게 쓴다.
# 휴대폰 사진은 대개 1,200만 화소이고, 5,000만 화소 모드까지 받는다
MAX_IMAGE_PIXELS = 60_000_000

_HWP_PARA_TEXT = 67
# 한 글자(2바이트)로 끝나는 제어 문자. 나머지 0~31 은 8글자 자리를 차지한다
_HWP_ONE_UNIT = frozenset({0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31})
_HWPX_T = "{http://www.hancom.co.kr/hwpml/2011/paragraph}t"
_HWPX_P = "{http://www.hancom.co.kr/hwpml/2011/paragraph}p"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_BLANK_LINES = re.compile(r"\n{3,}")


class UnreadableDocument(ValueError):
    """글자를 꺼낼 수 없는 파일. `reason` 은 사람이 읽는 이유다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DocumentText:
    text: str
    pages: int = 1
    needs_vision: bool = False  # 글자가 없어 모델이 봐야 한다


def _hwp_para_text(payload: bytes) -> str:
    out: list[str] = []
    i = 0
    while i + 1 < len(payload):
        code = struct.unpack_from("<H", payload, i)[0]
        if code >= 32:
            out.append(chr(code))
            i += 2
        elif code in (10, 13):
            out.append("\n")
            i += 2
        elif code == 9:
            out.append("\t")
            i += 16
        elif code in _HWP_ONE_UNIT:
            i += 2
        else:
            i += 16
    return "".join(out)


def _hwp_section_text(data: bytes) -> str:
    out: list[str] = []
    i = 0
    while i + 4 <= len(data):
        header = struct.unpack_from("<I", data, i)[0]
        i += 4
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if i + 4 > len(data):
                break
            size = struct.unpack_from("<I", data, i)[0]
            i += 4
        if tag == _HWP_PARA_TEXT:
            out.append(_hwp_para_text(data[i : i + size]))
        i += size
    return "".join(out)


def read_hwp(data: bytes) -> DocumentText:
    if not olefile.isOleFile(io.BytesIO(data)):
        raise UnreadableDocument("HWP 파일이 아니에요")
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        if not ole.exists("FileHeader"):
            raise UnreadableDocument("HWP 파일이 아니에요")
        flags = struct.unpack_from("<I", ole.openstream("FileHeader").read(), 36)[0]
        compressed = bool(flags & 0x01)
        if flags & 0x02:
            raise UnreadableDocument("암호가 걸린 문서예요")
        if flags & 0x04:
            raise UnreadableDocument("배포용 문서라 글자를 꺼낼 수 없어요")
        sections = sorted(
            (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText"),
            key=lambda e: int(e[1].removeprefix("Section") or 0),
        )
        if not sections:
            raise UnreadableDocument("본문이 없는 문서예요")
        parts: list[str] = []
        for entry in sections:
            raw = ole.openstream(entry).read()
            if compressed:
                decompressor = zlib.decompressobj(-15)
                raw = decompressor.decompress(raw, MAX_UNZIPPED_BYTES)
                if decompressor.unconsumed_tail:
                    raise UnreadableDocument("문서가 너무 커요")
            parts.append(_hwp_section_text(raw))
    return DocumentText(clean_text("\n".join(parts)), pages=len(sections))


def read_hwpx(data: bytes) -> DocumentText:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UnreadableDocument("HWPX 파일이 아니에요") from e
    with archive:
        if sum(i.file_size for i in archive.infolist()) > MAX_UNZIPPED_BYTES:
            raise UnreadableDocument("문서가 너무 커요")
        names = sorted(n for n in archive.namelist() if re.search(r"section\d+\.xml$", n, re.I))
        if not names:
            raise UnreadableDocument("본문이 없는 문서예요")
        paragraphs: list[str] = []
        for name in names:
            root = ElementTree.fromstring(archive.read(name))
            for para in root.iter(_HWPX_P):
                own = "".join(t.text or "" for t in para.iter(_HWPX_T))
                if own.strip():
                    paragraphs.append(own)
    return DocumentText(clean_text("\n".join(paragraphs)), pages=len(names))


def read_pdf(data: bytes) -> DocumentText:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnreadableDocument("암호가 걸린 문서예요")
        pages = [page.extract_text() or "" for page in reader.pages]
    except UnreadableDocument:
        raise
    except Exception as e:  # pypdf 는 깨진 파일마다 다른 예외를 던진다
        raise UnreadableDocument("PDF 를 열 수 없어요") from e
    text = clean_text("\n".join(pages))
    scanned = len(text) < MIN_CHARS_PER_PAGE * max(len(pages), 1)
    return DocumentText(text, pages=len(pages), needs_vision=scanned)


def read_xlsx(data: bytes) -> DocumentText:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # openpyxl 도 파일마다 다른 예외를 던진다
        raise UnreadableDocument("엑셀 파일을 열 수 없어요") from e
    rows: list[str] = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                rows.append(" | ".join(cells))
    book.close()
    return DocumentText(clean_text("\n".join(rows)), pages=len(book.worksheets))


def pdf_pages_as_png(data: bytes, *, max_pages: int, scale: float = 2.0) -> list[bytes]:
    """스캔 PDF 를 모델이 볼 수 있는 그림으로 바꾼다."""
    document = pypdfium2.PdfDocument(data)
    try:
        out: list[bytes] = []
        for index in range(min(len(document), max_pages)):
            image = document[index].render(scale=scale).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            out.append(buffer.getvalue())
        return out
    finally:
        document.close()


def _flatten(image: Image.Image) -> Image.Image:
    """투명한 바탕은 흰색으로 채운다 — 검은 바탕이 되면 검은 글자가 사라진다."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def normalize_image(data: bytes) -> tuple[bytes, str]:
    """모델이 받는 형식(PNG · JPEG)으로 맞춘다. 확장자는 믿지 않고 내용을 본다.

    게시판의 `.gif` 는 대개 JPEG 이다. 움직이는 GIF 는 첫 장만 쓴다. 폭이 너무 넓을 때만 줄인다 —
    긴 소식지 · 휴대폰 캡처는 세로가 6,000 픽셀을 넘는데, 긴 변으로 줄이면 글자가 뭉개진다.

    🚨 원본 바이트를 그대로 보내지 않는다. 사진 파일에 담긴 촬영 장소(GPS) · 기기 같은 부가 정보는
       모델로 보내기 전에 지운다 (처리방침 1장 ⑧ 4호). 다시 그려서 저장하면 부가 정보가 따라가지
       않는다. 지우기 전에 방향 정보대로 돌려 둔다 — 폰은 세로 사진을 가로로 저장하고 "돌려 보라"
       는 표시만 남겨서, 표시가 지워지면 사진이 눕는다. JPEG 는 화질 95 로 다시 저장한다.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            # 여기까지는 머리만 읽었다. 펼치기(load) 전에 크기를 본다
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise UnreadableDocument("사진이 너무 커요")
            image.load()
            fmt = image.format
            converted = _flatten(ImageOps.exif_transpose(image))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise UnreadableDocument("이미지를 열 수 없어요") from e
    if converted.width > MAX_IMAGE_WIDTH:
        # 아주 가로로 긴 그림은 높이가 0 이 되어 resize 가 실패했다 — 1 픽셀은 남긴다
        height = max(1, round(converted.height * MAX_IMAGE_WIDTH / converted.width))
        converted = converted.resize((MAX_IMAGE_WIDTH, height))
    # 그림을 다시 그려도 info 에 남은 JPEG 주석(COM) 같은 값은 저장할 때 따라간다. 비우고 저장한다
    converted.info.clear()
    buffer = io.BytesIO()
    if fmt == "JPEG":
        converted.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue(), "image/jpeg"
    converted.save(buffer, format="PNG")
    return buffer.getvalue(), "image/png"


READERS = {".hwp": read_hwp, ".hwpx": read_hwpx, ".pdf": read_pdf, ".xlsx": read_xlsx}
