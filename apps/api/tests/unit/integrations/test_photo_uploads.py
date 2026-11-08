"""사진 임시 보관(S3). 진짜 AWS 는 부르지 않는다 — moto 가 S3 를 흉내 낸다."""

import base64
import json
import uuid

import boto3
import pytest
from moto import mock_aws

from app.core.config import settings
from app.integrations import photo_uploads

BUCKET = "test-photo-uploads"
PARENT = uuid.UUID("0193f0a0-0000-7000-8000-000000000001")
LIMIT = 100


@pytest.fixture
def s3(monkeypatch):
    # 개발자 컴퓨터의 진짜 AWS 로그인을 절대 쓰지 않게 가짜 키로 덮는다
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setattr(settings, "PHOTO_UPLOAD_BUCKET", BUCKET)
    photo_uploads._client.cache_clear()
    with mock_aws():
        client = boto3.client("s3", region_name=settings.PHOTO_UPLOAD_REGION)
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": settings.PHOTO_UPLOAD_REGION},
        )
        yield client
    photo_uploads._client.cache_clear()


def _keys(client) -> list[str]:
    return [o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]


def test_업로드_주소는_보호자_열쇠와_크기_상한을_담는다(s3):
    ticket = photo_uploads.new_upload(PARENT, max_bytes=LIMIT)
    assert photo_uploads.owns(ticket.key, PARENT)
    assert ticket.fields["key"] == ticket.key
    policy = json.loads(base64.b64decode(ticket.fields["policy"]))
    assert ["content-length-range", 1, LIMIT] in policy["conditions"]


def test_읽으면_바로_지운다(s3):
    key = photo_uploads.new_upload(PARENT, max_bytes=LIMIT).key
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"photo")

    assert photo_uploads.take(key, max_bytes=LIMIT) == b"photo"
    assert _keys(s3) == []


def test_너무_크면_읽지_않고_지운다(s3):
    key = photo_uploads.new_upload(PARENT, max_bytes=LIMIT).key
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"x" * (LIMIT + 1))

    with pytest.raises(photo_uploads.UploadTooLarge):
        photo_uploads.take(key, max_bytes=LIMIT)
    assert _keys(s3) == []


def test_없는_사진은_없다고_말한다(s3):
    key = photo_uploads.new_upload(PARENT, max_bytes=LIMIT).key
    with pytest.raises(photo_uploads.MissingUpload):
        photo_uploads.take(key, max_bytes=LIMIT)


def test_지우기가_실패해도_읽은_사진은_돌려준다(s3, monkeypatch):
    # 사진은 버킷 수명 규칙이 하루 안에 지운다. 지우기 실패로 보호자 요청까지 실패시키지 않는다
    key = photo_uploads.new_upload(PARENT, max_bytes=LIMIT).key
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"photo")
    client = photo_uploads._client()

    def broken(**_):
        raise photo_uploads.ClientError({"Error": {"Code": "500"}}, "DeleteObject")

    monkeypatch.setattr(client, "delete_object", broken)
    assert photo_uploads.take(key, max_bytes=LIMIT) == b"photo"


@pytest.mark.parametrize(
    "key",
    [
        f"photos/{uuid.uuid4()}/{uuid.uuid4().hex}",  # 다른 보호자
        f"photos/{PARENT}/../{uuid.uuid4().hex}",
        f"other/{PARENT}/{uuid.uuid4().hex}",
        f"photos/{PARENT}/{uuid.uuid4().hex}/more",
        f"photos/{PARENT}/{uuid.uuid4().hex}\n",  # re.match 의 $ 는 끝 줄바꿈 앞에서도 맞는다
        "",
    ],
)
def test_남의_열쇠나_모양이_다른_열쇠는_내_것이_아니다(key):
    assert photo_uploads.owns(key, PARENT) is False


def test_읽지_않고_지우기(s3):
    key = photo_uploads.new_upload(PARENT, max_bytes=LIMIT).key
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"photo")

    photo_uploads.discard(key)

    assert _keys(s3) == []
