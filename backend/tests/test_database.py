from sqlalchemy import inspect, text

from app.database import engine, migrate_schema


def test_migrate_schema_stamps_an_unversioned_existing_database():
    with engine.begin() as connection:
        if "alembic_version" in inspect(engine).get_table_names():
            connection.execute(text("DROP TABLE alembic_version"))

    migrate_schema()

    with engine.connect() as connection:
        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert version == "0002_request_outcomes"
