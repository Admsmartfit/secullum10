# PRD Técnico — Redução de Bloqueio/Denúncia (Spam) nos Envios de WhatsApp do secullum10

**Projeto:** secullum10
**Responsável pela implementação:** Antigravity (agente de desenvolvimento)
**Baseado em:** PRD de negócio "Redução de Bloqueio/Denúncia (Spam)" de 22/07/2026
**Escopo desta versão:** tradução do PRD de negócio em tarefas técnicas concretas, mapeadas ao código real do repositório `Admsmartfit/secullum10` (branch `main`)
**Status:** Pronto para implementação em etapas

---

## 0. Diagnóstico do código atual (linha de base)

Antes de detalhar as fases, este é o estado real encontrado no repositório, que serve de ponto de partida para o Antigravity:

| Componente | Arquivo | Situação atual |
|---|---|---|
| Cliente Mega-API | `services/whatsapp_bot.py` | `enviar_texto`, `enviar_botoes`, `enviar_menu_lista`, `enviar_documento` fazem `requests.post(...)` **síncrono e imediato**, sem delay, sem jitter, sem teto de envios/hora. (Presença "digitando" não é uma lacuna a corrigir — a Mega-API não expõe esse recurso, ver 0.1.) |
| Dispatcher único | `services/whatsapp_bot.py::enviar_msg` | Já existe um ponto único de entrada (bom — atende ao requisito de "camada única" do PRD de negócio), mas ele despacha direto para a Mega-API. |
| Fila existente | `models.py::NotificacaoFila` + `notification_processor.py::processar_fila_notificacoes` | Já existe uma fila, mas com propósito estreito: só guarda mensagens de regra que caíram **fora do expediente** (Direito à Desconexão), e o Celery beat despacha a fila inteira de uma vez (`processar-notificacoes-fila`, `crontab(minute=10)`), sem delay/jitter entre os itens. |
| Log de envio | `models.py::WhatsappLog` | Guarda `status` (`enviado`/`erro_*`/`sem_config`), `celular`, `tipo`, `tipo_regra`, `criado_em`. **Não guarda status de webhook** (`delivered`/`read`/`failed`) nem se é primeiro contato. |
| Webhook Mega-API | `blueprints/whatsapp.py::webhook()` | Já recebe eventos e valida HMAC (`X-Mega-Signature`). Hoje só trata mensagens **recebidas** (respostas de funcionários) para o fluxo de bot (`ChatState`), não trata eventos de **status de mensagem enviada** nem de **desconexão da instância**. |
| Agendamento | `app.py` (Celery `beat_schedule`) + `services/auto_sync.py` (APScheduler) | O projeto já usa **Celery + Redis** para tasks assíncronas e **APScheduler** rodando dentro do processo Flask para sync rápido. Ambas as infra existem e serão reaproveitadas — não é necessário introduzir nova tecnologia de fila. |
| Configuração dinâmica | `services/config_service.py::get_setting` | Padrão já existente: lê da tabela `Configuracao` com fallback para variável de ambiente. **Todos os novos parâmetros deste PRD devem seguir esse padrão**, não hardcode. |
| Pontos de disparo | `blueprints/whatsapp.py`, `notification_processor.py`, `avaliacao_service.py`, `report_service.py`, `blueprints/marketplace.py`, `blueprints/espelho.py`, `blueprints/config_hub.py`, `tasks.py` | Múltiplos pontos chamam `enviar_texto`/`enviar_msg`/`enviar_documento` diretamente. **Todos devem passar a enfileirar em vez de enviar direto**, sem exceção, para que o rate-limit seja global e não por chamador. |
| Estado de conversa | `models.py::ChatState` | Já existe máquina de estados por funcionário (`estado`, `contexto` em JSON). Será reaproveitada na Fase 4 para o fluxo de opt-in conversacional. |
| Motor de texto dinâmico | `notification_processor.py::_render` | Já existe motor de substituição de variáveis (`{{name}}`, `{{saldo_dia}}` etc.), mas não há Spintax. Será estendido, não recriado.

**Conclusão da linha de base:** a arquitetura já tem quase todas as peças certas (fila, dispatcher único, config dinâmica, Celery, ChatState). O trabalho principal é **generalizar a fila existente** para todos os envios (não só os "fora de expediente") e **inserir uma camada de rate-limit/jitter** entre o enfileiramento e o `requests.post` real. Isso reduz o risco de regressão porque reaproveita padrões já testados no projeto.

---

## 0.1 Confirmação técnica com a documentação oficial da Mega-API

O usuário forneceu a documentação oficial da Mega-API ("Mega-api Docs"), que permite confirmar ou corrigir várias suposições da versão anterior deste PRD. Pontos confirmados:

1. **A própria Mega-API alerta para o risco de spam em todo endpoint de envio.** Cada método de envio (`text`, `mediaUrl`, `mediaBase64`, `location`, `sendLinkPreview`, `listMessage`, etc.) traz um aviso próprio dizendo que enviar mensagens para muitos contatos de forma simultânea pode ser interpretado como comportamento automatizado/spam pelo WhatsApp, aumentando o risco de bloqueio ou restrição da conta — e recomenda moderar frequência e volume. Isso **reforça diretamente a justificativa de negócio da Fase 1** (rate-limit/jitter): a mitigação não depende de nenhum recurso especial da Mega-API, é responsabilidade da aplicação cliente.
2. **A Mega-API não armazena mensagens** — confirma que o `WhatsappLog`/`FilaEnvioWhatsapp` do secullum10 são a única fonte de histórico; não há como "recuperar" mensagens perdidas do lado do provedor.
3. **Não existe endpoint de presença (`composing`/`recording`/`paused`) documentado nesta API.** A documentação cobre apenas quatro controllers: **Instance** (status, QR code, pairing code, logout, download de mídia, `isOnWhatsApp`), **Webhook** (consultar/configurar URL), **Message** (envio de texto, mídia por URL/Base64, localização, link preview, listas, contato, forward, quote/resposta) e **Chat** (deletar mensagem) — além do **Group Controller** (não relevante para este projeto, que só envia para contatos individuais). **Isso invalida a Fase 2 do PRD original** ("simulação de presença digitando/gravando") tal como especificada: a Mega-API não expõe esse recurso. Ver revisão da Fase 2 abaixo (seção 3.2).
4. **O campo de ID da mensagem enviada vem no nível raiz da resposta, como `id`** (não `messageId` nem `key.id`, como a versão anterior deste PRD supunha por analogia com outras APIs). Ex.: resposta de `POST /rest/sendMessage/{instance_key}/text` traz `{"error": false, "message": "...", "remoteJid": "...", "formMe": true, "id": "...", "text": "...", "messageTimestamp": "..."}`. **Correção aplicada na Fase 0 (3.0.2) abaixo.**
5. **O formato do campo `to` no `messageData` usa sufixo `@s.whatsapp.net`** em todos os exemplos da documentação oficial (ex.: `"to": "556199999999@s.whatsapp.net"`), com a própria documentação trazendo avisos "Fique atento!" reforçando esse formato para contatos privados. O código atual (`services/whatsapp_bot.py::_fone()`) produz só dígitos, sem sufixo (ex.: `"5511999999999"`). **Decisão incorporada nesta revisão**: mesmo funcionando hoje sem o sufixo (provavelmente por tolerância da instância), o projeto passa a enviar sempre no formato documentado oficialmente — reduz a chance de qualquer comportamento não padronizado ser sinalizado pelos sistemas antifraude/antispam do próprio WhatsApp. Tratado como tarefa concreta da Fase 1 (3.1.4), não mais como item em aberto.
6. **O webhook é configurável via API** (`GET /rest/webhook/{instance_key}` para consultar, `POST /rest/webhook/{instance_key}/configWebhook` para configurar `webhookUrl`/`webhookEnabled`), não só pelo painel. Isso permite à Fase 0 **automatizar** a configuração do webhook (ex.: em um script de instalação/migração) em vez de depender de um passo manual no painel Mega-API.
7. **A documentação não mostra um exemplo de payload de webhook de status de mensagem enviada** (`delivered`/`read`/`ack`) — só mostra exemplos de mensagens **recebidas** (`conversation`, `extendedTextMessage`, `audioMessage`, `imageMessage`, `videoMessage`, `documentMessage`, `locationMessage`, `contactMessage`, `stickerMessage`, `messageContextInfo`) e menciona que, ao desconectar, a instância "deixa de enviar webhooks" e passa a enviar apenas o payload de desconexão/QR code. **Decisão incorporada nesta revisão**: tratar como confirmado que este provedor não expõe esse evento; o rastreamento de entrega/leitura (`WhatsappLog.status_webhook`) sai do escopo da Fase 0 (ver Fase 0 revisada). A observabilidade se limita a status síncrono de envio (`error`/sucesso da própria chamada) e a eventos de conexão/desconexão da instância.
8. **A própria documentação desaconselha usar `isOnWhatsApp` como passo prévio ao envio**, pois a validação já ocorre internamente nas operações padrão de envio — confirma que o secullum10 não precisa (e não deve) adicionar essa chamada extra por mensagem, o que seria mais uma requisição por envio e não traria benefício.

