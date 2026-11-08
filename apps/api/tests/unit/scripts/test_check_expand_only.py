"""마이그레이션 expand-only 검사 (#782)."""

import pytest

from scripts.check_expand_only import (
    BASELINE,
    VERSIONS_DIR,
    check,
    find_violations,
    main,
    new_migrations,
)


def upgrade(body: str, before: str = "") -> str:
    lines = "".join(f"    {line}\n" for line in body.splitlines())
    return f"{before}\ndef upgrade():\n{lines}\n\ndef downgrade():\n    op.drop_column('t', 'c')\n"


@pytest.mark.parametrize(
    "body",
    [
        "op.drop_column('t', 'c')",
        "op.drop_table('t')",
        "op.rename_table('a', 'b')",
        "op.alter_column('t', 'c', new_column_name='d')",
        "op.alter_column('t', 'c', nullable=False)",
        "op.add_column('t', sa.Column('c', sa.UUID(), nullable=False))",
        "op.add_column('t', column=sa.Column('c', sa.UUID(), nullable=False))",
        "op.add_column('t', sa.Column('c', sa.UUID(), nullable=False, server_default=None))",
        "op.add_column('t', sa.Column('c', sa.Integer(), primary_key=True))",
        # NOT NULL 인 게 보이는 컬럼의 기본값 떼기 — 직전 코드의 INSERT 가 깨진다
        "op.alter_column('t', 'c', server_default=None, existing_nullable=False)",
        "op.add_column('t', sa.Column('c', sa.Integer(), nullable=False, server_default='0'))\n"
        "op.alter_column('t', 'c', server_default=None)",
        # 넓히기인지 모르는 type_ — VARCHAR 좁히기 · existing_type 없음 · 다른 타입
        "op.alter_column('t', 'c', type_=sa.String(8), existing_type=sa.String(64))",
        "op.alter_column('t', 'c', type_=sa.Text())",
        "op.alter_column('t', 'c', type_=sa.Integer(), existing_type=sa.String(8))",
        # autogenerate 가 쓰는 키워드 길이 · 상수가 아닌 길이도 좁히기일 수 있다
        "op.alter_column('t', 'c', type_=sa.String(length=8), existing_type=sa.VARCHAR(length=64))",
        "op.alter_column('t', 'c', type_=sa.String(N), existing_type=sa.String(64))",
        # CHECK 교체 — enum 값 빼기 · 바꾸기가 이렇게 생겼다
        "op.drop_constraint('ck', 't', type_='check')\n"
        "op.create_check_constraint('ck', 't', \"k IN ('a')\")",
        "batch_op.drop_constraint('ck', type_='check')\n"
        "batch_op.create_check_constraint('ck', \"k IN ('a')\")",
        # 원문 SQL
        "op.execute('ALTER TABLE t DROP COLUMN c')",
        "op.execute('ALTER TABLE t DROP c')",
        "op.execute('ALTER TABLE t ADD COLUMN d int, DROP COLUMN c')",
        "op.execute('DROP TABLE t')",
        "op.execute('ALTER TABLE a RENAME TO b')",
        "op.execute('ALTER TABLE t RENAME COLUMN a TO b')",
        "op.execute('ALTER TABLE t ALTER COLUMN c SET NOT NULL')",
        "op.execute('ALTER TABLE t ALTER COLUMN c TYPE text')",
        "op.execute('ALTER TABLE t ALTER c TYPE int')",
        "op.execute(sa.text('ALTER TABLE t DROP COLUMN c'))",
        "op.execute(f'ALTER TABLE {TABLE} DROP COLUMN c')",
        "op.execute('''ALTER TABLE t\n    DROP\n    COLUMN c''')",
        # batch_op 도 같다
        "batch_op.add_column(sa.Column('c', sa.Integer(), nullable=False))",
    ],
)
def test_직전_코드를_깨는_연산을_찾는다(body):
    assert find_violations(upgrade(body))


