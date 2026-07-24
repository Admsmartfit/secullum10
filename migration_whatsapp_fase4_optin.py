"""
Migração PRD Antiban WhatsApp — Fase 4 (opt-in conversacional).

  notification_rules:
    - requer_optin        BOOLEAN DEFAULT TRUE   — pergunta antes de enviar o conteúdo
    - optin_janela_horas  INTEGER DEFAULT 24     — prazo para resposta antes do fallback
    - optin_fallback      VARCHAR(20) DEFAULT 'enviar'  — enviar/reenviar_pergunta/cancelar

Decisão de negócio (PRD, seção Fase 4): default TRUE para todas as regras,
inclusive as já existentes — nenhum condition_type atual é "resposta direta
a uma ação que o funcionário acabou de fazer", então nenhuma correção manual
é necessária após esta migração.

Execute com:
    python migration_whatsapp_fase4_optin.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from extensions import db

app = create_app()

with app.app_context():
    conn = db.engine.connect()
    trans = conn.begin()
    try:
        conn.execute(db.text(
            "ALTER TABLE notification_rules "
            "ADD COLUMN IF NOT EXISTS requer_optin BOOLEAN DEFAULT TRUE, "
            "ADD COLUMN IF NOT EXISTS optin_janela_horas INTEGER DEFAULT 24, "
            "ADD COLUMN IF NOT EXISTS optin_fallback VARCHAR(20) DEFAULT 'enviar'"
        ))
        print("  ✓ notification_rules.requer_optin / optin_janela_horas / optin_fallback")
        trans.commit()
        print("\nMigração concluída.")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
