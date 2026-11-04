"""Use the service connection; never put database credentials in alembic.ini."""
from alembic import context

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    from app.db import engine
    connection = engine.connect()
    owns_connection = True
else:
    owns_connection = False
try:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
finally:
    if owns_connection:
        connection.close()
