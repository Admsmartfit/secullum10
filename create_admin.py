"""
Cria o usuario administrador inicial.
Execute uma vez apos a migracao.
Usage: python create_admin.py
"""
from app import create_app
from extensions import db
from models import Usuario


def main():
    app = create_app()
    with app.app_context():
        email = input('Email do admin: ').strip().lower()
        nome = input('Nome: ').strip()
        senha = input('Senha: ').strip()

        if Usuario.query.filter_by(email=email).first():
            print('Erro: email ja cadastrado.')
            return

        admin = Usuario(nome=nome, email=email, nivel_acesso='gestor')
        admin.set_senha(senha)
        db.session.add(admin)
        db.session.commit()
        print(f'Admin "{email}" criado com sucesso!')


if __name__ == '__main__':
    main()
