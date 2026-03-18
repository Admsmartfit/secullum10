import pandas as pd
from io import BytesIO
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, send_file, jsonify, flash, redirect, url_for
from flask_login import login_required
from extensions import db
from models import Batida, Funcionario, AlocacaoDiaria, Turno

espelho_bp = Blueprint('espelho', __name__, url_prefix='/config')


@espelho_bp.route('/espelho')
@login_required
def espelho():
    data_inicio_str = request.args.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
    export = request.args.get('export', 'false') == 'true'
    funcionario_id = request.args.get('funcionario_id', '').strip()

    data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
    data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()

    q = (
        Batida.query
        .filter(Batida.data >= data_inicio, Batida.data <= data_fim)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
    )
    if funcionario_id:
        q = q.filter(Batida.funcionario_id == funcionario_id)

    batidas_query = q.order_by(Batida.data.desc(), Batida.hora).all()

    funcionario_selecionado = Funcionario.query.get(funcionario_id) if funcionario_id else None
    todos_func = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()

    if export:
        rows = [{
            'Data': b.data.strftime('%Y-%m-%d'),
            'Hora': b.hora,
            'Funcionario': b.funcionario.nome,
            'CPF': b.funcionario.cpf,
            'Departamento': b.funcionario.departamento,
            'Funcao': b.funcionario.funcao,
            'Tipo': b.tipo,
            'Origem': b.origem,
            'Inconsistente': 'Sim' if b.inconsistente else 'Nao',
        } for b in batidas_query]

        df = pd.DataFrame(rows)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Batidas')
        output.seek(0)
        return send_file(
            output,
            download_name=f'batidas_{data_inicio_str}_{data_fim_str}.xlsx',
            as_attachment=True,
        )

    agrupado = {}
    for b in batidas_query:
        key = (b.data.strftime('%Y-%m-%d'), b.funcionario_id, b.funcionario.nome)
        agrupado.setdefault(key, []).append(b.hora)

    batidas_agrupadas = []
    for (d_str, fid, nome), horas in agrupado.items():
        horas_ordenadas = sorted(horas)
        data_obj = datetime.strptime(d_str, '%Y-%m-%d').date()

        func = Funcionario.query.get(fid)
        aloc = AlocacaoDiaria.query.filter_by(funcionario_id=fid, data=data_obj).first()
        turno = db.session.get(Turno, aloc.turno_id) if aloc else (func.horario_base if func else None)

        status_lista = []

        if turno and horas_ordenadas:
            h_ini, h_fim, _ = turno.get_horario_dia(data_obj.weekday())
            if h_ini and h_fim:
                dt_ini = datetime.combine(data_obj, h_ini)
                dt_fim = datetime.combine(data_obj, h_fim)
                if h_fim < h_ini:            # turno vira a madrugada
                    dt_fim += timedelta(days=1)

                # 1. Atraso na entrada (> 10 min)
                dt_entrada = datetime.combine(data_obj, horas_ordenadas[0])
                if (dt_entrada - dt_ini).total_seconds() > 600:
                    status_lista.append('Atrasado')

                # 2. Saída tardia (> 10 min além do fim do turno)
                dt_saida = datetime.combine(data_obj, horas_ordenadas[-1])
                if horas_ordenadas[-1] < h_ini:   # batida após meia-noite
                    dt_saida += timedelta(days=1)
                if (dt_saida - dt_fim).total_seconds() > 600:
                    status_lista.append('+10m Saída')

                # 3. Intervalo fora da faixa 50–70 min (requer ≥ 4 batidas)
                if len(horas_ordenadas) >= 4:
                    dt_saida_int = datetime.combine(data_obj, horas_ordenadas[1])
                    dt_retorno_int = datetime.combine(data_obj, horas_ordenadas[2])
                    if horas_ordenadas[2] < horas_ordenadas[1]:
                        dt_retorno_int += timedelta(days=1)
                    intervalo_min = (dt_retorno_int - dt_saida_int).total_seconds() / 60
                    if intervalo_min < 50 or intervalo_min > 70:
                        status_lista.append('Intervalo Insuficiente')

        batidas_agrupadas.append({
            'data': d_str,
            'funcionario_id': fid,
            'funcionario': nome,
            'horas': horas_ordenadas,
            'status': status_lista,
        })

    batidas_agrupadas = sorted(batidas_agrupadas, key=lambda x: x['data'], reverse=True)

    funcionarios_com_batida = sorted(
        {b['funcionario_id']: b['funcionario'] for b in batidas_agrupadas}.items(),
        key=lambda x: x[1],
    )

    return render_template(
        'batidas.html',
        batidas_agrupadas=batidas_agrupadas,
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        funcionarios_com_batida=funcionarios_com_batida,
        funcionario_id=funcionario_id,
        funcionario_selecionado=funcionario_selecionado,
        todos_func=todos_func,
    )