---

## 1. Arquitetura alvo (visão geral)

```
Chamador (blueprint / service / task)
        │
        ▼
enviar_msg() / enviar_texto() / enviar_documento()   ← API pública NÃO MUDA (assinatura mantida)
        │  (passa a apenas ENFILEIRAR, nunca fazer requests.post diretamente)
        ▼
FilaEnvioWhatsapp (tabela nova, substitui/estende NotificacaoFila)
        │
        ▼
services/envio_dispatcher.py  ← NOVO: única camada que fala com a Mega-API
        │  aplica: rate-limit, jitter, spintax, regras de 1º contato
        │  (presença "digitando" NÃO é aplicável — Mega-API não expõe esse endpoint, ver 0.1/3.2)
        ▼
Mega-API (rest/sendMessage/...)
        │
        ▼
Webhook (blueprints/whatsapp.py) ← recebe eventos de conexão/desconexão (sem status de entrega/leitura — ver 0.1)
        │
        ▼
WhatsappLog atualizado + alertas (Fase 0/6)
```

Princípio orientador (do PRD de negócio, seção 8): **uma única camada** concentra delay, jitter e rate-limit (presença fica de fora — não suportada pela Mega-API, ver 0.1/Fase 2 revisada). Ela será o novo módulo `services/envio_dispatcher.py`, chamado exclusivamente pelo Celery beat / APScheduler — nunca diretamente pelos blueprints.

---

## 2. Visão geral das fases (mapeada ao PRD de negócio)

| Fase | Nome | Entregável técnico principal | Migração de banco? |
|---|---|---|---|
| 0 | Observabilidade mínima | Webhook de status (best-effort) + tabela de eventos de instância + alerta de desconexão | Sim |
| 1 | Fila unificada + delay/jitter | `FilaEnvioWhatsapp` + `envio_dispatcher.py` + scheduler de despacho | Sim |
| 2 | **Revisada:** delay adicional "humano" pré-envio (substitui a presença, que não existe na Mega-API) | Ajuste de parâmetros no `envio_dispatcher.py` (sem novo endpoint) | Não |
| 3 | Spintax | `services/spintax.py` + migração de templates | Sim (opcional) |
| 4 | Opt-in conversacional | Extensão do `ChatState` + novo estado `AGUARDANDO_OPTIN` | Sim |
| 5 | Regras de 1º contato | Flag `primeiro_contato` na fila + lint de template | Sim |
| 6 | Monitoramento contínuo | Dashboard em `blueprints/config_hub.py` + circuito de segurança | Não (usa dados das fases anteriores) |

---

## 3. Detalhamento por fase

### FASE 0 — Observabilidade mínima (revisada com as decisões de 22/07/2026)

**Decisões incorporadas nesta revisão:**
- **Confirmado**: o `id` da mensagem vem na resposta síncrona do próprio `POST /rest/sendMessage/.../text` (campo raiz `id`) — não é necessário esperar webhook para obtê-lo. Mantido em `WhatsappLog.mega_message_id`, útil para features futuras de responder/marcar/excluir mensagem (a própria documentação recomenda guardá-lo para isso), mesmo sem uso de rastreamento de entrega.
- **Descartado**: rastreamento de `delivered`/`read` via webhook. A documentação oficial só lista webhooks de mensagens **recebidas** — não há evidência de evento de "ack" de entrega/leitura para mensagens enviadas por este provedor. **A coluna `status_webhook` sai do escopo desta fase.** A saúde do envio passa a ser medida só pelo que já é observável de forma síncrona (`error: false`/`true` na resposta do `POST`, já capturado hoje em `WhatsappLog.status`).

**Objetivo (revisado):** capturar o `mega_message_id` de cada envio (para uso futuro) e ser alertado se a instância cair — sem depender de nenhum evento de entrega/leitura que a Mega-API não confirma emitir.

**3.0.1 — Migração de banco (`migrations/versions/xxxx_observabilidade_whatsapp.py`)**

Adicionar em `WhatsappLog`:
- `mega_message_id` (String, nullable) — ID retornado pela Mega-API no `resp.json()['id']` do `enviar_texto`/`enviar_documento`/etc., hoje descartado.
- `atualizado_em` (DateTime, nullable) — mantido para uso genérico (ex.: futuras correções manuais de status pelo painel), mesmo sem o fluxo de ack.

> Removido desta migração, em relação à versão anterior: a coluna `status_webhook` (`delivered`/`read`/`failed`). Se no futuro a Mega-API passar a emitir esse evento (ou o projeto migrar de provedor), a coluna pode ser adicionada em uma migração própria nessa ocasião.

Nova tabela `MegaApiInstanceEvent`:
```python
class MegaApiInstanceEvent(db.Model):
    __tablename__ = 'megaapi_instance_events'
    id = db.Column(db.Integer, primary_key=True)
    tipo_evento = db.Column(db.String(50))   # 'connected' / 'disconnected' / 'qr_needed'
    payload_raw = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
```

