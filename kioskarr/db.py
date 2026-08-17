from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from kioskarr.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from kioskarr import models  # noqa: F401  (ensure models are registered)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all() only creates missing TABLES — it never alters a table that
    already exists, so a new column added to a model (e.g. AppSettings.admin_username)
    silently doesn't show up in an existing database, and every query against that
    model then fails with "no such column". There's no migration framework here
    (Alembic would be overkill for a single-admin homelab app), so backfill any
    columns the live schema is missing, once, idempotently, on every boot instead.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            if table.name not in inspector.get_table_names():
                continue  # brand-new table — create_all() above already made it
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type}"
                if column.default is not None and column.default.is_scalar:
                    ddl += f" DEFAULT {_sql_default_literal(column.default.arg)}"
                conn.execute(text(ddl))


def _sql_default_literal(value: object) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
