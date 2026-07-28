"""
Teste manual do PRD Antiban WhatsApp (Fases 1-6).
Segue o estilo de test_batidas.py/test_api.py (script standalone, não pytest),
mas NUNCA chama a Evolution API real: usa SQLite em memória e credenciais em
branco, então toda tentativa de envio cai em status 'sem_config'.

Execute com:
    python test_envio_dispatcher.py
"""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['EVOLUTION_HOST'] = ''
os.environ['EVOLUTION_INSTANCE'] = ''
os.environ['EVOLUTION_API_KEY'] = ''

from datetime import datetime, timedelta


def test_spintax():
    from services.spintax import resolver_spintax
    texto = '{Olá|Oi|E aí}, tudo bem?'
    resultados = {resolver_spintax(texto) for _ in range(30)}
    assert resultados <= {'Olá, tudo bem?', 'Oi, tudo bem?', 'E aí, tudo bem?'}
    assert len(resultados) > 1, 'Spintax deveria variar entre chamadas repetidas'
    print(f'  ✓ Spintax variou em {len(resultados)} formas diferentes: {resultados}')


def test_lint_primeiro_contato():
    from services.lint_template import validar_template_primeiro_contato
    palavras = ['promoção', 'grátis', 'clique aqui']
    assert validar_template_primeiro_contato('Olá! Confira https://exemplo.com', palavras)
    assert validar_template_primeiro_contato('Aproveite essa PROMOÇÃO incrível', palavras)
    assert not validar_template_primeiro_contato('Olá, tudo bem?', palavras)
    print('  ✓ Lint bloqueia link e palavra-gatilho, libera texto neutro')


def _setup_app():
    from app import create_app
    app = create_app()
    return app


def test_dispatcher_rate_limit_e_opt_in():
    app = _setup_app()
    with app.app_context():
        from extensions import db
        from models import Funcionario, FilaEnvioWhatsapp, NotificationRule, WhatsappLog
        from services.whatsapp_bot import enviar_texto, _fone
        from services.envio_dispatcher import processar_proximo
        from services.config_service import set_setting

        set_setting('whatsapp_delay_min_s', '0')
        set_setting('whatsapp_delay_max_jitter_s', '0')
        set_setting('whatsapp_delay_por_caractere_s', '0')
        set_setting('whatsapp_delay_jitter_digitacao_s', '0')
        set_setting('whatsapp_delay_extra_min_s', '0')

        func = Funcionario(id='t1', nome='Fulano de Teste', celular='27900000001', ativo=True)
        db.session.add(func)
        db.session.commit()

        # ── Rate-limit básico: sem regra (sem opt-in), enfileira e despacha ──
        enviar_texto('27900000001', 'Mensagem simples de teste', func_id='t1', tipo='teste')
        r = processar_proximo()
        assert r.get('enviado') is False  # sem_config -> _despachar_real retorna False
        log = WhatsappLog.query.order_by(WhatsappLog.id.desc()).first()
        assert log.status == 'sem_config'
        print('  ✓ Item sem regra despachado normalmente (sem_config, como esperado sem credenciais)')

        # ── Opt-in: regra com requer_optin=True intercepta o despacho ──
        regra = NotificationRule(nome='Regra de Teste', requer_optin=True, optin_janela_horas=24, optin_fallback='enviar')
        db.session.add(regra)
        db.session.commit()

        item = FilaEnvioWhatsapp(
            regra_id=regra.id, funcionario_id='t1', celular=_fone(func.celular),
            mensagem='Conteúdo real da notificação', tipo='regra', tipo_msg='texto',
            status='pendente',
        )
        db.session.add(item)
        db.session.commit()

        r = processar_proximo()
        item = FilaEnvioWhatsapp.query.get(item.id)
        assert item.status == 'aguardando_optin', f'esperado aguardando_optin, veio {item.status}'
        print('  ✓ Item com requer_optin=True virou aguardando_optin (pergunta enviada) em vez de despachar o conteúdo real')

        # Simula resposta afirmativa do funcionário
        from blueprints.whatsapp import _processar_resposta_optin
        _processar_resposta_optin(func, {'fila_id': item.id}, 'sim')
        item = FilaEnvioWhatsapp.query.get(item.id)
        assert item.status == 'optin_confirmado'
        print('  ✓ Resposta afirmativa liberou o item (optin_confirmado)')

        r = processar_proximo()
        item = FilaEnvioWhatsapp.query.get(item.id)
        assert item.status in ('erro', 'pendente'), f'esperado erro/pendente pos-despacho, veio {item.status}'
        print(f'  ✓ Item optin_confirmado seguiu para despacho normal (status final: {item.status})')

        # ── Lint bloqueando primeiro contato ──
        set_setting('whatsapp_palavras_gatilho', 'promoção,grátis')
        item2 = FilaEnvioWhatsapp(
            funcionario_id='t1', celular='27900099999',
            mensagem='Aproveite essa PROMOÇÃO imperdível: https://exemplo.com',
            tipo='regra', tipo_msg='texto', primeiro_contato=True, status='pendente',
        )
        db.session.add(item2)
        db.session.commit()
        processar_proximo()
        item2 = FilaEnvioWhatsapp.query.get(item2.id)
        assert item2.status == 'bloqueado_lint', f'esperado bloqueado_lint, veio {item2.status}'
        print('  ✓ Mensagem de primeiro contato com link/gatilho foi bloqueada pelo dispatcher')


if __name__ == '__main__':
    print('Spintax:')
    test_spintax()
    print('\nLint de primeiro contato:')
    test_lint_primeiro_contato()
    print('\nDispatcher (rate-limit + opt-in + lint), sem nenhuma chamada real à Mega-API:')
    test_dispatcher_rate_limit_e_opt_in()
    print('\nTodos os testes passaram.')
