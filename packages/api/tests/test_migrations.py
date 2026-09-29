from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlmodel import SQLModel, create_engine, inspect

import jatszoter_api.models  # noqa: F401  registers the tables on SQLModel.metadata


def test_head_matches_the_models(database_dsn: str, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_dsn)

    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), SQLModel.metadata)

    engine.dispose()
    assert diff == []


def test_schema_has_no_foreign_keys(database_dsn: str, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_dsn)
    inspector = inspect(engine)

    tables = inspector.get_table_names()
    foreign_keys = {table: inspector.get_foreign_keys(table) for table in tables}

    engine.dispose()
    assert all(keys == [] for keys in foreign_keys.values())


def test_downgrade_leaves_no_tables(database_dsn: str, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    engine = create_engine(database_dsn)

    tables = inspect(engine).get_table_names()

    engine.dispose()
    assert tables == ["alembic_version"]
