import json
from datetime import datetime, date, timedelta
from extensions import db
from models import Funcionario, Batida, Configuracao
from secullum_api import SecullumAPI
import os

_CHAVE_ULTIMA_SYNC = 'ultima_sync_batidas'


def get_ultima_sync_batidas() -> datetime | None:
    """Retorna o datetime da última sincronização de batidas (armazenado em Configuracao)."""
    cfg = Configuracao.query.filter_by(chave=_CHAVE_ULTIMA_SYNC).first()
    if not cfg or not cfg.valor:
        return None
    try:
        return datetime.fromisoformat(cfg.valor)
    except ValueError:
        return None


def set_ultima_sync_batidas(dt: datetime):
    """Persiste o datetime da sincronização de batidas."""
    cfg = Configuracao.query.filter_by(chave=_CHAVE_ULTIMA_SYNC).first()
    if cfg:
        cfg.valor = dt.isoformat()
    else:
        db.session.add(Configuracao(chave=_CHAVE_ULTIMA_SYNC, valor=dt.isoformat()))
    db.session.commit()


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

            # Horário Secullum
            f.horario_secullum_numero = item.get('HorarioNumero')
            horario_obj = item.get('Horario') or {}
            if isinstance(horario_obj, dict):
                f.horario_secullum_nome = horario_obj.get('Descricao') or item.get('NomeHorario')
            else:
                f.horario_secullum_nome = item.get('NomeHorario')
            f.data_ultima_sincronizacao = datetime.utcnow()

            if f.ativo:
                active_count += 1
                # Auto-alocação baseada no horário da Secullum
                if f.horario_secullum_numero:
                    # Tenta encontrar um turno local que corresponda a este horário
                    # No sync_horarios garantimos que turnos são criados com o nome do horário
                    from models import HorarioSecullum, AlocacaoDiaria, Turno
                    # Sincroniza alocação para hoje e amanhã como exemplo rápido
                    hoje = date.today()
                    for d in [hoje, hoje + timedelta(days=1)]:
                        hs = HorarioSecullum.query.get(f.horario_secullum_numero)
                        if hs:
                            dias_dict = json.loads(hs.dias_json)
                            dia_str = str(d.weekday())
                            info = dias_dict.get(dia_str)
                            if info and info.get('entrada') and info.get('tipo') != 2:
                                t = Turno.query.filter_by(hora_inicio=datetime.strptime(info['entrada'], '%H:%M').time(), 
                                                       hora_fim=datetime.strptime(info['saida'], '%H:%M').time()).first()
                                if t:
                                    aloc = AlocacaoDiaria.query.filter_by(funcionario_id=f.id, data=d).first()
                                    if not aloc:
                                        db.session.add(AlocacaoDiaria(funcionario_id=f.id, turno_id=t.id, data=d))

        db.session.commit()
        return True, f"Sync OK! {active_count} ativos, {new_count} novos, {updated_count} atualizados."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro no banco de dados: {str(e)}"


def sync_batidas(data_inicio, data_fim, hora_inicio=None, hora_fim=None):
    api = get_api()
    agora_sync = datetime.now()
    registros = api.buscar_batidas(data_inicio, data_fim, hora_inicio, hora_fim)
    if registros is None:
        return False, "Erro ao buscar batidas da API."
    if not registros:
        # Ainda assim salvamos a última sync para não repetir o período vazio
        set_ultima_sync_batidas(agora_sync)
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
                    partes_hora = hora.split(':')
                    if len(partes_hora) < 2:
                        continue
                    hora = f'{partes_hora[0]}:{partes_hora[1]}'  # normaliza HH:MM:SS → HH:MM
                    if hora in ('00:00',):
                        continue  # campo vazio/zerado

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
        set_ultima_sync_batidas(agora_sync)
        return True, (f"Batidas sincronizadas! {new_count} novas, "
                      f"{updated_count} atualizadas, {skipped_count} ignoradas.")
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao sincronizar batidas: {str(e)}"


def sync_batidas_incremental():
    """Sincroniza batidas a partir da última sync registrada até agora.

    - Primeira vez: retrocede 7 dias como segurança.
    - Nas demais: usa o timestamp exato da última sync (menos 1h de buffer para
      cobrir batidas que possam ter chegado atrasadas na API Secullum).
    Salva o timestamp de início da requisição atual ao concluir com sucesso.
    """
    agora = datetime.now()
    ultima = get_ultima_sync_batidas()

    hora_inicio = None
    if ultima is None:
        data_inicio = (agora - timedelta(days=7)).strftime('%Y-%m-%d')
    else:
        # 1h de sobreposição para cobrir atrasos da API
        inicio_com_buffer = ultima - timedelta(hours=1)
        data_inicio = inicio_com_buffer.strftime('%Y-%m-%d')
        hora_inicio = inicio_com_buffer.strftime('%H:%M')

    data_fim = agora.strftime('%Y-%m-%d')
    hora_fim = agora.strftime('%H:%M')
    
    return sync_batidas(data_inicio, data_fim, hora_inicio, hora_fim)