@pytest.mark.parametrize(
    "body",
    [
        "op.add_column('t', sa.Column('c', sa.UUID(), nullable=True))",
        "op.add_column('t', sa.Column('c', sa.UUID(), nullable=False, server_default='x'))",
        "op.alter_column('t', 'c', nullable=True)",
        "op.alter_column('t', 'c', server_default=None, nullable=True)",
        "op.alter_column('t', 'c', server_default='x')",
        "op.alter_column('t', 'c', type_=sa.String(32), existing_type=sa.String(6))",
        "op.alter_column('t', 'c', type_=sa.Text(), existing_type=sa.String(6))",
        "op.alter_column('t', 'c', type_=sa.String(length=32), existing_type=sa.VARCHAR(length=6))",
        "op.drop_index('ix_t_c')",
        "op.drop_constraint('uq_t_c', 't')",
        "op.create_check_constraint('ck_new', 't', 'c > 0')",
        "op.create_table('t', sa.Column('id', sa.UUID(), nullable=False))",
        "op.execute('CREATE INDEX ix ON t (c)')",
        "op.execute(\"UPDATE t SET kind = 'renamed' WHERE kind = 'dropped'\")",
        "op.execute('ALTER INDEX ix RENAME TO ix2')",
        "op.execute('ALTER TABLE t ALTER COLUMN c DROP NOT NULL')",
        "op.execute('ALTER TABLE t DROP CONSTRAINT ck')",
        "op.execute('ALTER TABLE t ADD COLUMN renamed_at timestamptz')",
    ],
)
def test_expand_인_연산은_통과한다(body):
    assert find_violations(upgrade(body)) == []


def test_downgrade_는_보지_않는다():
    assert find_violations(upgrade("pass")) == []


def test_downgrade_의_같은_이름_변수는_upgrade_SQL_로_섞이지_않는다():
    src = (
        "def upgrade():\n    sql = 'UPDATE t SET a = 1'\n    op.execute(sql)\n\n"
        "def downgrade():\n    sql = 'ALTER TABLE t DROP COLUMN a'\n    op.execute(sql)\n"
    )
    assert find_violations(src) == []


def test_upgrade_가_부르는_같은_파일_함수도_본다():
    # 헬퍼가 헬퍼를 부르고, 서로 부르는 재귀도 멈춘다
    before = (
        "def _a():\n    _b()\n\n"
        "def _b():\n    _a()\n    op.drop_column('t', 'c')\n\n"
        "def _unused():\n    op.drop_table('t')\n"
    )
    assert [what for _, what in find_violations(upgrade("_a()", before))] == ["drop_column"]


def test_헬퍼_안의_CHECK_교체도_본다():
    # b3e7d9c05a12 · d8e9f0a1b2c3 모양 — 이름은 모듈 상수, 연산은 헬퍼 안
    before = (
        'CONSTRAINT = "suggestion_agent"\n\n'
        "def _swap(values):\n"
        "    op.drop_constraint(CONSTRAINT, 'suggestion', type_='check')\n"
        "    op.create_check_constraint(CONSTRAINT, 'suggestion', _check(values))\n"
    )
    assert find_violations(upgrade("_swap(AFTER)", before))


def test_변수에_담아_넘긴_SQL_도_본다():
    assert find_violations(upgrade("op.execute(SQL)", "SQL = 'ALTER TABLE t DROP COLUMN c'\n"))
    assert find_violations(upgrade("sql = 'DROP TABLE t'\nop.execute(sql)"))


# --- 표시는 그 연산에만 ---


def test_contract_표시는_바로_위_줄_주석의_그_연산만_넘긴다():
    body = (
        "# contract: #753\n"
        "op.drop_column('t', 'a')\n"
        "op.add_column('t', sa.Column('b', sa.Integer(), nullable=False))"
    )
    assert [what for _, what in find_violations(upgrade(body))] == [
        "add_column — server_default 없는 NOT NULL 컬럼"
    ]


def test_contract_표시는_같은_줄에_달아도_된다():
    assert find_violations(upgrade("op.drop_column('t', 'a')  # contract: #753")) == []


@pytest.mark.parametrize(
    "body, before",
    [
        # 문자열 · docstring 안의 글자는 주석이 아니다
        ("op.drop_column('t', 'a')", '"""`# contract: #1` 같은 표시는 이렇게 쓴다"""\n'),
        ("'# contract: #1'\nop.drop_column('t', 'a')", ""),
        # #0 · 번호 없음
        ("# contract: #0\nop.drop_column('t', 'a')", ""),
        ("# contract:\nop.drop_column('t', 'a')", ""),
        # 위 연산의 뒤에 붙은 주석은 아래 연산의 표시가 아니다
        ("op.drop_column('t', 'a')  # contract: #753\nop.drop_column('t', 'b')", ""),
        # 두 줄 위
        ("# contract: #753\n\nop.drop_column('t', 'a')", ""),
        # 파일 맨 위
        ("op.drop_column('t', 'a')", "# contract: #753\n"),
    ],
)
def test_표시로_인정하지_않는_것(body, before):
    assert find_violations(upgrade(body, before))


