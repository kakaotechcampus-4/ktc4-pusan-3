from sqlalchemy import Enum as SAEnum


def enum_col(*values: str, name: str) -> SAEnum:
    """값이 정해진 컬럼을 VARCHAR + CHECK 로 만든다.

    native_enum=False — PG 네이티브 ENUM 타입을 쓰지 않는다.
      네이티브는 값을 지우는 명령이 없다(`ALTER TYPE ... DROP VALUE` 가 존재하지 않음).
      그리고 Alembic 이 두 곳에서 깨진다 —
        · downgrade 가 타입을 안 지워서 `downgrade base` → `upgrade head` 왕복이
          "type already exists" 로 실패한다
        · ENUM 값 추가를 autogenerate 가 잡지 못해 빈 마이그레이션이 나온다 (에러도 없음)
      우리는 값이 계속 움직이는 단계라 CHECK 교체 한 번으로 끝나는 쪽을 택했다.

    length=32 — 32자를 쓰라는 뜻이 아니라 여유를 두는 것이다.
      이 인자를 빼면 SQLAlchemy 가 "현재 값 중 최장 길이"로 VARCHAR 를 만든다.
      engagement_level 이 varchar(4)('high') 가 되고, 나중에 'medium' 을 추가할 때
      CHECK 를 고쳐도 "value too long for type character varying(4)" 로 막힌다.
      에러가 값 검증이 아니라 길이 초과로 나서 원인을 찾기 어렵다.
      현재 최장 값은 'institution_notice'(18자)이므로 32 면 충분하고,
      VARCHAR 는 선언 길이만큼 공간을 미리 잡지 않아 낭비도 없다.
    """
    return SAEnum(
        *values,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        validate_strings=True,
    )