**3.0.2 — Capturar `mega_message_id` no envio (confirmado)**

Confirmado na documentação oficial da Mega-API (seção Message Controller — Envio de Mensagens de Texto, Response 200) e na resposta do usuário: a resposta de `POST /rest/sendMessage/{instance_key}/text` traz o ID da mensagem no campo **raiz `id`**, disponível de forma síncrona, sem necessidade de webhook. O mesmo padrão se repete nos demais endpoints de envio (`mediaUrl`, `mediaBase64`, `location`, `sendLinkPreview`, `listMessage`, `contactMessage`, etc.).

Em `services/whatsapp_bot.py`, em `_despachar_real` (ver Fase 1, 3.1.4 — é essa função, não mais `enviar_texto`, que faz o `requests.post` de fato), após `resp = requests.post(...)`:
```python
if ok:
    try:
        log.mega_message_id = resp.json().get('id')
    except Exception:
        pass
```

**3.0.3 — Webhook de conexão/desconexão da instância (escopo simplificado)**

Sem o fluxo de ack de entrega/leitura, o webhook adicional desta fase se limita a monitorar a **saúde da conexão da instância** (o sinal mais direto de risco de banimento: uma instância que cai sozinha, sem ter sido deslogada manualmente, é candidata a ter sido banida ou a exigir novo QR code):

```python
@whatsapp_bp.route('/webhook/status', methods=['POST'])
def webhook_status():
    """Recebe eventos de conexão/desconexão da instância Mega-API (não trata ack de mensagem — ver 3.0)."""
    payload_bytes = request.get_data()
    signature = request.headers.get('X-Mega-Signature', '')
    if not _validar_hmac(payload_bytes, signature):
        return jsonify({'error': 'invalid signature'}), 401

    data = request.get_json(force=True, silent=True) or {}

    from models import MegaApiInstanceEvent
    db.session.add(MegaApiInstanceEvent(tipo_evento='desconhecido', payload_raw=json.dumps(data)))
    db.session.commit()

    from tasks import processar_evento_instancia
    processar_evento_instancia.delay(data)

    return jsonify({'ok': True}), 200
```
> Nota: como a documentação não detalha o schema exato do payload de conexão/desconexão (só menciona, em texto, que ele existe e que os demais webhooks cessam quando a instância cai), o `tipo_evento` é gravado como `'desconhecido'` na captura bruta e só classificado (`connected`/`disconnected`/`qr`) dentro da task assíncrona, após inspeção real do payload em ambiente de teste — evita perder o evento por um parsing errado antes de conhecer o formato exato.

Configurar a URL via `POST /rest/webhook/{instance_key}/configWebhook` (endpoint confirmado pela documentação — ver 0.1 item 6), o que permite automatizar esse passo em vez de depender do painel manual.

**3.0.4 — Nova Celery task em `tasks.py`**
```python
@celery.task(name='tasks.processar_evento_instancia')
def processar_evento_instancia(payload):
    """Classifica o evento bruto de conexão e alerta em caso de desconexão."""
    from models import MegaApiInstanceEvent
    from extensions import db
    # Heurística inicial: ajustar as chaves reais assim que confirmadas em teste.
    indicativo_desconexao = any(
        str(v).lower() in ('disconnected', 'close', 'logout')
        for v in payload.values()
    ) if isinstance(payload, dict) else False

    if indicativo_desconexao:
        logger.error(f'[ALERTA] Possível desconexão da instância Mega-API: {payload}')
        from flask_mail import Message
        from extensions import mail
        from services.config_service import get_setting
        destinatario = get_setting('alerta_email_destino', 'ALERTA_EMAIL_DESTINO', '')
        if destinatario:
            msg = Message('⚠️ Instância WhatsApp possivelmente desconectada', recipients=[destinatario])
            msg.body = f'Payload recebido:\n{payload}'
            mail.send(msg)
    return {'processado': True}
```

**3.0.5 — Critério de aceite**
- É possível consultar, via query em `WhatsappLog`, quantas mensagens foram `enviado`/`erro_*`/`sem_config`, e cada uma com `mega_message_id` preenchido quando `enviado`.
- Um payload de desconexão gera uma linha em `MegaApiInstanceEvent` e, quando a heurística identifica desconexão, um e-mail de alerta em até 1 minuto (task assíncrona).
- **Não** é critério de aceite desta fase medir taxa de entrega/leitura — esse dado não está disponível nesta API (ver 0.1 item 7 e decisão acima).

---

### FASE 1 — Fila unificada + delay/jitter

**Objetivo:** nenhum envio deve mais ser síncrono/direto; todo envio passa por uma fila com intervalo mínimo variável entre mensagens.

**3.1.1 — Migração: generalizar `NotificacaoFila` → `FilaEnvioWhatsapp`**

Em vez de criar uma tabela paralela, **estender** `NotificacaoFila` (ela já tem quase todos os campos necessários) e renomeá-la semanticamente via novos campos, mantendo compatibilidade com o uso atual de "Direito à Desconexão":

```python
class FilaEnvioWhatsapp(db.Model):  # renomeia NotificacaoFila
    __tablename__ = 'fila_envio_whatsapp'   # nova tabela; migração copia dados de notificacao_fila
    id = db.Column(db.Integer, primary_key=True)
    regra_id = db.Column(db.Integer, db.ForeignKey('notification_rules.id'), nullable=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)
    celular = db.Column(db.String(20), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(50))
    tipo_regra = db.Column(db.String(50), nullable=True)
    tipo_msg = db.Column(db.String(20), default='texto')       # NOVO: texto/botoes/lista/documento
    interativo_json = db.Column(db.Text, nullable=True)        # NOVO
    anexo_ref = db.Column(db.String(255), nullable=True)       # NOVO: referência a PDF gerado (ver Fase 4/5)
    data_referencia = db.Column(db.Date, nullable=True)
    prioridade = db.Column(db.Integer, default=10)              # NOVO: menor = mais prioritário (urgente=1)
    primeiro_contato = db.Column(db.Boolean, default=False)     # NOVO: usado na Fase 5
    enviar_apos = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='pendente')       # pendente/processando/enviado/erro/cancelado
    tentativas = db.Column(db.Integer, default=0)
    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    enviado_em = db.Column(db.DateTime, nullable=True)
```

> Antigravity: gerar a migração Alembic com `flask db migrate -m "fila_envio_whatsapp"` e revisar manualmente o script gerado em `migrations/versions/` (o projeto já segue esse padrão — ver `migrations/versions/4d8de113b578_horario_secullum.py` como referência de estilo). Incluir um passo de migração de dados que copia linhas de `notificacao_fila` para `fila_envio_whatsapp` antes de dropar a tabela antiga, para não perder mensagens pendentes em produção.

**3.1.2 — Novas configurações (tabela `Configuracao`, via `get_setting`)**

