"""초대 시도 제한의 IP 버킷 — invite_attempts.py."""

from app.api.invite_attempts import ip_bucket


def test_IPv6_는_64_단위로_묶는다():
    assert ip_bucket("2001:db8:1:2:aaaa::1") == ip_bucket("2001:db8:1:2:bbbb::9")
    assert ip_bucket("2001:db8:1:2::1") != ip_bucket("2001:db8:1:3::1")


def test_IPv4_는_주소_그대로다():
    assert ip_bucket("203.0.113.7") == "203.0.113.7"
    assert ip_bucket(None) == "unknown"


def test_IPv6_로_매핑된_IPv4_는_IPv4_로_센다():
    """듀얼 스택 소켓에서 IPv4 가 ::ffff:x.x.x.x 로 온다. /64 로 묶으면 IPv4 전원이 한 버킷이다."""
    assert ip_bucket("::ffff:1.2.3.4") == "1.2.3.4"
    assert ip_bucket("::ffff:1.2.3.4") != ip_bucket("::ffff:5.6.7.8")
