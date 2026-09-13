"""에러 봉투와 라우터 등록 순서 — 계약서 §01 · 명세 §3-6

Spring 대응: MockMvc 로 상태코드와 JSON 경로를 검증하는 테스트.
DB 를 보지 않으므로 Postgres 없이 돈다.

이 파일이 지키는 것 둘.
    ① 어떤 에러든 {"error": {"code", ...}} 봉투로 나간다 (프론트가 이 모양만 뜯는다)
    ② POST /auth/logout 이 /auth/{provider} 에 먹히지 않는다 (등록 순서)
"""


async def test_unknown_path_returns_envelope(client):
    """FastAPI 기본 {"detail": "Not Found"} 가 아니라 계약 봉투로 나와야 한다."""
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    # detail 이 없으면 키 자체가 빠진다 (envelope 의 exclude_none).
    assert "detail" not in body["error"]


async def test_logout_is_not_swallowed_by_provider_route(client):
    """헤더 없이 부르면 401 unauthenticated.

    422 가 나오면 provider="logout" 으로 잡힌 것이고, 등록 순서가 뒤집혔다는 뜻이다.
    404 가 나오면 라우터가 마운트되지 않았다는 뜻이다.
    """
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_logout_with_token_reaches_unimplemented_dependency(client):
    """토큰이 있으면 인증 의존성 안까지 들어가고, 지금은 501 이 정상이다.

    #34 에서 get_current_parent 본문이 채워지면 이 테스트는 A-14/A-15 로 대체된다.
    """
    response = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": "Bearer dummy-token"},
    )

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


async def test_health_stays_outside_v1(client):
    """마운트가 기존 운영 엔드포인트를 깨지 않았는지 (계약서 §01)."""
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/api/v1/health")).status_code == 404
