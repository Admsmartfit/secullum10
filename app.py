
import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
from models import db, Funcionario, Configuracao, Batida
from secullum_api import SecullumAPI
import pandas as pd
from io import BytesIO

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///secullum.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Initialize API Client
def get_api():
    return SecullumAPI(
        os.getenv('SECULLUM_EMAIL'),
        os.getenv('SECULLUM_PASSWORD'),
        os.getenv('SECULLUM_BANCO')
    )

with app.app_context():
    db.create_all()

# Simple Cache for Batidas
_cache = {}
CACHE_TIMEOUT = timedelta(minutes=15)

def get_cached_batidas(data_inicio, data_fim):
    key = f"{data_inicio}_{data_fim}"
    if key in _cache:
        cached_data, timestamp = _cache[key]
        if datetime.now() - timestamp < CACHE_TIMEOUT:
            return cached_data
    
    api = get_api()
    data = api.buscar_batidas(data_inicio, data_fim)
    _cache[key] = (data, datetime.now())
    return data

@app.route('/')
def dashboard():
    today = date.today().strftime('%Y-%m-%d')
    batidas = get_cached_batidas(today, today)
    
    total_f = Funcionario.query.filter_by(ativo=True).count()
    batidas_hoje_count = len(batidas)
    
    # Simple logic for dashboard metrics
    inconsistencias = sum(1 for b in batidas if b.get('Inconsistente', False))
    
    return render_template('index.html', 
                           total_funcionarios=total_f, 
                           batidas_hoje=batidas_hoje_count,
                           inconsistencias=inconsistencias,
                           batidas=batidas[:10], # Mostrar apenas as 10 mais recentes
                           last_sync=datetime.now().strftime('%H:%M'))

@app.route('/funcionarios')
def listar_funcionarios():
    # Filtrar apenas ativos para a listagem
    funcionarios = Funcionario.query.filter_by(ativo=True).all()

    # Preparar JSON para o modal
    import json
    funcionarios_dict = {}
    for f in funcionarios:
        funcionarios_dict[str(f.id)] = {
            'nome': f.nome or '',
            'cpf': f.cpf or '---',
            'rg': f.rg or '---',
            'pis': f.pis or '---',
            'email': f.email or '---',
            'celular': f.celular or '---',
            'telefone': f.telefone or '---',
            'endereco': f.endereco or '---',
            'bairro': f.bairro or '---',
            'cidade': f.cidade or '---',
            'uf': f.uf or '---',
            'cep': f.cep or '---',
            'departamento': f.departamento or '---',
            'funcao': f.funcao or '---',
            'admissao': f.admissao.strftime('%d/%m/%Y') if f.admissao else '---',
            'nascimento': f.nascimento.strftime('%d/%m/%Y') if f.nascimento else '---',
            'numero_folha': f.numero_folha or '---'
        }

    funcionarios_json = json.dumps(funcionarios_dict)
    return render_template('funcionarios.html', funcionarios=funcionarios, funcionarios_json=funcionarios_json)

@app.route('/sync')
def sync_data():
    """Sincronização via redirecionamento (clássico)."""
    success, message = perform_sync()
    flash(message, "success" if success else "danger")
    return redirect(url_for('listar_funcionarios'))

@app.route('/api/sync')
def api_sync_data():
    """Sincronização via API (AJAX)."""
    success, message = perform_sync()
    return {"success": success, "message": message}

@app.route('/sync-batidas')
def sync_batidas():
    """Sincronizar batidas de um período específico."""
    data_inicio = request.args.get('data_inicio', (date.today() - timedelta(days=30)).strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))

    success, message = perform_batidas_sync(data_inicio, data_fim)
    flash(message, "success" if success else "danger")
    return redirect(url_for('espelho'))

@app.route('/api/sync-batidas')
def api_sync_batidas():
    """Sincronização de batidas via API (AJAX)."""
    data_inicio = request.args.get('data_inicio', (date.today() - timedelta(days=30)).strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))

    success, message = perform_batidas_sync(data_inicio, data_fim)
    return {"success": success, "message": message}

