# Manual de Testes — Secullum Hub
**Ambiente:** Windows 11 | Python 3.13 | PostgreSQL | Redis

---

## 1. PRÉ-REQUISITOS

### 1.1 Serviços obrigatórios

Antes de qualquer coisa, confirme que estes dois serviços estão rodando:

**PostgreSQL**
```
# Verificar (deve retornar ":5432 - aceitando conexões")
pg_isready
```

**Redis** (necessário para Celery)

Redis não está no PATH deste computador. Opções:

- **Opção A – Docker (recomendado)**
  ```
  docker run -d -p 6379:6379 --name redis redis:alpine
  ```
- **Opção B – Redis para Windows**
  Baixe em: https://github.com/microsoftarchive/redis/releases
  Instale e inicie o serviço pelo `services.msc`

- **Opção C – Testar sem Celery**
  O Flask funciona normalmente sem Redis. Tarefas agendadas (sync, bot WhatsApp, alertas) não rodam, mas todas as telas funcionam.

---

## 2. INICIAR O SISTEMA

Abra **3 terminais** na pasta `c:\Users\ralan\secullum10`:

### Terminal 1 — Flask (interface web)
```bash
cd c:\Users\ralan\secullum10
python app.py
```
> Acesse: http://localhost:5010

### Terminal 2 — Celery Worker (tarefas em background)
```bash
cd c:\Users\ralan\secullum10
celery -A app.celery worker --loglevel=info --pool=solo
```
> `--pool=solo` é obrigatório no Windows

### Terminal 3 — Celery Beat (agendador)
```bash
cd c:\Users\ralan\secullum10
celery -A app.celery beat --loglevel=info
```
> Necessário apenas para testar tarefas agendadas (sync, bot, alertas)

---

## 3. CREDENCIAIS DE ACESSO

| Campo  | Valor                    |
|--------|--------------------------|
| URL    | http://localhost:5010    |
| Email  | admin@secullum10.com     |
| Senha  | Admin@123                |
| Perfil | gestor (acesso total)    |

---

## 4. ROTEIRO DE TESTES POR MÓDULO

---

### ETAPA 1 — Fundação (RF1.1 a RF1.6)

#### RF1.1 / RF1.2 — Banco de dados
```bash
# Verificar tabelas criadas (deve listar 12 tabelas)
python -c "
from app import create_app; app = create_app()
from sqlalchemy import inspect
from extensions import db
with app.app_context():
    for t in sorted(inspect(db.engine).get_table_names()): print(t)
"
```
**Esperado:** 12 tabelas incluindo `marketplace_turnos`, `prontuario_docs`, `feedbacks_aula`, `candidaturas`

#### RF1.5 / RF1.6 — Login e proteção de rotas
1. Acesse http://localhost:5010 sem estar logado → deve redirecionar para `/login`
2. Tente acessar http://localhost:5010/funcionarios sem login → redireciona para login
3. Faça login com `admin@secullum10.com` / `Admin@123`
4. Confirme redirecionamento para o dashboard

---

### ETAPA 2 — Escalas CLT (RF2.1 a RF2.6)

#### RF2.1 — Criar Turno
1. Acesse **Escalas** no menu lateral → clique **Novo Turno**
2. Preencha: Nome = `Turno Manhã`, Início = `08:00`, Fim = `17:00`, Dias = Seg a Sex
3. Salve → turno aparece na lista

#### RF2.2 — Alocar Funcionário
1. Em Escalas → clique **Alocar**
2. Selecione um funcionário, o turno criado, e a data de hoje
3. Clique **Salvar**

#### RF2.3 / RF2.4 — Validação CLT
Teste conflito de interjornada:
1. Aloque o mesmo funcionário no dia anterior com turno `22:00–06:00`
2. Tente alocar hoje com turno `08:00–17:00`
3. **Esperado:** erro JSON `{"error": "INTERJORNADA", "message": "...", "horas_encontradas": X}` — alocação não salva

#### RF2.5 — Divergências
- Acesse http://localhost:5010/escalas/divergencias
- **Esperado:** lista de funcionários escalados hoje sem batida registrada

#### RF2.6 — Card Ausências no Dashboard
- Acesse http://localhost:5010
- **Esperado:** 5 cards na faixa superior, incluindo **Ausências Hoje** com contagem (vermelho se > 0)
- Clicar no card leva para `/escalas/divergencias`

---

