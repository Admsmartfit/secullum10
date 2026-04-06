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
    total_env = 0
    ontem_str = data_ref.strftime('%d/%m/%Y')

    try:
        relatorio_global = _gerar_relatorio_inconsistencias(data_ref)
        relatorios_dept  = _gerar_relatorio_por_departamento(data_ref)

        # 1. Envia para cada líder de departamento (SEMPRE)
        unidades = UnidadeLider.query.filter(UnidadeLider.celular_lider.isnot(None)).all()
        alvos = [
            {'celular': _normalizar_celular(u.celular_lider), 'dept': u.departamento}
            for u in unidades if u.celular_lider
        ]

        for alvo in alvos:
            dept = alvo['dept']
            texto_dept = relatorios_dept.get(dept) or (
                f'📋 Inconsistências — {dept} — {ontem_str}\n\n✅ Nenhuma inconsistência encontrada.'
            )
            try:
                if enviar_texto(celular=alvo['celular'], mensagem=texto_dept, tipo='relatorio'):
                    total_env += 1
            except Exception as e_env:
                logger.error(f'[report_service] Falha envio líder "{dept}" ({alvo["celular"]}): {e_env}')

        # 2. Envia para gestores (se houver regra ativa)
        regras = NotificationRule.query.filter_by(ativo=True, condition_type='INCONSISTENCY_REPORT').all()
        for regra in regras:
            if regra.dest_manager:
                from services.config_service import get_gestor_celular
                cel = get_gestor_celular()
                if cel:
                    try:
                        if enviar_texto(celular=_normalizar_celular(cel), mensagem=relatorio_global, tipo='relatorio'):
                            total_env += 1
                    except Exception as e_env:
                        logger.error(f'[report_service] Falha envio global: {e_env}')
            
            regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + 1
            regra.ultima_execucao = datetime.utcnow()
        
        if regras:
            db.session.commit()

    except Exception as e:
        logger.error(f'[report_service] Erro geral: {e}')
        raise e

    return total_env
