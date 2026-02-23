from datetime import datetime, date, timedelta
from extensions import db
from models import Funcionario, Batida
from secullum_api import SecullumAPI
import os


def get_api():
    return SecullumAPI(
        os.getenv('SECULLUM_EMAIL'),
        os.getenv('SECULLUM_PASSWORD'),
        os.getenv('SECULLUM_BANCO'),
    )


def parse_date(date_str):
    if not date_str:
        return None
    try:
        for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d/%m/%Y']:
            try:
                return datetime.strptime(date_str.split('.')[0], fmt).date()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def sync_funcionarios():
    api = get_api()
    data = api.listar_funcionarios()
    if not data:
        return False, "Erro ao sincronizar dados da API Secullum ou nenhum dado retornado."

    try:
        existing = {f.id: f for f in Funcionario.query.all()}
        for f in existing.values():
            f.ativo = False

        new_count = updated_count = active_count = 0

        for item in data:
            f_id = str(item.get('Id'))
            if f_id in existing:
                f = existing[f_id]
                updated_count += 1
            else:
                f = Funcionario(id=f_id)
                db.session.add(f)
                new_count += 1

            f.nome = item.get('Nome')
            f.pis = item.get('NumeroPis') or item.get('Pis')
            f.cpf = item.get('Cpf')
            f.rg = item.get('Rg')
            f.carteira = item.get('Carteira')
            f.email = item.get('Email')
            f.celular = item.get('Celular')
            f.telefone = item.get('Telefone')
            f.endereco = item.get('Endereco')
            f.bairro = item.get('Bairro')

            cidade = item.get('Cidade')
            if isinstance(cidade, dict):
                cidade = cidade.get('Descricao') or cidade.get('Nome')
            f.cidade = cidade

            f.uf = item.get('Uf')
            f.cep = item.get('Cep')

            dept = item.get('NomeDepartamento')
            if not dept and item.get('Departamento'):
                dept_obj = item.get('Departamento')
                if isinstance(dept_obj, dict):
                    dept = dept_obj.get('Descricao') or dept_obj.get('Nome')
            f.departamento = dept

            funcao = item.get('NomeFuncao')
            if not funcao and item.get('Funcao'):
                funcao_obj = item.get('Funcao')
                if isinstance(funcao_obj, dict):
                    funcao = funcao_obj.get('Descricao') or funcao_obj.get('Nome')
            f.funcao = funcao

            f.numero_folha = item.get('NumeroFolha')
            f.numero_identificador = item.get('NumeroIdentificador')
            f.admissao = parse_date(item.get('Admissao'))
            f.demissao = parse_date(item.get('Demissao'))
            f.nascimento = parse_date(item.get('Nascimento'))
            f.ativo = item.get('Demissao') is None
            f.data_ultima_sincronizacao = datetime.utcnow()

            if f.ativo:
                active_count += 1

        db.session.commit()
        return True, f"Sync OK! {active_count} ativos, {new_count} novos, {updated_count} atualizados."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro no banco de dados: {str(e)}"


def sync_batidas(data_inicio, data_fim):
    api = get_api()
    registros = api.buscar_batidas(data_inicio, data_fim)
    if registros is None:
        return False, "Erro ao buscar batidas da API."
    if not registros:
        return True, "Nenhuma batida encontrada no período."

    try:
        func_ids = {f.id for f in Funcionario.query.filter_by(ativo=True).all()}
        new_count = updated_count = skipped_count = 0

        ORIGEM_MAP = {0: 'REP', 1: 'Manual', 16: 'App', 32: 'Web'}
        MARCACOES_ESPECIAIS = {'ATESTAD', 'FOLGA', 'FALTA', 'FERIAS', 'NEUTRO', 'DSRFOL',
                               'DSRFALTA', 'COMPENSAR', 'ATESTADO'}

        for registro in registros:
            func_id = str(registro.get('FuncionarioId'))
            if func_id not in func_ids:
                skipped_count += 1
                continue

            data_batida = parse_date(registro.get('Data'))
            if not data_batida:
                continue

            batidas_do_dia = []
            for i in range(1, 6):
                for tipo_str, campo_hora, campo_fonte in [
                    ('Entrada', f'Entrada{i}', f'FonteDadosEntrada{i}'),
                    ('Saida',   f'Saida{i}',   f'FonteDadosSaida{i}'),
                ]:
                    hora = registro.get(campo_hora)
                    if not hora or hora.upper() in MARCACOES_ESPECIAIS:
                        continue
                    if ':' not in hora or len(hora.split(':')) != 2:
                        continue

                    fonte = registro.get(campo_fonte)
                    if isinstance(fonte, dict):
                        origem_id = fonte.get('Origem', 0)
                        origem = ORIGEM_MAP.get(origem_id, f'Origem-{origem_id}')
                    elif fonte:
                        origem = str(fonte)
                    else:
                        origem = 'REP'

                    batidas_do_dia.append({'hora': hora, 'tipo': tipo_str, 'origem': origem})

            for b_info in batidas_do_dia:
                hora_str = b_info['hora']
                existente = Batida.query.filter_by(
                    funcionario_id=func_id,
                    data=data_batida,
                    hora=hora_str,
                ).first()

                if existente:
                    batida = existente
                    updated_count += 1
                else:
                    batida = Batida(funcionario_id=func_id, data=data_batida, hora=hora_str)
                    db.session.add(batida)
                    new_count += 1

                try:
                    h, m = hora_str.split(':')
                    batida.data_hora = datetime.combine(
                        data_batida,
                        datetime.strptime(f'{h}:{m}', '%H:%M').time()
                    )
                except Exception:
                    pass

                batida.tipo = b_info['tipo']
                batida.origem = b_info['origem']
                batida.inconsistente = False
                batida.data_sincronizacao = datetime.utcnow()

        db.session.commit()
        return True, (f"Batidas sincronizadas! {new_count} novas, "
                      f"{updated_count} atualizadas, {skipped_count} ignoradas.")
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao sincronizar batidas: {str(e)}"
