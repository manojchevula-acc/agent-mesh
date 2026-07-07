"""Dialect-portability tests — the same tool SQL must work on Postgres and MySQL."""

import pytest

from sql_agent.config import settings
from sql_agent.db import dialect as d


@pytest.fixture
def force_dialect(monkeypatch):
    def _set(name: str):
        monkeypatch.setattr(settings, "db_dialect", name)
    return _set


def test_infer_from_dsn(monkeypatch):
    monkeypatch.setattr(settings, "db_dialect", "")
    monkeypatch.setattr(settings, "db_dsn", "mysql+pymysql://u:p@h:3306/db")
    assert d.current_dialect() == d.MYSQL
    monkeypatch.setattr(settings, "db_dsn", "postgresql+psycopg2://u:p@h:5432/db")
    assert d.current_dialect() == d.POSTGRES
    monkeypatch.setattr(settings, "db_dsn", "sqlite:///./fab.db")
    assert d.current_dialect() == d.SQLITE


def test_explicit_dialect_overrides_dsn(monkeypatch):
    monkeypatch.setattr(settings, "db_dsn", "postgresql://u:p@h/db")
    monkeypatch.setattr(settings, "db_dialect", "mysql")
    assert d.current_dialect() == d.MYSQL


def test_concat_postgres(force_dialect):
    force_dialect("postgres")
    assert d.concat("a", "b", "c") == "(a || b || c)"


def test_concat_mysql(force_dialect):
    force_dialect("mysql")
    assert d.concat("a", "b", "c") == "CONCAT(a, b, c)"


def test_csv_membership_postgres(force_dialect):
    force_dialect("postgres")
    clause = d.csv_membership_clause("eligible_segments", "segment")
    assert clause == "(',' || eligible_segments || ',') LIKE ('%,' || :segment || ',%')"


def test_csv_membership_mysql(force_dialect):
    force_dialect("mysql")
    clause = d.csv_membership_clause("eligible_segments", "segment")
    assert clause == "CONCAT(',', eligible_segments, ',') LIKE CONCAT('%,', :segment, ',%')"


def test_concat_sqlite_uses_pipes(force_dialect):
    force_dialect("sqlite")
    assert d.concat("a", "b") == "(a || b)"


def test_sqlglot_dialect_mapping(force_dialect):
    force_dialect("postgres")
    assert d.sqlglot_dialect() == "postgres"
    force_dialect("mysql")
    assert d.sqlglot_dialect() == "mysql"
    force_dialect("sqlite")
    assert d.sqlglot_dialect() == "sqlite"


def test_prompt_grounding_per_dialect(force_dialect):
    force_dialect("mysql")
    assert d.dialect_label() == "MySQL 8.0"
    assert "CONCAT" in d.dialect_notes()
    force_dialect("postgres")
    assert d.dialect_label() == "PostgreSQL"
    assert "||" in d.dialect_notes()
    force_dialect("sqlite")
    assert d.dialect_label() == "SQLite"
