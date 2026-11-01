"""복약 API 의 Swagger — 엔드포인트와 요청 · 응답의 모든 칸에 설명이 있는가 (#577).

설명이 빠지면 깨진다. 칸의 뜻은 `Field(description=...)` 에 적는다 (루트 CLAUDE.md §5).
"""

import pytest

from app.main import app

REQUEST_SCHEMAS = ["SubmitMedicationRequest", "MedicationScheduleIn", "MedicationDoseIn"]


RESPONSE_SCHEMAS = ["SubmitMedicationResponse", "MedicationCourseOut", "MedicationDoseOut"]


def _operation() -> dict:
    return app.openapi()["paths"]["/api/v1/children/{cid}/medications"]["post"]


def _has_description(prop: dict) -> bool:
    nodes = [prop, *prop.get("allOf", []), *prop.get("anyOf", [])]
    return any(isinstance(n, dict) and n.get("description", "").strip() for n in nodes)


def test_엔드포인트에_요약_설명_상태_코드가_적혀_있다():
    operation = _operation()

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"201", "400", "403", "409"} <= set(operation["responses"])


def test_Idempotency_Key_헤더가_Swagger_에_보인다():
    names = {p["name"].lower() for p in _operation().get("parameters", [])}

    assert "idempotency-key" in names


@pytest.mark.parametrize("name", REQUEST_SCHEMAS + RESPONSE_SCHEMAS)
def test_요청과_응답의_모든_칸에_설명이_있다(name):
    schemas = app.openapi()["components"]["schemas"]

    assert name in schemas
    assert schemas[name].get("description", "").strip(), f"{name} 에 설명이 없다"
    missing = [
        field for field, prop in schemas[name]["properties"].items() if not _has_description(prop)
    ]
    assert missing == [], f"{name} 설명이 없는 칸: {missing}"


LIST_RESPONSE_SCHEMAS = ["MedicationsResponse"]


def test_조회_엔드포인트와_응답의_모든_칸에_설명이_있다():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/children/{cid}/medications"]["get"]

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"200", "403"} <= set(operation["responses"])
    for name in LIST_RESPONSE_SCHEMAS:
        schema = spec["components"]["schemas"][name]
        assert schema.get("description", "").strip(), f"{name} 에 설명이 없다"
        missing = [f for f, prop in schema["properties"].items() if not _has_description(prop)]
        assert missing == [], f"{name} 설명이 없는 칸: {missing}"


CHECK_REQUEST_SCHEMAS = ["CheckDoseRequest"]


def test_체크_엔드포인트와_요청_응답의_모든_칸에_설명이_있다():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/children/{cid}/medication-doses/{did}/check"]["put"]

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"200", "400", "403", "404", "422"} <= set(operation["responses"])
    for name in CHECK_REQUEST_SCHEMAS:
        schema = spec["components"]["schemas"][name]
        assert schema.get("description", "").strip(), f"{name} 에 설명이 없다"
        missing = [f for f, prop in schema["properties"].items() if not _has_description(prop)]
        assert missing == [], f"{name} 설명이 없는 칸: {missing}"


REVISE_SCHEMAS = ["ReviseMedicationRequest"]


def test_수정_엔드포인트와_요청의_모든_칸에_설명이_있다():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/children/{cid}/medications/{sid}"]["patch"]

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"200", "400", "403", "404", "409"} <= set(operation["responses"])
    assert "idempotency-key" in {p["name"].lower() for p in operation["parameters"]}
    for name in REVISE_SCHEMAS:
        schema = spec["components"]["schemas"][name]
        assert schema.get("description", "").strip(), f"{name} 에 설명이 없다"
        missing = [f for f, prop in schema["properties"].items() if not _has_description(prop)]
        assert missing == [], f"{name} 설명이 없는 칸: {missing}"


def test_수정의_설명은_체크했거나_이미_지난_회차가_기간_밖이_되면_400이라고_적는다():
    # 체크 안 한 어제 회차만 있어도 시작일을 오늘로 늦추면 400 이다 — 적지 않으면 FE 가 모른다
    operation = app.openapi()["paths"]["/api/v1/children/{cid}/medications/{sid}"]["patch"]
    texts = [operation["description"], operation["responses"]["400"]["description"]]

    assert all("체크했거나 이미 지난 회차" in text for text in texts)
    assert all("체크한 회차가" not in text for text in texts)
