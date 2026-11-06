"""기관 문서 사진의 임시 보관 — S3 (멘토님 제안, 11-04).

화면이 S3 에 바로 올리고, 서버는 그 사진을 내려받자마자 지운다. 사진 본문이 API 서버를
거치지 않는다.

이 모듈만 S3 를 안다. 밖으로는 `UploadTicket` 과 사진 바이트만 나간다.

🚨 원본을 남기지 않는다. 내려받은 뒤에는 성공 · 실패와 상관없이 지운다. 화면이 올리기만 하고 읽히지
   않은 사진은 버킷 수명 규칙(하루)이 지운다 — 그래서 버킷의 버전 관리를 켜면 안 된다. 켜면 지워도
   옛 판이 남는다 (docs/notice/notice-ocr-v1.md §2).
🚨 열쇠(key)에 보호자 id 를 넣고, 읽기 전에 그 보호자의 열쇠인지 본다. 남이 올린 사진을 읽지 않게.
🚨 자격 증명을 코드 · 설정에 두지 않는다. 서버에서는 EC2 인스턴스 역할을 boto3 가 찾는다.
   열쇠 · 서명된 주소를 로그에 남기지 않는다 — 서명된 주소는 5분 동안 누구나 올릴 수 있는 주소다.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from functools import cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

log = logging.getLogger(__name__)

UPLOAD_URL_TTL_SECONDS = 300
_PREFIX = "photos"
_KEY = re.compile(rf"^{_PREFIX}/(?P<parent>[0-9a-f-]{{36}})/[0-9a-f]{{32}}$")
_MISSING = frozenset({"404", "NoSuchKey", "NotFound"})


class MissingUpload(LookupError):
    """올렸다는 사진이 버킷에 없다. 열쇠가 틀렸거나, 이미 읽었거나, 하루가 지났다."""


class UploadTooLarge(ValueError):
    """올린 사진이 상한보다 크다. S3 가 올릴 때 막지만 읽을 때 한 번 더 본다."""


@dataclass(frozen=True)
class UploadTicket:
    """화면이 S3 에 올릴 때 쓰는 값. `fields` 를 폼에 그대로 담고 마지막에 `file` 을 붙인다."""

    url: str
    fields: dict[str, str]
    key: str


@cache
def _client():
    region = settings.PHOTO_UPLOAD_REGION
    return boto3.client(
        "s3",
        region_name=region,
        # 🚨 지역 주소를 박는다. 기본값인 전 세계 주소는 새 버킷이면 하루쯤 307 로 돌려보내서,
        #    화면의 업로드가 실패한다 (11-04 실제 버킷 시험에서 났다)
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "virtual"},
        ),
    )


def new_upload(parent_id: uuid.UUID, *, max_bytes: int) -> UploadTicket:
    """5분짜리 업로드 주소. 크기 상한은 S3 가 올릴 때 검사한다 (서명에 들어간다)."""
    key = f"{_PREFIX}/{parent_id}/{uuid.uuid4().hex}"
    post = _client().generate_presigned_post(
        Bucket=settings.PHOTO_UPLOAD_BUCKET,
        Key=key,
        Conditions=[["content-length-range", 1, max_bytes]],
        ExpiresIn=UPLOAD_URL_TTL_SECONDS,
    )
    return UploadTicket(url=post["url"], fields=dict(post["fields"]), key=key)


def owns(key: str, parent_id: uuid.UUID) -> bool:
    """이 보호자가 받은 업로드 주소의 열쇠인가."""
    m = _KEY.fullmatch(key)  # match 의 $ 는 끝 줄바꿈 앞에서도 맞는다
    return m is not None and m["parent"] == str(parent_id)


def take(key: str, *, max_bytes: int) -> bytes:
    """내려받고 지운다. 크기가 넘어도, 내려받다 실패해도 지운다."""
    client = _client()
    bucket = settings.PHOTO_UPLOAD_BUCKET
    try:
        try:
            head = client.head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _MISSING:
                raise MissingUpload from None
            raise
        if head["ContentLength"] > max_bytes:
            raise UploadTooLarge
        return client.get_object(Bucket=bucket, Key=key)["Body"].read(max_bytes + 1)
    finally:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as e:
            # 지우기만 실패했다. 사진은 버킷 수명 규칙이 하루 안에 지운다
            log.warning("photo upload delete failed: %s", type(e).__name__)
