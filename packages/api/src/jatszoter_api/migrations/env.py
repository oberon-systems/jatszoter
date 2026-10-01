from alembic import context
from sqlmodel import SQLModel, create_engine

import jatszoter_api.models  # noqa: F401  registers the tables on SQLModel.metadata
from jatszoter_api.settings import Settings

config = context.config
dsn = config.get_main_option("sqlalchemy.url") or str(Settings().database_dsn)


def run_offline() -> None:
    context.configure(
        url=dsn,
        target_metadata=SQLModel.metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(dsn)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=SQLModel.metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
