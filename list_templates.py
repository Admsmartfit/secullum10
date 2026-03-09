from app import create_app
from models import TemplateDocumento

app = create_app()
with app.app_context():
    templates = TemplateDocumento.query.all()
    for t in templates:
        print(f"ID: {t.id} | Nome: {t.nome} | Arquivo: {t.arquivo_nome}")
