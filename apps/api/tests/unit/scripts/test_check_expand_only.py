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


def upgrade(body: str) -> str:
    return f"def upgrade():\n    {body}\n\n\ndef downgrade():\n    op.drop_column('t', 'c')\n"


@pytest.mark.parametrize(
    "body",
    [
        "op.drop_column('t', 'c')",
        "op.drop_table('t')",
        "op.rename_table('a', 'b')",
        "op.alter_column('t', 'c', new_column_name='d')",
        "op.alter_column('t', 'c', nullable=False)",
        "op.add_column('t', sa.Column('c', sa.UUID(), nullable=False))",
        "op.execute('ALTER TABLE t DROP COLUMN c')",
        "op.execute('DROP TABLE t')",
        "op.execute('ALTER TABLE a RENAME TO b')",
        "op.execute('ALTER TABLE t ALTER COLUMN c SET NOT NULL')",
        "op.execute('ALTER TABLE t ALTER COLUMN c TYPE text')",
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
        "op.alter_column('t', 'c', type_=sa.Text())",
        "op.drop_index('ix_t_c')",
        "op.drop_constraint('uq_t_c', 't')",
        "op.create_table('t', sa.Column('id', sa.UUID(), nullable=False))",
        "op.execute('CREATE INDEX ix ON t (c)')",
    ],
)
def test_expand_인_연산은_통과한다(body):
    assert find_violations(upgrade(body)) == []


def test_downgrade_는_보지_않는다():
    assert find_violations(upgrade("pass")) == []


def revision(
    rev: str, down: str | tuple[str, ...] | None, body: str = "pass", extra: str = ""
) -> str:
    return f'{extra}\nrevision = "{rev}"\ndown_revision = {down!r}\n\n' + upgrade(body)


@pytest.fixture
def versions(tmp_path):
    # BASELINE 까지 둘, 그 뒤 하나 — 앞의 둘은 칼럼을 지워도 검사하지 않는다
    (tmp_path / "a.py").write_text(revision("aaa", None, "op.drop_column('t', 'c')"))
    (tmp_path / "b.py").write_text(revision(BASELINE, "aaa", "op.drop_table('t')"))
    (tmp_path / "c.py").write_text(revision("ccc", BASELINE))
    return tmp_path


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
        revision("ddd", "ccc", "op.drop_column('t', 'c')", extra="# contract: #753")
    )

    assert main([str(versions)]) == 0


def test_merge_revision_뒤도_따라간다(versions):
    (versions / "d.py").write_text(revision("ddd", BASELINE))
    (versions / "e.py").write_text(revision("eee", ("ccc", "ddd")))
    (versions / "f.py").write_text(revision("fff", "eee", "op.drop_table('t')"))

    assert [p.name for p, _, _ in check(new_migrations(versions))] == ["f.py"]


def test_BASELINE_이_없으면_통과시키지_않는다(tmp_path):
    (tmp_path / "a.py").write_text(revision("aaa", None, "op.drop_table('t')"))

    with pytest.raises(SystemExit):
        new_migrations(tmp_path)


def test_지금_마이그레이션은_통과한다():
    assert main([]) == 0


def test_규칙이_약해지지_않았다():
    # BASELINE 을 무시하고 전체에 돌리면 expand-only 를 어긴 기존 마이그레이션이 걸려야 한다
    flagged = {p.name.split("_")[0] for p, _, _ in check(sorted(VERSIONS_DIR.glob("*.py")))}

    assert len(flagged) == 17
    assert {"db405527cc82", "1b7312ff8880", "4c7e2b9d1a63", "c37a8e1d902f"} <= flagged
