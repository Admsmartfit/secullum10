from datetime import date, datetime, timedelta
from extensions import db
from models import NotificationRule, UnidadeLider
from services.notification_processor import (
    _gerar_relatorio_inconsistencias,
    _gerar_relatorio_por_departamento,
    _normalizar_celular,
)
from services.whatsapp_bot import enviar_texto
import logging

logger = logging.getLogger(__name__)

def disparar_relatorio_inconsistencias(data_ref: date) -> int:
    """
    Gera e envia relatórios de inconsistências para líderes e gestores.
    Retorna o total de mensagens enviadas com sucesso.
    """
    from services.config_service import get_gestor_celular

    total_env = 0
    ontem_str = data_ref.strftime('%d/%m/%Y')
    detalhes = []

    try:
        relatorio_global = _gerar_relatorio_inconsistencias(data_ref)
    except Exception as e:
        logger.error(f'[report_service] Erro ao gerar relatório global: {e}')
        relatorio_global = f'📋 Inconsistências — {ontem_str}\n\n⚠️ Erro ao gerar relatório.'

    try:
        relatorios_dept = _gerar_relatorio_por_departamento(data_ref)
    except Exception as e:
        logger.error(f'[report_service] Erro ao gerar relatório por departamento: {e}')
        relatorios_dept = {}

    # 1. Envia para cada líder de departamento (SEMPRE que tiver celular cadastrado)
    try:
        unidades = UnidadeLider.query.filter(UnidadeLider.celular_lider.isnot(None)).all()
        for u in unidades:
            if not u.celular_lider:
                continue
            dept = u.departamento
            cel = _normalizar_celular(u.celular_lider)
            texto_dept = relatorios_dept.get(dept) or (
                f'📋 Inconsistências — {dept} — {ontem_str}\n\n✅ Nenhuma inconsistência encontrada.'
            )
            try:
                if enviar_texto(celular=cel, mensagem=texto_dept, tipo='relatorio'):
                    total_env += 1
                    detalhes.append(f'líder "{dept}" ({cel})')
            except Exception as e_env:
                logger.error(f'[report_service] Falha envio líder "{dept}" ({cel}): {e_env}')
    except Exception as e:
        logger.error(f'[report_service] Erro ao enviar para líderes: {e}')

    # 2. Envia sempre para o gestor principal (se configurado), independente de NotificationRule
    try:
        cel_gestor = get_gestor_celular()
        if cel_gestor:
            cel_gestor_norm = _normalizar_celular(cel_gestor)
            try:
                if enviar_texto(celular=cel_gestor_norm, mensagem=relatorio_global, tipo='relatorio'):
                    total_env += 1
                    detalhes.append(f'gestor ({cel_gestor_norm})')
            except Exception as e_env:
                logger.error(f'[report_service] Falha envio gestor ({cel_gestor_norm}): {e_env}')
        else:
            logger.warning('[report_service] Gestor principal sem celular configurado — relatório não enviado ao gestor.')
    except Exception as e:
        logger.error(f'[report_service] Erro ao obter celular do gestor: {e}')

    # 3. Atualiza estatísticas das regras INCONSISTENCY_REPORT (mantém compatibilidade)
    try:
        regras = NotificationRule.query.filter_by(ativo=True, condition_type='INCONSISTENCY_REPORT').all()
        for regra in regras:
            regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + max(total_env, 1)
            regra.ultima_execucao = datetime.utcnow()
        if regras:
            db.session.commit()
    except Exception as e:
        logger.error(f'[report_service] Erro ao atualizar estatísticas de regras: {e}')

    logger.info(f'[report_service] {total_env} mensagem(ns) enviada(s): {", ".join(detalhes) or "nenhum destinatário configurado"}')
    return total_env
