import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from extensions import db
from models import Funcionario, Batida, Configuracao

_TZ_BR = ZoneInfo('America/Sao_Paulo')


_CHAVE_ULTIMA_SYNC = 'ultima_sync_batidas'


def get_ultima_sync_batidas() -> datetime | None:
    """Retorna o datetime da última sincronização de batidas (armazenado em Configuracao)."""
    cfg = Configuracao.query.filter_by(chave=_CHAVE_ULTIMA_SYNC).first()
    if not cfg or not cfg.valor:
        return None
    try:
        dt = datetime.fromisoformat(cfg.valor)
        # Blinda contra datas antigas sem fuso (offset-naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ_BR)
        return dt
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
    from services.config_service import get_secullum_api
    return get_secullum_api()


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

        db.session.commit()
        return True, f"Sync OK! {active_count} ativos, {new_count} novos, {updated_count} atualizados."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro no banco de dados: {str(e)}"


def sync_batidas(data_inicio, data_fim, hora_inicio=None, hora_fim=None):
    api = get_api()
    agora_sync = datetime.now(_TZ_BR)
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
    agora = datetime.now(_TZ_BR)
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
    """Sincroniza horários da API Secullum → cria UM Turno por horário usando dias complexos,
    e já aplica como 'Horário Base' dos funcionários vinculados."""
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

        complex_data = {}
        dias_list = []
        primeira_entrada = "00:00"
        primeira_saida = "00:00"

        for d in dias_raw:
            dia = d.get('DiaSemana')
            entrada = (d.get('Entrada1') or '').strip()
            saida = (d.get('Saida2') or d.get('Saida1') or '').strip()
            tipo = d.get('TipoDia', 0)  # 0=Normal, 1=Extra, 2=Folga

            if dia is not None and entrada and saida and tipo != 2:
                dias_list.append(str(dia))
                complex_data[str(dia)] = {
                    "inicio": entrada,
                    "fim": saida,
                    "intervalo": 60
                }
                if primeira_entrada == "00:00":
                    primeira_entrada = entrada
                    primeira_saida = saida

        existing = HorarioSecullum.query.get(numero)
        if existing:
            existing.descricao = descricao
            existing.dias_json = json.dumps(complex_data)
            existing.sincronizado_em = datetime.utcnow()
        else:
            db.session.add(HorarioSecullum(
                numero=numero,
                descricao=descricao,
                dias_json=json.dumps(complex_data),
            ))

        nome_turno = f"{descricao} [Secullum]"
        turno = Turno.query.filter_by(nome=nome_turno).first()

        try:
            h_ini = datetime.strptime(primeira_entrada, '%H:%M').time() if primeira_entrada != "00:00" else datetime.strptime('08:00', '%H:%M').time()
            h_fim = datetime.strptime(primeira_saida, '%H:%M').time() if primeira_saida != "00:00" else datetime.strptime('17:00', '%H:%M').time()
        except ValueError:
            continue

        if not turno:
            turno = Turno(
                nome=nome_turno,
                hora_inicio=h_ini,
                hora_fim=h_fim,
                intervalo_minutos=60
            )
            db.session.add(turno)
            criados += 1
        else:
            turno.hora_inicio = h_ini
            turno.hora_fim = h_fim
            atualizados += 1

        turno.dias_semana = ','.join(dias_list) if dias_list else ''
        turno.dias_complexos_json = json.dumps(complex_data)

        db.session.flush()  # Garante turno.id disponível antes do update

        Funcionario.query.filter_by(horario_secullum_numero=numero).update(
            {'horario_base_id': turno.id}
        )

    try:
        db.session.commit()
        return True, f"Horários: {criados} turnos criados, {atualizados} atualizados e vinculados aos Contratos."
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao salvar horários: {str(e)}"


def sync_alocacoes(_data_inicio_str: str, _data_fim_str: str):
    """
    Função reformulada.
    No novo modelo, a sincronização do Secullum atualiza o Horário Base (Contrato),
    pelo que não geramos mais Alocações Diárias de forma forçada.
    Esta função agora apenas limpa as alocações antigas bloqueantes para libertar a agenda do RH.
    """
    from models import AlocacaoDiaria, Turno
    try:
        turnos_sync = Turno.query.filter(
            db.or_(Turno.nome.like('%[Secullum]%'), Turno.nome.like('%[Sincronizado]%'))
        ).all()
        t_ids = [t.id for t in turnos_sync]

        if t_ids:
            AlocacaoDiaria.query.filter(AlocacaoDiaria.turno_id.in_(t_ids)).delete(synchronize_session=False)
            db.session.commit()
    except Exception:
        db.session.rollback()

    return True, "Escalas destravadas: Os horários da API estão agora aplicados na base do contrato."