| Chave | Env var fallback | Default | Descrição |
|---|---|---|---|
| `whatsapp_delay_min_s` | `WA_DELAY_MIN_S` | `20` | Intervalo mínimo entre envios (segundos) |
| `whatsapp_delay_max_jitter_s` | `WA_DELAY_JITTER_S` | `15` | Jitter aleatório somado ao mínimo |
| `whatsapp_max_por_hora` | `WA_MAX_HORA` | `20` | Teto de envios por hora por instância |
| `whatsapp_dispatcher_ativo` | `WA_DISPATCHER_ATIVO` | `1` | Circuito de segurança (Fase 6) — liga/desliga o despacho |

> **Decisão incorporada nesta revisão**: o negócio confirmou que **não há mensagens urgentes** no sistema (ver seção 7 da revisão anterior, agora resolvida). Isso significa que os valores acima podem ser calibrados de forma conservadora (delay maior, teto/hora menor) sem risco de atrasar algo crítico — e reforça que o campo `prioridade` em `FilaEnvioWhatsapp` pode, na prática, usar um único valor padrão para a maioria das regras (a coluna é mantida no schema para flexibilidade futura, mas não é necessário desenhar uma lógica de priorização por urgência nesta fase). Isso também abre espaço para, se o volume diário justificar, diluir os envios de forma ainda mais espalhada ao longo do expediente (ex.: aumentar `whatsapp_delay_min_s` e reduzir `whatsapp_max_por_hora` além dos defaults sugeridos) — exatamente a recomendação da própria Mega-API de moderar frequência e volume (ver 0.1 item 1).

**3.1.3 — Novo módulo `services/envio_dispatcher.py`**

```python
"""Camada única de despacho de WhatsApp: delay, jitter, rate-limit e delay proporcional ao tamanho (Fase 2 revisada)."""
import random
from datetime import datetime, timedelta
from extensions import db
from models import FilaEnvioWhatsapp

def _cfg_int(chave, env, default):
    from services.config_service import get_setting
    return int(get_setting(chave, env, str(default)))

def _pode_enviar_agora() -> bool:
    """Verifica intervalo mínimo desde o último envio bem-sucedido + teto por hora."""
    from services.config_service import get_setting
    if get_setting('whatsapp_dispatcher_ativo', 'WA_DISPATCHER_ATIVO', '1') != '1':
        return False

    ultimo = (FilaEnvioWhatsapp.query
              .filter(FilaEnvioWhatsapp.status == 'enviado')
              .order_by(FilaEnvioWhatsapp.enviado_em.desc())
              .first())
    delay_min = _cfg_int('whatsapp_delay_min_s', 'WA_DELAY_MIN_S', 20)
    jitter = random.randint(0, _cfg_int('whatsapp_delay_max_jitter_s', 'WA_DELAY_JITTER_S', 15))
    intervalo_necessario = delay_min + jitter

    if ultimo and ultimo.enviado_em:
        decorrido = (datetime.utcnow() - ultimo.enviado_em).total_seconds()
        if decorrido < intervalo_necessario:
            return False

    limite_hora = datetime.utcnow() - timedelta(hours=1)
    enviados_ultima_hora = FilaEnvioWhatsapp.query.filter(
        FilaEnvioWhatsapp.status == 'enviado',
        FilaEnvioWhatsapp.enviado_em >= limite_hora,
    ).count()
    max_hora = _cfg_int('whatsapp_max_por_hora', 'WA_MAX_HORA', 20)
    return enviados_ultima_hora < max_hora


def processar_proximo() -> dict:
    """Processa NO MÁXIMO 1 item da fila por chamada (chamado a cada poucos segundos pelo scheduler)."""
    if not _pode_enviar_agora():
        return {'skipped': True, 'motivo': 'rate_limit_ou_desativado'}

    agora = datetime.utcnow()
    item = (FilaEnvioWhatsapp.query
            .filter(
                FilaEnvioWhatsapp.status == 'pendente',
                db.or_(FilaEnvioWhatsapp.enviar_apos.is_(None),
                       FilaEnvioWhatsapp.enviar_apos <= agora),
            )
            .order_by(FilaEnvioWhatsapp.prioridade.asc(), FilaEnvioWhatsapp.criada_em.asc())
            .first())
    if not item:
        return {'skipped': True, 'motivo': 'fila_vazia'}

    item.status = 'processando'
    item.tentativas = (item.tentativas or 0) + 1
    db.session.commit()

    from services.whatsapp_bot import _despachar_real  # ver 3.1.4
    ok = _despachar_real(item)

    item.status = 'enviado' if ok else 'erro'
    item.enviado_em = datetime.utcnow() if ok else item.enviado_em
    db.session.commit()
    return {'enviado': ok, 'item_id': item.id}
```

**3.1.4 — Refatoração de `services/whatsapp_bot.py`**

As funções públicas (`enviar_texto`, `enviar_botoes`, `enviar_menu_lista`, `enviar_documento`, `enviar_msg`) **mantêm a mesma assinatura** (para não quebrar ~50 chamadas espalhadas no código), mas passam a apenas **inserir na fila** em vez de chamar `requests.post` diretamente:

```python
def enviar_texto(celular, mensagem, func_id=None, tipo='saida', tipo_regra=None, data_ref=None,
                  prioridade=10, primeiro_contato=False) -> bool:
    """Agora ENFILEIRA em vez de enviar direto. Retorno True = enfileirado com sucesso."""
    from models import FilaEnvioWhatsapp
    item = FilaEnvioWhatsapp(
        celular=_fone(celular), mensagem=mensagem, funcionario_id=func_id,
        tipo=tipo, tipo_regra=tipo_regra, data_referencia=data_ref,
        tipo_msg='texto', prioridade=prioridade, primeiro_contato=primeiro_contato,
        status='pendente',
    )
    db.session.add(item)
    db.session.commit()
    return True

def _despachar_real(item) -> bool:
    """Chamada exclusivamente pelo envio_dispatcher.py. Faz o requests.post de fato."""
    # Corpo praticamente idêntico ao enviar_texto/enviar_botoes/enviar_documento ATUAIS,
    # mas escrevendo em WhatsappLog em vez de FilaEnvioWhatsapp, e ramificando por item.tipo_msg.
    ...
```

> **Ponto crítico de atenção para o Antigravity:** existem chamadas hoje que dependem do **retorno booleano imediato** de `enviar_texto` para decidir o próximo passo síncrono dentro da mesma requisição HTTP (ex.: `blueprints/whatsapp.py:749` em rotas de teste manual, e `blueprints/config_hub.py:258` no botão "testar envio" do painel). Para essas rotas de **teste manual no painel administrativo**, manter um caminho síncrono explícito (ex.: parâmetro `imediato=True` que chama `_despachar_real` diretamente, sem fila) — são disparos de baixíssimo volume, feitos manualmente por um humano no painel, e o próprio PRD de negócio (seção 5) já reconhece esse tipo de exceção. Todo o restante (regras automáticas, relatórios, avaliações, notificações em massa) **deve** ir para a fila.

**3.1.4.1 — Corrigir `_fone()` para o formato oficial (`@s.whatsapp.net`)**

Decisão incorporada (ver 0.1 item 5): alinhar o projeto ao formato documentado oficialmente pela Mega-API, mesmo que o formato atual (só dígitos) esteja funcionando hoje.

