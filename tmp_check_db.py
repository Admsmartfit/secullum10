from app import app
from extensions import db
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print("Tables in DB:", tables)
    if 'feriados' not in tables:
        print("Creating missing tables...")
        db.create_all()
        print("Done.")
    else:
        print("'feriados' table already exists.")