def _batidas_de_func(func_id: str, data_inicio, data_fim) -> list:
    """Retorna batidas agrupadas por dia para um único funcionário."""
    batidas_query = (
        Batida.query
        .filter(
            Batida.funcionario_id == func_id,
            Batida.data >= data_inicio,
            Batida.data <= data_fim,
        )
        .order_by(Batida.data, Batida.hora)
        .all()
    )
    agrupado = {}
    for b in batidas_query:
        agrupado.setdefault(b.data.strftime('%Y-%m-%d'), []).append(b.hora)
    return [{'data': d, 'horas': sorted(h)} for d, h in sorted(agrupado.items())]


@espelho_bp.route('/espelho/pdf')
@login_required
def espelho_pdf():
    """RF4.4 – Gera e baixa PDF do espelho de um funcionário."""
    func_id = request.args.get('funcionario_id')
    data_inicio_str = request.args.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))

    func = Funcionario.query.get_or_404(func_id)
    d_ini = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
    d_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
    batidas = _batidas_de_func(func_id, d_ini, d_fim)

    from services.pdf_service import gerar_espelho_pdf
    buf = gerar_espelho_pdf(func, batidas, d_ini, d_fim)
    fname = f'espelho_{func.nome.replace(" ", "_")}_{data_inicio_str}.pdf'
    return send_file(buf, as_attachment=True, download_name=fname, mimetype='application/pdf')


@espelho_bp.route('/espelho/enviar-whatsapp', methods=['POST'])
@login_required
def espelho_enviar_whatsapp():
    """RF4.4 – Gera PDF do espelho e envia ao funcionário via WhatsApp."""
    func_id = request.form.get('funcionario_id')
    data_inicio_str = request.form.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str = request.form.get('data_fim', date.today().strftime('%Y-%m-%d'))

    func = Funcionario.query.get_or_404(func_id)
    if not func.celular:
        flash(f'{func.nome} não tem celular cadastrado.', 'danger')
        return redirect(url_for('espelho.espelho'))

    d_ini = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
    d_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
    batidas = _batidas_de_func(func_id, d_ini, d_fim)

    from services.pdf_service import gerar_espelho_pdf
    buf = gerar_espelho_pdf(func, batidas, d_ini, d_fim)
    pdf_bytes = buf.read()
    fname = f'espelho_{func.nome.replace(" ", "_")}_{data_inicio_str}.pdf'
    caption = f'Espelho de ponto – {data_inicio_str} a {data_fim_str}'

    from services.whatsapp_bot import enviar_documento
    ok = enviar_documento(celular=func.celular, pdf_bytes=pdf_bytes, filename=fname,
                          caption=caption, func_id=func.id, tipo='espelho')
    flash(
        f'Espelho enviado para {func.nome}!' if ok
        else 'Falha ao enviar (verifique MEGAAPI_TOKEN ou celular do funcionário).',
        'success' if ok else 'danger',
    )
    return redirect(url_for('espelho.espelho',
                            data_inicio=data_inicio_str, data_fim=data_fim_str))
