"""Forward-only schema upgrades, serialized for PostgreSQL deployments."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text


def upgrade_database(engine: Engine) -> None:
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    with engine.connect() as connection:
        locked = connection.dialect.name == "postgresql"
        if locked:
            connection.execute(text("SELECT pg_advisory_lock(73500931)"))
            connection.commit()
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        finally:
            if locked:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(73500931)"))
                connection.commit()