def sync_horarios():
    """Sincroniza horários da API Secullum → HorarioSecullum + cria/atualiza Turnos.

    Cada HorarioDia vira um Turno (agrupando dias com mesmo par entrada/saída).
    """
    from models import HorarioSecullum, Turno

    api = get_api()
    horarios = api.listar_horarios()
    if horarios is None:
        return False, "Erro ao listar horários da API Secullum."
    if not horarios:
        return True, "Nenhum horário retornado pela API."

    criados = atualizados = 0

    for h in horarios:
        numero = h.get('Numero') or h.get('Id')
        if numero is None:
            continue
        descricao = (h.get('Descricao') or f'Horário {numero}').strip()
        dias_raw = h.get('Dias') or []

        # Build dias dict: dia_semana(str) → {entrada, saida, tipo}
        dias_dict = {}
        for d in dias_raw:
            dia = d.get('DiaSemana')
            entrada = (d.get('Entrada1') or '').strip()
            # Última saída preenchida (Saida2 → Saida1 fallback)
            saida = (d.get('Saida2') or d.get('Saida1') or '').strip()
            tipo = d.get('TipoDia', 0)  # 0=Normal, 1=Extra, 2=Folga
            if dia is not None:
                dias_dict[str(dia)] = {'entrada': entrada, 'saida': saida, 'tipo': tipo}

        existing = HorarioSecullum.query.get(numero)
        if existing:
            existing.descricao = descricao
            existing.dias_json = json.dumps(dias_dict)
            existing.sincronizado_em = datetime.utcnow()
            atualizados += 1
        else:
            db.session.add(HorarioSecullum(
                numero=numero,
                descricao=descricao,
                dias_json=json.dumps(dias_dict),
            ))
            criados += 1

        # Create/update Turnos for each unique (entrada, saida) pair
        # Group days by their hour pattern
        padrao_dias: dict[tuple, list] = {}
        for dia_str, info in dias_dict.items():
            if not info['entrada'] or not info['saida'] or info['tipo'] == 2:
                continue
            key = (info['entrada'], info['saida'])
            padrao_dias.setdefault(key, []).append(int(dia_str))

        for (entrada_str, saida_str), dias_list in padrao_dias.items():
            try:
                h_ini = datetime.strptime(entrada_str, '%H:%M').time()
                h_fim = datetime.strptime(saida_str, '%H:%M').time()
            except ValueError:
                continue

            turno = Turno.query.filter_by(hora_inicio=h_ini, hora_fim=h_fim).first()
            if not turno:
                turno = Turno(
                    nome=f"{descricao} ({entrada_str}-{saida_str})",
                    hora_inicio=h_ini,
                    hora_fim=h_fim,
                    dias_semana=','.join(map(str, sorted(dias_list))),
                    intervalo_minutos=60 # Padrão
                )
                db.session.add(turno)
            else:
                # Atualiza nome se for um turno importado e mescla dias
                if "Sincronizado" not in (turno.nome or ""):
                    turno.nome = f"{descricao} ({entrada_str}-{saida_str}) [Sincronizado]"
                
                existing_dias = turno.dias_semana_list
                merged = sorted(set(existing_dias) | set(dias_list))
                turno.dias_semana = ','.join(map(str, merged))

    try:
        db.session.commit()
        return True, f"Horários: {criados} criados, {atualizados} atualizados."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao salvar horários: {str(e)}"


def sync_alocacoes(data_inicio_str: str, data_fim_str: str):
    """Gera AlocacaoDiaria a partir do HorarioSecullum de cada funcionário.

    Para cada dia no intervalo, verifica se o funcionário tem turno naquele
    dia da semana segundo seu horário Secullum e cria/atualiza a alocação.
    """
    from models import HorarioSecullum, Turno, AlocacaoDiaria

    try:
        data_ini = date.fromisoformat(data_inicio_str)
        data_fim = date.fromisoformat(data_fim_str)
    except ValueError as e:
        return False, f"Datas inválidas: {e}"

    funcionarios = Funcionario.query.filter(
        Funcionario.ativo == True,
        Funcionario.horario_secullum_numero.isnot(None),
    ).all()

    if not funcionarios:
        return True, "Nenhum funcionário com horário Secullum vinculado."

    # Cache horarios e turnos para evitar N+1 queries
    horario_cache: dict[int, dict] = {}
    turno_cache: dict[tuple, int | None] = {}

    criadas = atualizadas = sem_turno = 0

    for func in funcionarios:
        num = func.horario_secullum_numero
        if num not in horario_cache:
            hs = HorarioSecullum.query.get(num)
            horario_cache[num] = json.loads(hs.dias_json) if hs and hs.dias_json else {}

        dias_dict = horario_cache[num]
        if not dias_dict:
            continue

        d = data_ini
        while d <= data_fim:
            dia_str = str(d.weekday())  # 0=Segunda … 6=Domingo
            info = dias_dict.get(dia_str)
            if not info or not info.get('entrada') or info.get('tipo') == 2:
                d += timedelta(days=1)
                continue

            entrada_str = info['entrada']
            saida_str = info['saida']
            cache_key = (entrada_str, saida_str)

            if cache_key not in turno_cache:
                try:
                    h_ini = datetime.strptime(entrada_str, '%H:%M').time()
                    h_fim = datetime.strptime(saida_str, '%H:%M').time()
                    t = Turno.query.filter_by(hora_inicio=h_ini, hora_fim=h_fim).first()
                    turno_cache[cache_key] = t.id if t else None
                except Exception:
                    turno_cache[cache_key] = None

            turno_id = turno_cache[cache_key]
            if not turno_id:
                sem_turno += 1
                d += timedelta(days=1)
                continue

            existing = AlocacaoDiaria.query.filter_by(
                funcionario_id=func.id, data=d
            ).first()
            if existing:
                existing.turno_id = turno_id
                atualizadas += 1
            else:
                db.session.add(AlocacaoDiaria(
                    funcionario_id=func.id,
                    turno_id=turno_id,
                    data=d,
                ))
                criadas += 1

            d += timedelta(days=1)

    try:
        db.session.commit()
        return True, (f"Alocações: {criadas} criadas, {atualizadas} atualizadas"
                      + (f", {sem_turno} sem turno" if sem_turno else "") + ".")
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao salvar alocações: {str(e)}"