### ETAPA 3 — Banco de Horas (RF3.1 a RF3.6)

#### RF3.3 — Configurar regras
1. Acesse http://localhost:5010/config/banco-horas
2. Defina: Valor da hora = `R$ 25,00`, Limite alertas = `30 dias`
3. Salve

#### RF3.1 / RF3.2 — Calcular e visualizar saldo
1. Acesse http://localhost:5010/banco-horas
2. Selecione um funcionário e um período
3. Clique **Calcular** → tabela com Previsto / Realizado / Saldo Dia / Saldo Acumulado
4. Clique **Salvar Saldos** → saldo persiste no banco
5. Clique **Excel** → baixa arquivo `.xlsx`

#### RF3.4 — Alertas de vencimento
- Acesse http://localhost:5010/banco-horas/alertas
- Badge no sidebar (⚠️) aparece quando há saldos positivos com mais de 30 dias

#### RF3.5 — Dashboard Financeiro
1. Acesse http://localhost:5010/financeiro
2. **Esperado:** cards com Total HE do mês, Custo estimado (R$), variação vs mês anterior

#### RF3.6 — Simulador de custo
1. Em Escalas → Alocar → selecione turno
2. **Esperado:** modal exibe custo estimado calculado automaticamente ao trocar turno

---

### ETAPA 4 — WhatsApp / Mega-API (RF4.1 a RF4.6)

> **Atenção:** Esta etapa requer credenciais reais no `.env`:
> `MEGAAPI_TOKEN`, `MEGAAPI_INSTANCE`, `MEGAAPI_SECRET`, `GESTOR_CELULAR`
> Sem credenciais, simule via curl abaixo.

#### RF4.1 — Webhook (simulação)
```bash
# Simula mensagem recebida (sem validação HMAC em dev)
curl -X POST http://localhost:5010/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"message\",\"data\":{\"from\":\"5511999999999\",\"body\":\"SIM\",\"type\":\"text\"}}"
```
**Esperado:** resposta `{"status": "ok"}` em < 2s

#### RF4.2 — Bot de ausência (simulação manual)
```bash
# Dispara a task diretamente (com Celery rodando)
python -c "
from app import create_app; app = create_app()
with app.app_context():
    from tasks import bot_ausencia
    bot_ausencia.delay()
    print('Task enviada')
"
```

#### RF4.3 — Resposta SIM/NÃO
Envie via curl (veja RF4.1) com body `"SIM"` ou `"NÃO"`.
- SIM → `pre_checkin = True` na alocação do dia
- NÃO → gestor notificado no WhatsApp

#### RF4.4 — PDF Espelho
1. Acesse http://localhost:5010/espelho
2. Selecione funcionário e período
3. Clique **PDF** → baixa arquivo PDF com tabela de batidas
4. Clique **Enviar WhatsApp** → envia PDF para celular do funcionário (requer credenciais)

#### RF4.6 — Log de mensagens
- Acesse http://localhost:5010/whatsapp/logs
- **Esperado:** tabela com histórico de mensagens enviadas/recebidas

---

### ETAPA 5 — Módulos Avançados (RF5.1 a RF5.6)

#### RF5.1 / RF5.2 — Marketplace de Turnos
1. Acesse **Marketplace** no menu lateral
2. Clique **Nova Vaga** → preencha título, data, turno, valor/hora → Salve
3. A vaga aparece com status **ABERTO**
4. (Simule professor) — Clique **Candidatar-se** → status muda para **CANDIDATURA**
5. (Como gestor) — Clique **Aprovar**:
   - Sistema verifica conflitos CLT automaticamente
   - Se aprovado: cria alocação e status → **APROVADO**
   - Se conflito CLT: erro exibido, aprovação bloqueada

#### RF5.3 — Prontuário Digital
1. Acesse **Funcionários** → clique ícone 📁 de qualquer funcionário
2. Na tela do prontuário, faça upload de um PDF ou JPG (≤ 10 MB)
3. Defina data de vencimento (ex: 30 dias a partir de hoje)
4. Clique **Download** → arquivo baixado corretamente
5. Clique **Excluir** → documento removido

#### RF5.4 — Alertas de documentos
- Acesse http://localhost:5010/prontuario/alertas
- **Esperado:** lista de documentos com vencimento ≤ 30 dias
- Badge vermelho no sidebar quando há documentos pendentes

