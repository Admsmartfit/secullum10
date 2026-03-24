from datetime import date
from flask import Blueprint, redirect, url_for, flash, request, jsonify
from flask_login import login_required
from services.sync_service import sync_funcionarios, sync_batidas, sync_batidas_incremental

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


@api_sync_bp.route('/batidas/')
@api_sync_bp.route('/batidas/<path:rest>')
def redirect_batidas(rest=''):
    from flask import request as _req
    target = url_for('espelho.espelho', **_req.args) if not rest else f'/config/espelho'
    return redirect(target, 301)