```python
def _fone(celular: str) -> str:
    """Normaliza celular para 5511999999999@s.whatsapp.net (formato oficial da Mega-API)."""
    digits = ''.join(c for c in (celular or '') if c.isdigit())
    if len(digits) == 11:
        digits = f'55{digits}'
    # (mantém a lógica de dígitos já existente; só adiciona o sufixo no final)
    return f'{digits}@s.whatsapp.net' if digits and '@' not in digits else digits
```
> Atenção: como `_fone()` é usado tanto para montar o `to` do payload quanto (hoje) para comparações/armazenamento em `WhatsappLog.celular`/`FilaEnvioWhatsapp.celular`, decidir explicitamente **onde** o sufixo é aplicado — recomendação: manter `celular` armazenado só com dígitos (sem sufixo) nas tabelas do banco (mais fácil de buscar/comparar/deduplicar, inclusive para a checagem de "primeiro contato" da Fase 5), e aplicar o sufixo **apenas no momento de montar o `messageData`** dentro de `_despachar_real`, via uma função separada (ex.: `_jid(celular)`) — evitando duas responsabilidades dentro de `_fone()`.

**3.1.5 — Agendamento do despacho**

Adicionar em `services/auto_sync.py` (reaproveitando o `BackgroundScheduler` do APScheduler já inicializado ali) um novo job de intervalo curto, já que o Celery beat só tem granularidade de minuto e a Fase 1 exige intervalos de 20-45s:
```python
_scheduler.add_job(
    func=_processar_fila_whatsapp_job,
    trigger='interval',
    seconds=5,   # verifica a fila a cada 5s; o próprio _pode_enviar_agora() decide se envia
    id='fila_whatsapp_dispatcher',
    replace_existing=True,
)
```
onde `_processar_fila_whatsapp_job` chama `services.envio_dispatcher.processar_proximo()` dentro do `app.app_context()`, seguindo o mesmo padrão já usado pelos outros jobs desse arquivo.

**3.1.6 — Migrar chamadores existentes**

Buscar e ajustar (sem mudar comportamento de negócio, só a semântica de "agora é assíncrono"):
- `services/notification_processor.py::_enviar` e `processar_fila_notificacoes` (esta última pode ser **removida** — sua função é absorvida pelo novo dispatcher genérico, mantendo a lógica de "Direito à Desconexão" apenas como o cálculo de `enviar_apos`, que continua existindo).
- `services/avaliacao_service.py`, `services/report_service.py`, `blueprints/marketplace.py`, `blueprints/espelho.py`, `blueprints/config_hub.py`, `tasks.py` — nenhuma mudança de código é necessária nesses arquivos **se a assinatura de `enviar_texto`/`enviar_msg`/`enviar_documento` for mantida**, o que é o objetivo do design acima.

**3.1.7 — Critério de aceite**
- Nenhum par de mensagens é enviado à Mega-API com intervalo menor que `whatsapp_delay_min_s`.
- Os intervalos reais variam entre envios consecutivos (não são idênticos) — validar via consulta em `WhatsappLog.criado_em` ordenado.
- Rotas de teste manual no painel continuam respondendo de forma síncrona ao usuário.

---

### FASE 2 — Revisada: não há endpoint de presença na Mega-API

**Mudança em relação à versão anterior deste PRD:** a documentação oficial da Mega-API, fornecida pelo usuário, cobre exaustivamente quatro controllers (Instance, Webhook, Message, Chat) e nenhum deles expõe um método de presença (`composing`/`recording`/`paused`, "digitando..."). Diferente de outras plataformas (ex.: Baileys/WhatsApp Web direto, ou a API oficial do WhatsApp Business/Cloud API, que têm endpoints de presença), **a Mega-API simplesmente não oferece esse recurso**. Portanto, a Fase 2 como descrita no PRD de negócio original (seção "Simulação de presença") **não é implementável com o provedor atual**, e não deve ser codificada — não há endpoint a chamar.

**Objetivo revisado:** já que a simulação visual de "digitando..." é inviável, reforçar o único mecanismo disponível para dar ao envio um ritmo menos robótico: **um atraso adicional, aplicado por mensagem, antes do disparo real**, propositalmente maior para mensagens mais longas — um proxy indireto e mais grosseiro do mesmo objetivo (o tempo entre a "chegada" da notificação e a mensagem aparecer para o destinatário deixa de ser instantâneo), mas sem qualquer indicador visual no WhatsApp do destinatário.

**3.2.1 — Parâmetro adicional no `envio_dispatcher.py` (sem novo endpoint)**

Estender a config já criada na Fase 1 com:

| Chave | Env var fallback | Default | Descrição |
|---|---|---|---|
| `whatsapp_delay_por_caractere_s` | `WA_DELAY_CHAR_S` | `0.15` | Segundos adicionais de espera por caractere da mensagem, somados ao delay/jitter da Fase 1 |
| `whatsapp_delay_extra_max_s` | `WA_DELAY_EXTRA_MAX_S` | `10` | Teto do delay extra proporcional ao tamanho, para não atrasar demais mensagens longas |

Em `envio_dispatcher.py::processar_proximo`, antes de `_despachar_real(item)` (sem `time.sleep` bloqueante — em vez disso, **adiar o próprio item na fila**, reaproveitando o campo `enviar_apos` já existente):
```python
def _calcular_delay_extra(mensagem: str) -> float:
    por_char = _cfg_float('whatsapp_delay_por_caractere_s', 'WA_DELAY_CHAR_S', 0.15)
    teto = _cfg_float('whatsapp_delay_extra_max_s', 'WA_DELAY_EXTRA_MAX_S', 10)
    return min(teto, len(mensagem or '') * por_char)
```
Esse cálculo entra na etapa em que o item é lido pela primeira vez da fila (ex.: ao enfileirar em `enviar_texto`, ou na primeira leitura pelo dispatcher): se `item.enviar_apos` ainda não foi definido por outra regra (ex.: Direito à Desconexão da Fase 4/legado), define-se `item.enviar_apos = datetime.utcnow() + timedelta(seconds=_calcular_delay_extra(item.mensagem))`. Isso evita `time.sleep` dentro do job do scheduler (elimina o risco de concorrência com `services/auto_sync.py` mencionado na versão anterior deste PRD) e reaproveita 100% da mecânica de fila já existente.

**3.2.2 — Critério de aceite**
- Mensagens mais longas (ex.: resumo semanal) esperam proporcionalmente mais tempo na fila antes do envio do que mensagens curtas (ex.: aviso de boleto), respeitado o teto configurável.
- Nenhum `time.sleep` bloqueante é introduzido no job do scheduler.

**3.2.3 — Se, no futuro, o secullum10 avaliar trocar de provedor**

