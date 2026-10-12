from app.db.database import Base, engine
from app.models.entities import *
from sqlalchemy import inspect, text
import sqlite3
from pathlib import Path

def init_db(bind=None):
    bind = bind or engine
    Base.metadata.create_all(bind=bind)
    additions = {'recommendation_history': {
        'request_id': "VARCHAR(36) NOT NULL DEFAULT ''",
        'intent_json': "TEXT NOT NULL DEFAULT '{}'",
        'rank_position': 'INTEGER NOT NULL DEFAULT 0',
    }, 'user_preferences': {'learned_profile_json': "TEXT NOT NULL DEFAULT '{}'"}}
    missing = {}
    for table, fields in additions.items():
        columns = {x['name'] for x in inspect(bind).get_columns(table)}
        for name, sql_type in fields.items():
            if name not in columns:
                missing[(table, name)] = sql_type
    # Back up existing SQLite data once before the additive, idempotent migration.
    database = bind.url.database
    if missing and bind.dialect.name == 'sqlite' and database and database != ':memory:':
        backup = Path(str(database) + '.pre-feedback-learning.bak')
        if not backup.exists():
            with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
                source.backup(target)
    with bind.begin() as conn:
        for (table, name), sql_type in missing.items():
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
