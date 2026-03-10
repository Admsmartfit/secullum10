from datetime import date, timedelta
from flask import Blueprint, redirect, url_for, flash, request, jsonify
from flask_login import login_required
from services.sync_service import sync_funcionarios, sync_batidas, sync_horarios, sync_alocacoes, sync_batidas_incremental

api_sync_bp = Blueprint('api_sync', __name__)


@api_sync_bp.route('/sync')
@login_required
def sync_func():
    success, message = sync_funcionarios()
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('funcionarios.funcionarios'))


@api_sync_bp.route('/api/sync')
@login_required
def api_sync_func():
    success, message = sync_funcionarios()
    return jsonify({'success': success, 'message': message})


@api_sync_bp.route('/sync-batidas')
@login_required
def sync_bat():
    if 'data_inicio' not in request.args:
        success, message = sync_batidas_incremental()
    else:
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
        success, message = sync_batidas(data_inicio, data_fim)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('espelho.espelho'))


@api_sync_bp.route('/api/sync-batidas')
@login_required
def api_sync_bat():
    if 'data_inicio' not in request.args:
        success, message = sync_batidas_incremental()
    else:
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
        success, message = sync_batidas(data_inicio, data_fim)
    return jsonify({'success': success, 'message': message})


# Redirects de compatibilidade (URLs antigas → novas)
@api_sync_bp.route('/funcionarios')
@api_sync_bp.route('/funcionarios/<path:rest>')
def redirect_funcionarios(rest=''):
    target = url_for('funcionarios.funcionarios') if not rest else f'/config/funcionarios/{rest}'
    return redirect(target, 301)


@api_sync_bp.route('/espelho')
@api_sync_bp.route('/espelho/<path:rest>')
def redirect_espelho(rest=''):
    from flask import request as _req
    target = url_for('espelho.espelho', **_req.args) if not rest else f'/config/espelho/{rest}'
    return redirect(target, 301)


@api_sync_bp.route('/sync-horarios')
@login_required
def sync_horarios_route():
    """Sincroniza horários e gera alocações para os próximos 60 dias."""
    ok_h, msg_h = sync_horarios()
    data_ini = date.today().strftime('%Y-%m-%d')
    data_fim = (date.today() + timedelta(days=60)).strftime('%Y-%m-%d')
    ok_a, msg_a = sync_alocacoes(data_ini, data_fim)
    flash(f'Horários: {msg_h} | Alocações: {msg_a}', 'success' if ok_h and ok_a else 'warning')
    return redirect(url_for('escalas.index'))


@api_sync_bp.route('/api/sync-horarios')
@login_required
def api_sync_horarios():
    ok_h, msg_h = sync_horarios()
    data_ini = date.today().strftime('%Y-%m-%d')
    data_fim = (date.today() + timedelta(days=60)).strftime('%Y-%m-%d')
    ok_a, msg_a = sync_alocacoes(data_ini, data_fim)
    return jsonify({'horarios': msg_h, 'alocacoes': msg_a, 'success': ok_h and ok_a})