Caso a equipe queira recuperar a simulação de presença de fato (com o indicador visual "digitando..." aparecendo para o destinatário), isso exigiria um provedor diferente que exponha esse recurso — por exemplo, uma integração via Baileys (biblioteca não-oficial que fala diretamente com o protocolo do WhatsApp Web e expõe `sendPresenceUpdate`) hospedada pela própria equipe, ou a API oficial do WhatsApp Business (Cloud API/BSP), que tem seu próprio conjunto de regras e é explicitamente citada como fora do escopo deste PRD (seção 5 do documento de negócio original). Registrar isso como recomendação futura, não como tarefa desta fase.

---

### FASE 3 — Motor de variação de texto (Spintax)

**Objetivo:** dois funcionários recebendo a mesma notificação no mesmo dia não recebem texto idêntico.

**3.3.1 — Novo módulo `services/spintax.py`**
```python
import random
import re

_SPINTAX_RE = re.compile(r'\{([^{}]+)\}')

def resolver_spintax(texto: str) -> str:
    """Resolve {opcao1|opcao2|opcao3} recursivamente, escolhendo uma opção aleatória."""
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = _SPINTAX_RE.sub(lambda m: random.choice(m.group(1).split('|')), texto)
    return texto
```

**3.3.2 — Integração no motor de templates existente**

Em `services/notification_processor.py::_render`, **após** a substituição de variáveis (`{{name}}` etc.) e **antes** do `return template`:
```python
from services.spintax import resolver_spintax
template = resolver_spintax(template)
```
Isso reaproveita 100% do motor de variáveis já existente — só adiciona uma etapa final.

**3.3.3 — Migração de templates**

Os templates ficam em `NotificationRule.template_employee` / `template_manager` (tabela já existente, editável via painel — ver `blueprints/config_hub.py` e `blueprints/notificacoes.py::BOT_MSG_DEFAULTS`). Não é necessária migração de schema; é necessário:
- Atualizar os textos-padrão em `BOT_MSG_DEFAULTS` (blueprints/notificacoes.py) para incluir Spintax com no mínimo 3 variações por trecho variável, conforme exemplo do PRD de negócio (seção 11).
- Adicionar validação no formulário de edição de template (`templates/config/...`) avisando o usuário sobre a sintaxe `{opção1|opção2}`.
- Persistir, no envio (Fase 1, campo a adicionar em `WhatsappLog`: `variacao_resolvida` ou simplesmente logar o texto final já resolvido — o campo `mensagem` já guarda o texto final, então **nenhuma coluna nova é necessária aqui**, pois o log grava a mensagem já processada).

**3.3.4 — Critério de aceite**
- Uma regra com Spintax cadastrada, disparada para 2+ funcionários no mesmo dia, gera mensagens com texto visivelmente diferente (validado manualmente comparando `WhatsappLog.mensagem` de dois envios da mesma `tipo_regra`/`data_referencia`).

---

### FASE 4 — Fluxo de "pergunta antes do conteúdo" (opt-in conversacional)

**Objetivo:** para os tipos de mensagem configurados, só enviar o conteúdo principal após resposta afirmativa.

**Decisão de negócio incorporada nesta revisão:** como (a) o sistema é só para atender funcionários da própria empresa (não há contato frio/comercial), (b) não há mensagens urgentes que exijam entrega imediata, e (c) o objetivo principal do projeto é blindar o número contra denúncias — que é a causa mais comum de bloqueio em provedores não-oficiais como a Mega-API —, o opt-in conversacional deixa de ser uma decisão de RH em aberto e passa a ser **o padrão recomendado para a maior parte das regras**, não uma exceção. Concretamente: `requer_optin` nasce com **default `True`** na migração (invertendo a proposta anterior, que era `False`), e o Antigravity, na ausência de uma lista explícita da equipe de RH, deve **desativar manualmente (`requer_optin=False`) apenas para os poucos tipos de regra que, por natureza, são resposta direta a uma ação do próprio funcionário** (ex.: confirmação de check-in que o funcionário acabou de solicitar, resposta a uma pergunta que ele mesmo fez ao bot) — nesses casos não faz sentido perguntar "posso te enviar" sobre algo que ele pediu agora mesmo. Regras de iniciativa da empresa (avisos, resumos, lembretes, boletos, notificações de banco de horas, avaliação 360°, etc.) mantêm `requer_optin=True`.

**3.4.1 — Reaproveitar `ChatState`**

O modelo `ChatState` (já usado pelo bot conversacional em `blueprints/whatsapp.py`) ganha um novo estado: `AGUARDANDO_OPTIN`, com `contexto` guardando `{'fila_id': <id do FilaEnvioWhatsapp original>, 'expira_em': <iso datetime>}`.

**3.4.2 — Nova configuração por tipo de mensagem**

Adicionar coluna em `NotificationRule` (migração):
```python
requer_optin = db.Column(db.Boolean, default=True)   # NOVO — default True (ver decisão acima)
optin_janela_horas = db.Column(db.Integer, default=24) # NOVO
optin_fallback = db.Column(db.String(20), default='enviar')  # 'enviar' | 'reenviar_pergunta' | 'cancelar'
```
Exibir esses campos no formulário de edição de regra (`templates/config/...`, `blueprints/config_hub.py`), com o campo já marcado como ativado por padrão para regras novas, e um aviso explicativo: "desmarque apenas se esta mensagem for resposta direta a uma ação que o próprio funcionário acabou de fazer".

**3.4.3 — Fluxo no `envio_dispatcher.py`**

Quando um item de `FilaEnvioWhatsapp` pertence a uma `regra` com `requer_optin=True`:
1. Em vez de despachar `item.mensagem` diretamente, despachar uma mensagem de abertura fixa (configurável em `Configuracao`, chave `whatsapp_optin_texto_padrao`, ex.: `"Olá {{name}}, tudo bem? Posso te enviar {{assunto}} por aqui?"`).
2. Guardar o item original com `status='aguardando_optin'` (novo valor de status) em vez de `'enviado'`.
3. Criar/atualizar `ChatState` do funcionário para `AGUARDANDO_OPTIN`, com `contexto={'fila_id': item.id}`.

**3.4.4 — Detecção de resposta em `blueprints/whatsapp.py::_processar_mensagem`**

Adicionar, no fluxo de tratamento de mensagem recebida (onde hoje já existe tratamento de estado via `ChatState`), um ramo:
```python
if state.estado == 'AGUARDANDO_OPTIN':
    contexto = json.loads(state.contexto or '{}')
    fila_id = contexto.get('fila_id')
    palavras_afirmativas = _get_setting_lista('whatsapp_optin_palavras', 'WA_OPTIN_PALAVRAS', 'sim,pode,ok,claro,manda')
    texto_normalizado = _normalizar_sem_acento(texto_recebido.lower().strip())
    if any(p in texto_normalizado for p in palavras_afirmativas):
        item = FilaEnvioWhatsapp.query.get(fila_id)
        if item and item.status == 'aguardando_optin':
            item.status = 'pendente'   # volta para a fila normal, dispatcher processa o conteúdo real
            item.enviar_apos = None
            db.session.commit()
        _set_state(func.id, 'IDLE')
    else:
        enviar_texto(celular, "Sem problema! Se precisar, é só chamar.", func_id=func.id)
        _set_state(func.id, 'IDLE')
    return
```
(`_normalizar_sem_acento` é um helper novo e pequeno; `_get_setting_lista` é um wrapper simples sobre `get_setting` que faz `.split(',')`.)