def perform_batidas_sync(data_inicio, data_fim):
    """Sincroniza batidas do período e armazena no banco local."""
    api = get_api()
    batidas_api = api.buscar_batidas(data_inicio, data_fim)

    if not batidas_api:
        return False, "Nenhuma batida encontrada ou erro ao buscar dados da API."

    try:
        # Buscar funcionários ativos para validação
        funcionarios_ids = {f.id for f in Funcionario.query.filter_by(ativo=True).all()}

        new_count = 0
        updated_count = 0
        skipped_count = 0

        for item in batidas_api:
            func_id = str(item.get('FuncionarioId'))

            # Só sincronizar batidas de funcionários ativos no nosso banco
            if func_id not in funcionarios_ids:
                skipped_count += 1
                continue

            # Parsear data e hora
            data_batida = parse_date(item.get('Data'))
            hora_batida = item.get('Hora', '')

            if not data_batida or not hora_batida:
                continue

            # Verificar se batida já existe (mesmo funcionário, data e hora)
            batida_existente = Batida.query.filter_by(
                funcionario_id=func_id,
                data=data_batida,
                hora=hora_batida
            ).first()

            if batida_existente:
                updated_count += 1
                batida = batida_existente
            else:
                batida = Batida(
                    funcionario_id=func_id,
                    data=data_batida,
                    hora=hora_batida
                )
                db.session.add(batida)
                new_count += 1

            # Criar datetime combinando data e hora
            try:
                hora_parts = hora_batida.split(':')
                if len(hora_parts) >= 2:
                    batida.data_hora = datetime.combine(
                        data_batida,
                        datetime.strptime(f"{hora_parts[0]}:{hora_parts[1]}", '%H:%M').time()
                    )
            except:
                pass

            # Dados adicionais
            batida.tipo = item.get('Tipo')
            batida.origem = item.get('Origem')
            batida.inconsistente = item.get('Inconsistente', False)
            batida.justificativa = item.get('Justificativa')
            batida.latitude = item.get('Latitude')
            batida.longitude = item.get('Longitude')
            batida.data_sincronizacao = datetime.utcnow()

        db.session.commit()
        return True, f"Batidas sincronizadas! {new_count} novas, {updated_count} atualizadas, {skipped_count} ignoradas (funcionários inativos)."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao sincronizar batidas: {str(e)}"

def parse_date(date_str):
    """Converte string de data em objeto date, tratando vários formatos."""
    if not date_str:
        return None
    try:
        # Tenta vários formatos comuns
        for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d/%m/%Y']:
            try:
                return datetime.strptime(date_str.split('.')[0], fmt).date()
            except:
                continue
        return None
    except:
        return None

def perform_sync():
    """Lógica centralizada de sincronização otimizada com todos os campos."""
    api = get_api()
    data = api.listar_funcionarios()

    if not data:
        return False, "Erro ao sincronizar dados da API Secullum ou nenhum dado retornado."

    try:
        # Busca todos os IDs existentes
        existing_employees = {f.id: f for f in Funcionario.query.all()}

        # Marcar todos como inativos inicialmente
        for f in existing_employees.values():
            f.ativo = False

        new_count = 0
        updated_count = 0
        active_count = 0

        for item in data:
            f_id = str(item.get('Id'))
            if f_id in existing_employees:
                f = existing_employees[f_id]
                updated_count += 1
            else:
                f = Funcionario(id=f_id)
                db.session.add(f)
                new_count += 1

            # Dados básicos
            f.nome = item.get('Nome')

            # Documentos
            f.pis = item.get('NumeroPis') or item.get('Pis')
            f.cpf = item.get('Cpf')
            f.rg = item.get('Rg')
            f.carteira = item.get('Carteira')

            # Contatos
            f.email = item.get('Email')
            f.celular = item.get('Celular')
            f.telefone = item.get('Telefone')

            # Endereço
            f.endereco = item.get('Endereco')
            f.bairro = item.get('Bairro')
            f.cidade = item.get('Cidade')
            f.uf = item.get('Uf')
            f.cep = item.get('Cep')

            # Departamento pode vir como objeto ou string
            dept = item.get('NomeDepartamento')
            if not dept and item.get('Departamento'):
                dept_obj = item.get('Departamento')
                if isinstance(dept_obj, dict):
                    dept = dept_obj.get('Descricao') or dept_obj.get('Nome')
            f.departamento = dept

            # Função pode vir como objeto ou string
            funcao = item.get('NomeFuncao')
            if not funcao and item.get('Funcao'):
                funcao_obj = item.get('Funcao')
                if isinstance(funcao_obj, dict):
                    funcao = funcao_obj.get('Descricao') or funcao_obj.get('Nome')
            f.funcao = funcao

            f.numero_folha = item.get('NumeroFolha')
            f.numero_identificador = item.get('NumeroIdentificador')

            # Datas
            f.admissao = parse_date(item.get('Admissao'))
            f.demissao = parse_date(item.get('Demissao'))
            f.nascimento = parse_date(item.get('Nascimento'))

            # Status
            f.ativo = item.get('Demissao') is None
            f.data_ultima_sincronizacao = datetime.utcnow()

            if f.ativo:
                active_count += 1

        db.session.commit()
        return True, f"Sincronização concluída! {active_count} ativos, {new_count} novos, {updated_count} atualizados."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro no banco de dados: {str(e)}"