Disparar e-mail manualmente:
```bash
python -c "
from app import create_app; app = create_app()
with app.app_context():
    from tasks import alerta_documentos_vencendo
    alerta_documentos_vencendo.delay()
    print('Task enviada')
"
```
> Requer `MAIL_USERNAME`, `MAIL_PASSWORD` e `RH_EMAIL` preenchidos no `.env`

#### RF5.5 — Score de Pontualidade
1. Acesse **Funcionários** no menu lateral
2. **Esperado:** coluna **Pontualidade** com badges:
   - 🟢 Verde: ≥ 90%
   - 🟡 Amarelo: ≥ 70%
   - 🔴 Vermelho: < 70%
   - `—` sem escala cadastrada

#### RF5.6 — QR Code de Feedback
1. Acesse http://localhost:5010/qrcode/1 (substituir `1` por um ID de alocação real)
2. **Esperado:** imagem PNG com QR code
3. Escaneie o QR ou acesse http://localhost:5010/feedback/1
4. Preencha a nota (1–5 estrelas) e comentário → Salve
5. **Esperado:** página de agradecimento (rota pública, sem login)

Obter IDs de alocação válidos:
```bash
python -c "
from app import create_app; app = create_app()
with app.app_context():
    from models import AlocacaoDiaria
    for a in AlocacaoDiaria.query.limit(5).all():
        print(f'ID={a.id} | func={a.funcionario_id} | data={a.data}')
"
```

---

## 5. VERIFICAÇÃO RÁPIDA (checklist final)

```
[ ] Login e logout funcionam
[ ] Dashboard mostra 5 cards (incluindo Ausências Hoje)
[ ] /funcionarios lista funcionários com coluna Pontualidade
[ ] /escalas/ — criar turno e alocar funcionário
[ ] /banco-horas — calcular saldo e exportar Excel
[ ] /financeiro — custo estimado de HE
[ ] /whatsapp/logs — tabela de logs visível
[ ] /marketplace/ — criar vaga e candidatar
[ ] /prontuario/<id> — upload e download de arquivo
[ ] /prontuario/alertas — lista de documentos vencendo
[ ] /qrcode/<id> — gera imagem PNG
[ ] /feedback/<id> — formulário acessível sem login
```

---

## 6. PROBLEMAS COMUNS

| Erro | Causa | Solução |
|------|-------|---------|
| `Connection refused 6379` | Redis não está rodando | Inicie Redis (Docker ou serviço) |
| `FATAL: password authentication failed` | Senha do PostgreSQL errada | Verifique `DATABASE_URL` no `.env` |
| `ModuleNotFoundError: No module named 'X'` | Dependência faltando | `pip install X` |
| Celery não processa tasks | Worker não iniciado | Abra Terminal 2 com `celery worker` |
| PDF não gera | ReportLab não instalado | `pip install reportlab` |
| Upload retorna 413 | Arquivo > 10 MB | Use arquivo menor |
| Badge sidebar não aparece | Contexto de usuário não autenticado | Faça login primeiro |

---

## 7. VARIÁVEIS DE AMBIENTE (`.env`)

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `DATABASE_URL` | ✅ Sim | URL do PostgreSQL |
| `SECRET_KEY` | ✅ Sim | Chave de sessão Flask |
| `REDIS_URL` | ✅ Para Celery | URL do Redis |
| `SECULLUM_EMAIL` | ✅ Para sync | Login da API Secullum |
| `SECULLUM_PASSWORD` | ✅ Para sync | Senha da API Secullum |
| `SECULLUM_BANCO` | ✅ Para sync | ID do banco Secullum |
| `MEGAAPI_TOKEN` | ⚡ Para WhatsApp | Token da Mega-API |
| `MEGAAPI_INSTANCE` | ⚡ Para WhatsApp | Instância WhatsApp |
| `MEGAAPI_SECRET` | ⚡ Para webhook | Segredo HMAC |
| `GESTOR_CELULAR` | ⚡ Para WhatsApp | Celular do gestor (55+DDD+número) |
| `OPENAI_API_KEY` | ⚡ Opcional | Transcrição de áudio (Whisper) |
| `MAIL_USERNAME` | ⚡ Para e-mail | Gmail ou SMTP |
| `MAIL_PASSWORD` | ⚡ Para e-mail | Senha de app Gmail |
| `RH_EMAIL` | ⚡ Para e-mail | Destinatário dos alertas |