**3.4.5 — Fallback por tempo**

Nova Celery task periódica (adicionar em `app.py::beat_schedule`, granularidade de minuto já usada pelo projeto):
```python
'processar-fallback-optin': {
    'task': 'tasks.processar_fallback_optin',
    'schedule': crontab(minute='*/15'),
},
```
```python
@celery.task(name='tasks.processar_fallback_optin')
def processar_fallback_optin():
    """Aplica a regra de fallback (enviar/reenviar/cancelar) para opt-ins vencidos."""
    from models import FilaEnvioWhatsapp, ChatState
    agora = datetime.utcnow()
    pendentes = FilaEnvioWhatsapp.query.filter_by(status='aguardando_optin').all()
    for item in pendentes:
        regra = item.regra
        prazo = timedelta(hours=(regra.optin_janela_horas if regra else 24))
        if agora - item.criada_em < prazo:
            continue
        fallback = regra.optin_fallback if regra else 'enviar'
        if fallback == 'enviar':
            item.status = 'pendente'
        elif fallback == 'reenviar_pergunta':
            item.criada_em = agora  # reinicia a janela, reenvia a pergunta uma única vez
            # marcar de alguma forma que já reenviou, para não reenviar indefinidamente
        else:
            item.status = 'cancelado'
        db.session.commit()
```

**3.4.6 — Critério de aceite**
- Para uma regra com `requer_optin=True`, o conteúdo principal só chega ao funcionário depois de uma resposta afirmativa detectada, ou após a janela de fallback definida.
- Regras com `requer_optin=False` (padrão) continuam se comportando exatamente como hoje.

---

### FASE 5 — Regras para primeiro contato (sem link/gatilho)

**Objetivo:** mensagens para contatos sem histórico não podem conter URL nem palavras-gatilho.

**3.5.1 — Determinar "primeiro contato"**

Em `services/whatsapp_bot.py::enviar_texto` (a versão que enfileira, Fase 1), antes de criar o item:
```python
primeiro_contato = not db.session.query(
    WhatsappLog.query.filter_by(celular=_fone(celular), status='enviado').exists()
).scalar()
```

**3.5.2 — Nova configuração: lista de palavras-gatilho e validação**

Chave `Configuracao`: `whatsapp_palavras_gatilho` (CSV), default sugerido: `"promoção,grátis,desconto,clique aqui,imperdível"`.

Novo helper em `services/spintax.py` (ou módulo novo `services/lint_template.py`):
```python
import re

_URL_RE = re.compile(r'https?://|www\.', re.IGNORECASE)

def validar_template_primeiro_contato(texto: str, palavras_gatilho: list[str]) -> list[str]:
    """Retorna lista de problemas encontrados (vazia = ok)."""
    problemas = []
    if _URL_RE.search(texto):
        problemas.append('Contém URL — não permitido em mensagem de primeiro contato.')
    texto_low = texto.lower()
    for p in palavras_gatilho:
        if p.strip().lower() in texto_low:
            problemas.append(f'Contém palavra-gatilho: "{p.strip()}"')
    return problemas
```

**3.5.3 — Dois pontos de aplicação**

1. **Em tempo de execução** (`envio_dispatcher.py::processar_proximo`, antes de `_despachar_real`): se `item.primeiro_contato` e a mensagem (já resolvida do Spintax) falha na validação, **não enviar** — mover para `status='bloqueado_lint'` e logar um alerta (reaproveita a infra de alerta da Fase 0/6). Isso é uma rede de segurança, não o mecanismo principal.
2. **Em tempo de cadastro do template** (`blueprints/config_hub.py`, rota de salvar `NotificationRule`/template): rodar `validar_template_primeiro_contato` sobre `template_employee`/`template_manager` quando o template estiver marcado como usado em primeiro contato, e **bloquear o salvamento** exibindo os problemas encontrados via `flash()` (padrão já usado nesse blueprint).

**3.5.4 — Interação com a Fase 4**

A mensagem de abertura do opt-in (3.4.3) é, por definição, a mensagem de primeiro contato mais comum — ela deve ser validada como qualquer outro template e, por design, já não deve conter link (é só uma pergunta).

**3.5.5 — Critério de aceite**
- Nenhuma linha em `WhatsappLog` com `status='enviado'` e `primeiro_contato=True` contém URL ou palavra da lista de gatilhos (validar por consulta/regex sobre o histórico após deploy).
- Tentar salvar um template de primeiro contato com link é bloqueado no painel com mensagem de erro clara.

---

### FASE 6 — Monitoramento contínuo e circuito de segurança

**Objetivo:** sustentar o ganho e reagir rápido a sinais de risco.

**3.6.1 — Dashboard**

Nova rota/aba em `blueprints/config_hub.py` (o painel de configuração já é o lugar natural — ver seção "Testar Envio" hoje existente em `templates/config/index.html`), exibindo, a partir de `WhatsappLog` e `MegaApiInstanceEvent`:
- Envios/erros por dia (últimos 7/30 dias) — sem "entregues/lidos", métrica não disponível neste provedor (ver 0.1 item 7).
- Taxa de resposta a opt-ins (Fase 4): `aguardando_optin` → `pendente` (aceito) vs `cancelado`/expirado.
- Últimas desconexões de instância.
- Estado atual do circuito de segurança (`whatsapp_dispatcher_ativo`).

**3.6.2 — Alerta automático de falha de envio (revisado: sem `status_webhook`)**

Sem rastreamento de entrega/leitura (ver Fase 0 revisada), a métrica de "falha" desta fase é a taxa de `WhatsappLog.status` iniciando com `erro_` (falha síncrona reportada pela própria Mega-API no `POST`) sobre o total de tentativas, não mais uma taxa de `status_webhook == 'failed'`.

Nova Celery task periódica (`crontab(minute='*/15')`):
```python
@celery.task(name='tasks.verificar_saude_whatsapp')
def verificar_saude_whatsapp():
    limite = datetime.utcnow() - timedelta(hours=1)
    total = WhatsappLog.query.filter(WhatsappLog.criado_em >= limite).count()
    falhas = WhatsappLog.query.filter(
        WhatsappLog.criado_em >= limite,
        WhatsappLog.status.like('erro_%'),
    ).count()
    if total > 0 and (falhas / total) > 0.15:
        from services.config_service import set_setting  # criar helper simétrico a get_setting, se não existir
        set_setting('whatsapp_dispatcher_ativo', '0')  # pausa a fila automaticamente
        # + envia alerta por e-mail, reaproveitando o helper da Fase 0 (processar_evento_instancia
        #   pode servir de referência para um alertar_evento(assunto, corpo) genérico)
```
> Observação: `services/config_service.py` hoje só tem `get_setting`; será necessário criar `set_setting(chave, valor)` simétrico (a lógica já existe, duplicada, em `tasks.py::_set_cfg` — o Antigravity deve **unificar** essas duas implementações em uma única função em `config_service.py` para não violar o princípio de "camada única" do requisito 8, e atualizar `tasks.py` para importar dali).

