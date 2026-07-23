"""
Migração PRD Antiban WhatsApp — Fase 0 (observabilidade mínima) + Fase 1 (fila unificada).

  whatsapp_logs:
    - mega_message_id VARCHAR(100)  — id retornado pela Mega-API no envio (síncrono)
    - atualizado_em   TIMESTAMP

  megaapi_instance_events (nova):
    - eventos de conexão/desconexão da instância, capturados no webhook existente

  fila_envio_whatsapp (nova, substitui notificacao_fila como camada única de envio):
    - schema estendido de notificacao_fila (tipo_msg, interativo_json, anexo_ref,
      prioridade, primeiro_contato) + cópia dos dados de notificacao_fila

A tabela notificacao_fila NÃO é removida por este script — fica como rede de
segurança (é reversível apagar depois; não é reversível recriar dados perdidos).

Execute com:
    python migration_whatsapp_fase0_fase1.py
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
            "ALTER TABLE whatsapp_logs "
            "ADD COLUMN IF NOT EXISTS mega_message_id VARCHAR(100), "
            "ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMP"
        ))
        print("  ✓ whatsapp_logs.mega_message_id / atualizado_em")

        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS megaapi_instance_events (
                id          SERIAL PRIMARY KEY,
                tipo_evento VARCHAR(50),
                payload_raw TEXT,
                criado_em   TIMESTAMP DEFAULT NOW()
            )
        """))
        print("  ✓ megaapi_instance_events criada")

        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS fila_envio_whatsapp (
                id               SERIAL PRIMARY KEY,
                regra_id         INTEGER REFERENCES notification_rules(id) ON DELETE SET NULL,
                funcionario_id   VARCHAR(50) REFERENCES funcionarios(id) ON DELETE SET NULL,
                celular          VARCHAR(20) NOT NULL,
                mensagem         TEXT NOT NULL,
                tipo             VARCHAR(50),
                tipo_regra       VARCHAR(50),
                tipo_msg         VARCHAR(20) DEFAULT 'texto',
                interativo_json  TEXT,
                anexo_ref        VARCHAR(255),
                data_referencia  DATE,
                prioridade       INTEGER DEFAULT 10,
                primeiro_contato BOOLEAN DEFAULT FALSE,
                enviar_apos      TIMESTAMP,
                status           VARCHAR(20) DEFAULT 'pendente',
                tentativas       INTEGER DEFAULT 0,
                criada_em        TIMESTAMP DEFAULT NOW(),
                enviado_em       TIMESTAMP
            )
        """))
        print("  ✓ fila_envio_whatsapp criada")

        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS idx_fila_envio_status_hora "
            "ON fila_envio_whatsapp (status, enviar_apos)"
        ))
        print("  ✓ índice idx_fila_envio_status_hora criado")

        result = conn.execute(db.text("""
            INSERT INTO fila_envio_whatsapp
                (regra_id, funcionario_id, celular, mensagem, tipo, tipo_regra,
                 tipo_msg, prioridade, data_referencia, enviar_apos, status,
                 tentativas, criada_em, enviado_em)
            SELECT
                regra_id, funcionario_id, celular, mensagem, tipo, tipo_regra,
                'texto', 10, data_referencia, enviar_apos, status,
                tentativas, criada_em, enviado_em
            FROM notificacao_fila
            WHERE NOT EXISTS (
                SELECT 1 FROM fila_envio_whatsapp fw
                WHERE fw.celular = notificacao_fila.celular
                  AND fw.mensagem = notificacao_fila.mensagem
                  AND fw.criada_em = notificacao_fila.criada_em
            )
        """))
        print(f"  ✓ {result.rowcount} linha(s) copiada(s) de notificacao_fila para fila_envio_whatsapp")

        trans.commit()
        print("\nMigração concluída. notificacao_fila mantida intacta (não removida).")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
