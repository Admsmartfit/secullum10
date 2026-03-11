from app import app, db
from models import Feriado

with app.app_context():
    try:
        Feriado.__table__.create(db.engine)
        print("Tabela 'feriados' criada com sucesso!")
    except Exception as e:
        print(f"Erro ao criar tabela 'feriados' (provavelmente já existe): {e}")
