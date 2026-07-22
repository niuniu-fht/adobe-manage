from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def migrate_schema() -> None:
    from alembic import command
    from alembic.config import Config

    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if table_names and "instances" in table_names:
        metric_columns = {
            column["name"] for column in inspector.get_columns("metric_samples")
        }
        if "manager_settings" in table_names:
            baseline = "0003_account_preferences"
        elif {"successful_requests", "failed_requests"} <= metric_columns:
            baseline = "0002_request_outcomes"
        else:
            baseline = "0001_initial"
        with engine.begin() as connection:
            if "alembic_version" not in table_names:
                connection.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                    )
                )
            current = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
            if not current:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
                    {"version": baseline},
                )
    command.upgrade(config, "head")
