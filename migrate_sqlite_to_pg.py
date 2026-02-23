"""
Migra dados do SQLite (instance/secullum.db) para o PostgreSQL.
Execute UMA vez após configurar DATABASE_URL no .env.
"""
import sqlite3
from datetime import datetime
from app import create_app
from extensions import db
from models import Funcionario, Batida


def parse_date(val):
    if not val:
        return None
    for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%d/%m/%Y']:
        try:
            return datetime.strptime(str(val).split('.')[0], fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(val):
    if not val:
        return None
    for fmt in ['%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S']:
        try:
            return datetime.strptime(str(val).split('.')[0], fmt)
        except ValueError:
            continue
    return None


def migrate():
    app = create_app()
    with app.app_context():
        sqlite_path = 'instance/secullum.db'
        conn = sqlite3.connect(sqlite_path)

        def fetchall_as_dicts(query):
            cur = conn.execute(query)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

        print('Migrando funcionarios...')
        rows = fetchall_as_dicts('SELECT * FROM funcionarios')
        migrated_f = 0
        for r in rows:
            if db.session.get(Funcionario, r['id']):
                continue
            f = Funcionario(
                id=r['id'],
                nome=r.get('nome'),
                pis=r.get('pis'),
                cpf=r.get('cpf'),
                rg=r.get('rg'),
                carteira=r.get('carteira'),
                email=r.get('email'),
                celular=r.get('celular'),
                telefone=r.get('telefone'),
                endereco=r.get('endereco'),
                bairro=r.get('bairro'),
                cidade=r.get('cidade'),
                uf=r.get('uf'),
                cep=r.get('cep'),
                departamento=r.get('departamento'),
                funcao=r.get('funcao'),
                numero_folha=r.get('numero_folha'),
                numero_identificador=r.get('numero_identificador'),
                admissao=parse_date(r.get('admissao')),
                demissao=parse_date(r.get('demissao')),
                nascimento=parse_date(r.get('nascimento')),
                ativo=bool(r.get('ativo', True)),
                data_ultima_sincronizacao=parse_datetime(r.get('data_ultima_sincronizacao')),
            )
            db.session.add(f)
            migrated_f += 1

        db.session.commit()
        print(f'  {migrated_f} funcionarios migrados.')

        print('Migrando batidas...')
        try:
            rows_b = fetchall_as_dicts('SELECT * FROM batidas')
        except sqlite3.OperationalError:
            print('  Tabela batidas nao encontrada no SQLite, pulando.')
            rows_b = []

        migrated_b = 0
        for r in rows_b:
            exists = Batida.query.filter_by(
                funcionario_id=r['funcionario_id'],
                data=parse_date(r['data']),
                hora=r['hora'],
            ).first()
            if exists:
                continue
            b = Batida(
                funcionario_id=r['funcionario_id'],
                data=parse_date(r['data']),
                hora=r['hora'],
                data_hora=parse_datetime(r.get('data_hora')),
                tipo=r.get('tipo'),
                origem=r.get('origem'),
                inconsistente=bool(r.get('inconsistente', False)),
                justificativa=r.get('justificativa'),
                latitude=r.get('latitude'),
                longitude=r.get('longitude'),
                data_sincronizacao=parse_datetime(r.get('data_sincronizacao')),
            )
            db.session.add(b)
            migrated_b += 1

        db.session.commit()
        conn.close()
        print(f'  {migrated_b} batidas migradas.')
        print('Migracao concluida!')


if __name__ == '__main__':
    migrate()