@app.route('/relatorios')
def relatorios():
    data_inicio = request.args.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))

    # Converter strings para objetos date
    data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
    data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()

    # Buscar batidas do banco local
    batidas_query = Batida.query.filter(
        Batida.data >= data_inicio_obj,
        Batida.data <= data_fim_obj
    ).join(Funcionario).filter(Funcionario.ativo == True).all()

    # Estatísticas gerais
    total_batidas = len(batidas_query)
    funcionarios_unicos = len(set(b.funcionario_id for b in batidas_query))
    inconsistencias = sum(1 for b in batidas_query if b.inconsistente)

    # Agrupar por departamento
    por_departamento = {}
    for b in batidas_query:
        dept = b.funcionario.departamento or 'Sem Departamento'
        por_departamento[dept] = por_departamento.get(dept, 0) + 1

    # Ranking de funcionários com mais batidas
    por_funcionario = {}
    for b in batidas_query:
        fid = b.funcionario_id
        nome = b.funcionario.nome
        if fid:
            if fid not in por_funcionario:
                por_funcionario[fid] = {'nome': nome, 'batidas': 0}
            por_funcionario[fid]['batidas'] += 1

    ranking = sorted(por_funcionario.values(), key=lambda x: x['batidas'], reverse=True)[:10]

    return render_template('relatorios.html',
                           data_inicio=data_inicio,
                           data_fim=data_fim,
                           total_batidas=total_batidas,
                           funcionarios_unicos=funcionarios_unicos,
                           inconsistencias=inconsistencias,
                           por_departamento=por_departamento,
                           ranking=ranking)

@app.route('/espelho')
def espelho():
    data_inicio = request.args.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
    export = request.args.get('export', 'false') == 'true'

    # Converter strings para objetos date
    data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
    data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()

    # Buscar batidas do banco local
    batidas_query = Batida.query.filter(
        Batida.data >= data_inicio_obj,
        Batida.data <= data_fim_obj
    ).join(Funcionario).filter(Funcionario.ativo == True).order_by(Batida.data.desc(), Batida.hora).all()

    # Agrupar batidas por funcionário e data
    agrupado = {}
    for b in batidas_query:
        key = (b.data.strftime('%Y-%m-%d'), b.funcionario_id, b.funcionario.nome)
        if key not in agrupado:
            agrupado[key] = []
        agrupado[key].append(b.hora)

    batidas_agrupadas = []
    for (d, fid, nome), horas in agrupado.items():
        batidas_agrupadas.append({
            'data': d,
            'funcionario_id': fid,
            'funcionario': nome,
            'horas': sorted(horas)
        })

    # Ordenar por data decrescente
    batidas_agrupadas.sort(key=lambda x: x['data'], reverse=True)

    if export:
        # Preparar dados para export
        export_data = []
        for b in batidas_query:
            export_data.append({
                'Data': b.data.strftime('%Y-%m-%d'),
                'Hora': b.hora,
                'Funcionário': b.funcionario.nome,
                'CPF': b.funcionario.cpf,
                'Departamento': b.funcionario.departamento,
                'Função': b.funcionario.funcao,
                'Tipo': b.tipo,
                'Origem': b.origem,
                'Inconsistente': 'Sim' if b.inconsistente else 'Não'
            })

        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Batidas')
        output.seek(0)
        return send_file(output,
                         download_name=f"batidas_{data_inicio}_{data_fim}.xlsx",
                         as_attachment=True)

    return render_template('batidas.html',
                           batidas_agrupadas=batidas_agrupadas,
                           data_inicio=data_inicio,
                           data_fim=data_fim)

if __name__ == '__main__':
    app.run(debug=True, port=5010)
