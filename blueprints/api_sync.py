from datetime import date, timedelta
from flask import Blueprint, redirect, url_for, flash, request, jsonify
from flask_login import login_required
from services.sync_service import sync_funcionarios, sync_batidas

api_sync_bp = Blueprint('api_sync', __name__)


@api_sync_bp.route('/sync')
@login_required
def sync_func():
    success, message = sync_funcionarios()
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('funcionarios.listar'))


@api_sync_bp.route('/api/sync')
@login_required
def api_sync_func():
    success, message = sync_funcionarios()
    return jsonify({'success': success, 'message': message})


@api_sync_bp.route('/sync-batidas')
@login_required
def sync_bat():
    data_inicio = request.args.get('data_inicio', (date.today() - timedelta(days=30)).strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
    success, message = sync_batidas(data_inicio, data_fim)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('espelho.espelho'))


@api_sync_bp.route('/api/sync-batidas')
@login_required
def api_sync_bat():
    data_inicio = request.args.get('data_inicio', (date.today() - timedelta(days=30)).strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
    success, message = sync_batidas(data_inicio, data_fim)
    return jsonify({'success': success, 'message': message})