**3.6.3 — Revisão periódica (processo, não código)**

Documentar em `MANUAL_DE_INSTALACAO_LINUX.md` (ou novo `MANUAL_OPERACAO_WHATSAPP.md`) uma checklist mensal: revisar variações de Spintax, revisar lista de palavras-gatilho, revisar taxa de resposta de opt-in por tipo de regra.

**3.6.4 — Critério de aceite**
- Existe uma tela consultável no painel com as métricas-chave.
- Uma simulação de taxa de falha > 15% em 1h desativa automaticamente `whatsapp_dispatcher_ativo` e dispara um e-mail de alerta.

---

## 4. Requisitos técnicos transversais (aplicam-se a todas as fases)

1. **Camada única de envio**: toda lógica de delay/jitter/rate-limit vive em `services/envio_dispatcher.py` (presença não se aplica — não suportada pela Mega-API, ver 0.1/Fase 2). Nenhum blueprint ou service deve chamar `requests.post` para a Mega-API diretamente — apenas `services/whatsapp_bot.py::_despachar_real`, chamado apenas pelo dispatcher (exceção documentada: testes manuais síncronos do painel, seção 3.1.4).
2. **Configuração dinâmica, nunca hardcoded**: todo parâmetro novo (delays, teto/hora, janela de opt-in, palavras-gatilho) segue o padrão `get_setting`/`Configuracao` já estabelecido em `services/config_service.py`.
3. **Templates centralizados**: textos ficam em `NotificationRule` / `Configuracao` (`BOT_MSG_DEFAULTS`), nunca hardcoded dentro de `.py` de blueprints, para permitir o lint da Fase 5.
4. **Retrocompatibilidade de assinatura**: `enviar_texto`, `enviar_botoes`, `enviar_menu_lista`, `enviar_documento`, `enviar_msg` mantêm suas assinaturas atuais — o Antigravity não deve alterar nenhum dos ~50 pontos de chamada existentes, exceto para adicionar os novos parâmetros opcionais (`prioridade`, `primeiro_contato`) com defaults que preservam o comportamento atual.
5. **Testes**: usar `test_batidas.py`/`test_api.py` como referência de estilo de teste já usado no projeto; criar `test_envio_dispatcher.py` cobrindo: rate-limit respeitado, jitter variável, Spintax resolvendo diferente a cada chamada, lint de primeiro contato bloqueando link.

---

## 5. Sequenciamento sugerido (idêntico à lógica do PRD de negócio, adaptado)

1. **Fase 0** — pré-requisito de tudo (sem `mega_message_id`/evento de conexão, não há como medir nada depois).
2. **Fase 1** — maior mudança estrutural (fila unificada); testar com um número de teste antes de ir a 100% (sem ambiente de homologação dedicado — ver decisão abaixo, testar direto com um número secundário/pessoal antes de aplicar às regras de produção).
3. **Fase 2 (revisada)** — baixíssimo risco, é só um parâmetro a mais no `envio_dispatcher.py` da Fase 1 (delay proporcional ao tamanho da mensagem); não depende de nenhum endpoint novo, pode entrar junto com a Fase 1.
4. **Fase 5** — pode ser adiantada em paralelo à Fase 1 (a validação de lint independe da fila existir).
5. **Fase 3** — Spintax, aplicar sobre os templates existentes.
6. **Fase 4** — com `requer_optin=True` como default (decisão já tomada, ver 3.4), deixa de depender de alinhamento prévio com RH; pode ser implementada em seguida à Fase 1, ajustando manualmente para `False` só as regras de resposta direta a ação do funcionário.
7. **Fase 6** — versão simples desde a Fase 0 (alerta de desconexão já é isso); versão completa (dashboard + circuito automático) ao final.

---

## 6. Riscos técnicos específicos deste código (além dos já listados no PRD de negócio)

| Risco | Mitigação |
|---|---|
| Migrar `NotificacaoFila` → `FilaEnvioWhatsapp` perder mensagens pendentes em produção | Migração de dados explícita copiando linhas antes de trocar de tabela; fazer backup do `instance/secullum.db` antes (o projeto já tem o hábito, ver `instance/secullum_backup_*.db` existentes) — sem ambiente de homologação, este backup é a rede de segurança antes da migração em produção. |
| Sem `WhatsappLog.status_webhook`, a Fase 6 mede saúde do envio só pelo `error`/sucesso síncrono do `POST`, não pela entrega/leitura real | Aceito como limitação conhecida do provedor (ver 0.1 item 7 e decisão da Fase 0); documentar isso claramente no dashboard da Fase 6 para não passar a falsa impressão de que "enviado" = "lido pelo destinatário". |
| Dezenas de pontos de chamada (`grep` encontrou ~50) dependendo do retorno booleano de `enviar_texto` | Manter o retorno `True` = "aceito na fila" (não "entregue"); nenhum chamador hoje trata o retorno como confirmação de entrega real, então essa mudança de semântica é segura — mas documentar isso claramente no docstring da função. |
| Ajuste do formato do `to` para `@s.whatsapp.net` (3.1.4.1) introduzir uma regressão de entrega não percebida imediatamente (sem ambiente de homologação para pegar isso antes) | Fazer o deploy dessa mudança específica isoladamente (não junto com o resto da Fase 1) e, nas primeiras horas, acompanhar de perto `WhatsappLog.status` (`erro_*`) para o volume normal do dia; ter um rollback simples (reverter só essa função) pronto caso a taxa de erro suba. |
| Sem ambiente de homologação, toda validação de comportamento da Mega-API (Fases 0, 1, 2, 5) precisa ser feita com cautela direto no número de produção ou com um número pessoal/secundário à parte | Priorizar testes com um número de teste pessoal do responsável técnico (não vinculado a nenhum funcionário) antes de qualquer rollout às regras reais; usar as rotas de "teste manual" já existentes no painel (`blueprints/config_hub.py`, `blueprints/whatsapp.py`) para essa validação pontual, mantendo o caminho síncrono (3.1.4). |

---

## 7. Perguntas em aberto para alinhar antes/durante a implementação

Todas as perguntas da versão anterior foram respondidas pelo negócio em 22/07/2026 (ver decisões incorporadas ao longo deste documento: 0.1, Fase 0, Fase 1, Fase 4). Não há bloqueios em aberto para iniciar a implementação. Único ponto que permanece como validação técnica de baixo risco, a ser feita já durante a execução (não antes dela):

- Confirmar em ambiente real, com um número de teste, o schema exato do payload de conexão/desconexão da instância (3.0.3) antes de finalizar a heurística de classificação em `processar_evento_instancia` — a documentação confirma que o evento existe, mas não detalha seus campos.
- O envio atual em produção usa o número sem sufixo (`5511999999999`) e funciona — vale confirmar com o suporte da Mega-API (ou testando) se isso é garantido para a instância contratada, já que toda a documentação oficial usa `@s.whatsapp.net` (ver 0.1 item 5).