def test_expand_ok_는_넓히기_판정만_넘긴다():
    check_swap = (
        "op.drop_constraint('ck', 't', type_='check')\n"
        "# expand-ok: 값을 더하기만 한다\n"
        "op.create_check_constraint('ck', 't', \"k IN ('a', 'b')\")"
    )
    assert find_violations(upgrade(check_swap)) == []
    assert find_violations(upgrade("# expand-ok: 괜찮다\nop.drop_column('t', 'a')"))
    assert find_violations(upgrade("# expand-ok:\nop.alter_column('t', 'c', type_=sa.Text())"))


# --- 검사 대상 ---


def revision(
    rev: str, down: str | tuple[str, ...] | None, body: str = "pass", extra: str = ""
) -> str:
    return f'{extra}\nrevision = "{rev}"\ndown_revision = {down!r}\n\n' + upgrade(body)


@pytest.fixture
def versions(tmp_path):
    # BASELINE 까지 둘, 그 뒤 하나 — 앞의 둘은 칼럼을 지워도 검사하지 않는다
    d = tmp_path / "versions"
    d.mkdir()
    (d / "a.py").write_text(revision("aaa", None, "op.drop_column('t', 'c')"))
    (d / "b.py").write_text(revision(BASELINE, "aaa", "op.drop_table('t')"))
    (d / "c.py").write_text(revision("ccc", BASELINE))
    return d


def test_BASELINE_과_그_조상은_검사하지_않는다(versions):
    assert [p.name for p in new_migrations(versions)] == ["c.py"]
    assert main([str(versions)]) == 0


def test_BASELINE_뒤에_칼럼을_지우면_실패한다(versions, capsys):
    (versions / "d.py").write_text(revision("ddd", "ccc", "op.drop_column('t', 'c')"))

    assert main([str(versions)]) == 1
    out = capsys.readouterr().out
    assert "d.py:" in out and "drop_column" in out
    assert "# contract: #" in out  # 고치는 법을 알려 준다


def test_contract_표시가_있으면_통과한다(versions):
    (versions / "d.py").write_text(
        revision("ddd", "ccc", "# contract: #753\nop.drop_column('t', 'c')")
    )

    assert main([str(versions)]) == 0


def test_merge_revision_뒤도_따라간다(versions):
    (versions / "d.py").write_text(revision("ddd", BASELINE))
    (versions / "e.py").write_text(revision("eee", ("ccc", "ddd")))
    (versions / "f.py").write_text(revision("fff", "eee", "op.drop_table('t')"))

    assert [p.name for p, _, _ in check(new_migrations(versions))] == ["f.py"]


def test_BASELINE_보다_앞에서_갈라져_merge_된_revision_도_본다(versions):
    # aaa 에서 갈라진 옛 브랜치 ddd 가 BASELINE 뒤 merge 로 합쳐진다.
    # ddd 는 BASELINE 의 자손이 아니다
    (versions / "d.py").write_text(revision("ddd", "aaa", "op.drop_table('t')"))
    (versions / "e.py").write_text(revision("eee", ("ccc", "ddd")))

    assert [p.name for p, _, _ in check(new_migrations(versions))] == ["d.py"]


def test_BASELINE_이_없으면_통과시키지_않는다(tmp_path):
    (tmp_path / "a.py").write_text(revision("aaa", None, "op.drop_table('t')"))

    with pytest.raises(SystemExit):
        new_migrations(tmp_path)


def test_지금_마이그레이션은_통과한다():
    assert main([]) == 0


def test_규칙이_약해지지_않았다():
    # BASELINE 을 무시하고 전체에 돌리면 expand-only 를 어긴 기존 마이그레이션이 걸려야 한다.
    # 분류는 PR #807 리뷰 답. 새 오탐은 CHECK 넓히기(값 더하기)뿐이다 — `# expand-ok:` 로 넘긴다
    flagged = {p.name.split("_")[0] for p, _, _ in check(sorted(VERSIONS_DIR.glob("*.py")))}

    assert flagged == {
        # 이전 규칙부터 걸리던 17개
        "0f4480ddc873", "1b7312ff8880", "276e84441799", "3e7a9c1d5b20", "4c7e2b9d1a63",
        "5c09288aa40c", "a3e9c1f7b204", "b4d8f2a6c913", "b8d3e5a91c47", "c37a8e1d902f",
        "c5a81f0d3b62", "d7c204e9a1b6", "d916c437f5dd", "d9e1f3a5b7c2", "db405527cc82",
        "e075ad671ec9", "f1c8b2d3a4e5",
        # 헬퍼 · CHECK 교체로 새로 걸리는 것 — 앞 둘은 직전 코드를 깬다(값 바꾸기), 뒤 셋은 넓히기
        "b3e7d9c05a12", "d8e9f0a1b2c3", "a7e3c91d5f20", "ab49da81c5c5", "e1d147d1b701",
    }  # fmt: skip
