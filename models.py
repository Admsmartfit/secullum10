from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class Usuario(UserMixin, db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    nivel_acesso = db.Column(db.String(20), default='funcionario')  # administrador / gerente / funcionario
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha, method='pbkdf2:sha256')

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def __repr__(self):
        return f'<Usuario {self.email} ({self.nivel_acesso})>'


class Funcionario(db.Model):
    __tablename__ = 'funcionarios'
    id = db.Column(db.String(50), primary_key=True)  # ID da Secullum
    nome = db.Column(db.String(200), nullable=False)

    # Documentos
    pis = db.Column(db.String(20))
    cpf = db.Column(db.String(20))
    rg = db.Column(db.String(20))
    carteira = db.Column(db.String(50))

    # Contatos
    email = db.Column(db.String(200))
    celular = db.Column(db.String(20))
    telefone = db.Column(db.String(20))

    # Endereço
    endereco = db.Column(db.String(300))
    bairro = db.Column(db.String(100))
    cidade = db.Column(db.String(100))
    uf = db.Column(db.String(2))
    cep = db.Column(db.String(10))

    # Informações profissionais
    departamento = db.Column(db.String(200))
    funcao = db.Column(db.String(200))
    numero_folha = db.Column(db.String(50))
    numero_identificador = db.Column(db.String(50))

    # Datas
    admissao = db.Column(db.Date)
    demissao = db.Column(db.Date)
    nascimento = db.Column(db.Date)
 
    # ── PRD "War Room": Horário Base ──────────────────────────────────────────
    # Define o turno padrão do funcionário. Se não houver AlocacaoDiaria (exceção),
    # o sistema usará este turno para compor a Escala/Realidade.
    horario_base_id = db.Column(db.Integer, db.ForeignKey('turnos.id'), nullable=True)
    horario_base = db.relationship('Turno', foreign_keys=[horario_base_id])
 
    # Horário Secullum (schedule assigned via API)
    horario_secullum_numero = db.Column(db.Integer, nullable=True)
    horario_secullum_nome = db.Column(db.String(100), nullable=True)

    # Sexo: 'M' = masculino, 'F' = feminino (usado p/ regra Art. 386 CLT)
    sexo = db.Column(db.String(1), nullable=True)
    # Estado civil (preenchido manualmente pelo RH)
    estado_civil = db.Column(db.String(30), nullable=True)

    # Status e controles
    ativo = db.Column(db.Boolean, default=True)
    data_ultima_sincronizacao = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamento com batidas
    batidas = db.relationship('Batida', backref='funcionario', lazy='dynamic')

    def __repr__(self):
        return f'<Funcionario {self.nome}>'


class Batida(db.Model):
    __tablename__ = 'batidas'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Relacionamento com funcionário
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)

    # Dados da batida
    data = db.Column(db.Date, nullable=False)
    hora = db.Column(db.String(10), nullable=False)
    data_hora = db.Column(db.DateTime)

    # Informações adicionais
    tipo = db.Column(db.String(50))     # Entrada/Saída
    origem = db.Column(db.String(100))  # REP, App, Manual, etc
    inconsistente = db.Column(db.Boolean, default=False)
    justificativa = db.Column(db.Text)
    justificada_via = db.Column(db.String(50)) # Bot, Portal Web, RH

    # Localização (se disponível)
    latitude = db.Column(db.String(20))
    longitude = db.Column(db.String(20))

    # Integração Secullum (fonte_dados para rastreabilidade)
    secullum_id = db.Column(db.String(50))
    fonte_dados = db.Column(db.String(50))

    # Controle
    data_sincronizacao = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_funcionario_data', 'funcionario_id', 'data'),
        db.Index('idx_data', 'data'),
        db.UniqueConstraint('funcionario_id', 'data', 'hora', name='uq_batida'),
    )

    def __repr__(self):
        return f'<Batida {self.funcionario_id} em {self.data} as {self.hora}>'


class Configuracao(db.Model):
    __tablename__ = 'configuracoes'
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(50), unique=True)
    valor = db.Column(db.String(255))


# ── Etapa 2: Escalas ──────────────────────────────────────────────────────────

class Turno(db.Model):
    __tablename__ = 'turnos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    hora_inicio = db.Column(db.Time, nullable=False)
    hora_fim = db.Column(db.Time, nullable=False)
    # dias_semana: lista de ints 0=seg..6=dom, armazenada como string "0,1,2"
    dias_semana = db.Column(db.String(20), default='0,1,2,3,4')
    intervalo_minutos = db.Column(db.Integer, default=60)  # Descanso em minutos (15, 60, etc.)
    # dias_complexos_json: { "0": {"inicio": "08:00", "fim": "17:00", "intervalo": 60}, ... }
    dias_complexos_json = db.Column(db.Text, nullable=True)
    # Escopo: departamento (unidade/CNPJ) ao qual o turno pertence. Null = global.
    departamento = db.Column(db.String(200), nullable=True)
    # Cargo/Função específico para este turno (ex: Recepcionista, Professor). Null = todos.
    funcao = db.Column(db.String(100), nullable=True)
    # Cor hexadecimal para o calendário
    color = db.Column(db.String(7), nullable=True, default='#4f46e5')
    # Tipo de turno: 'A' = Abridor, 'B' = Fechador, 'C' = Tático, None = não classificado
    tipo_turno = db.Column(db.String(1), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    alocacoes = db.relationship('AlocacaoDiaria', backref='turno', lazy='dynamic', cascade="all, delete-orphan")
    vagas_marketplace = db.relationship('MarketplaceTurno', backref='turno', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def dias_complexos(self):
        import json
        if self.dias_complexos_json:
            try:
                return json.loads(self.dias_complexos_json)
            except Exception:
                return {}
        return {}

    def get_horario_dia(self, dia_semana: int):
        """Retorna (inicio, fim, intervalo) para o dia da semana (0-6)."""
        complexos = self.dias_complexos
        dia_str = str(dia_semana)
        if dia_str in complexos:
            d = complexos[dia_str]
            from datetime import datetime as dt
            return (
                dt.strptime(d['inicio'], '%H:%M').time(),
                dt.strptime(d['fim'], '%H:%M').time(),
                d.get('intervalo', self.intervalo_minutos)
            )
        return (self.hora_inicio, self.hora_fim, self.intervalo_minutos)

    @property
    def dias_semana_list(self):
        return [int(d) for d in self.dias_semana.split(',') if d.strip()]

    @property
    def duracao_horas(self):
        # Para escalas complexas, a duração pode variar por dia. 
        # Esta propriedade retorna a duração média ou base.
        from datetime import datetime as dt
        inicio = dt.combine(dt.today(), self.hora_inicio)
        fim = dt.combine(dt.today(), self.hora_fim)
        if fim < inicio:
            from datetime import timedelta
            fim += timedelta(days=1)
        duracao = (fim - inicio).seconds / 3600
        return max(0, duracao - (self.intervalo_minutos / 60))

    def duracao_horas_no_dia(self, data_ref):
        """Calcula duração exata considerando o dia específico."""
        h_ini, h_fim, intervalo = self.get_horario_dia(data_ref.weekday())
        from datetime import datetime as dt, timedelta
        inicio = dt.combine(data_ref, h_ini)
        fim = dt.combine(data_ref, h_fim)
        if fim < inicio:
            fim += timedelta(days=1)
        duracao = (fim - inicio).seconds / 3600
        return max(0, duracao - (intervalo / 60))

    def __repr__(self):
        return f'<Turno {self.nome}>'


class AlocacaoDiaria(db.Model):
    __tablename__ = 'alocacoes_diarias'
    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)
    turno_id = db.Column(db.Integer, db.ForeignKey('turnos.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    pre_checkin = db.Column(db.Boolean, default=False)
    # Aviso de compliance armazenado (não-bloqueante)
    compliance_warning = db.Column(db.Text, nullable=True)
    # Marcador de exceção manual (Se True, ignora o Horário Base do funcionário)
    is_excecao = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref='alocacoes')

    __table_args__ = (
        db.UniqueConstraint('funcionario_id', 'data', name='uq_alocacao'),
        db.Index('idx_alocacao_data', 'data'),
    )

    def __repr__(self):
        return f'<AlocacaoDiaria {self.funcionario_id} em {self.data}>'


# ── Etapa 3: Banco de Horas ───────────────────────────────────────────────────

class BancoHorasSaldo(db.Model):
    __tablename__ = 'banco_horas_saldo'
    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    horas_previstas = db.Column(db.Numeric(5, 2), default=0)
    horas_realizadas = db.Column(db.Numeric(5, 2), default=0)
    saldo_dia = db.Column(db.Numeric(5, 2), default=0)
    saldo_acumulado = db.Column(db.Numeric(6, 2), default=0)

    funcionario = db.relationship('Funcionario', backref='saldos_banco_horas')

    __table_args__ = (
        db.UniqueConstraint('funcionario_id', 'data', name='uq_saldo'),
        db.Index('idx_saldo_data', 'data'),
    )


# ── Etapa 4: WhatsApp ─────────────────────────────────────────────────────────

class WhatsappLog(db.Model):
    __tablename__ = 'whatsapp_logs'
    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)
    tipo = db.Column(db.String(50))      # saida / entrada / checkin / espelho / regra
    tipo_regra = db.Column(db.String(50), nullable=True)  # LATE_ENTRY, etc.
    data_referencia = db.Column(db.Date, nullable=True)   # Data da ocorrência
    mensagem = db.Column(db.Text)
    status = db.Column(db.String(120), default='enviado')   # enviado / erro / recebido / "erro: <detalhe>"
    celular = db.Column(db.String(20))
    # PRD Antiban Fase 0: id retornado pela Mega-API no envio (uso futuro: responder/marcar/excluir mensagem)
    mega_message_id = db.Column(db.String(100), nullable=True)
    atualizado_em = db.Column(db.DateTime, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref='whatsapp_logs')


class WhatsappBlacklist(db.Model):
    """Migração Evolution API: bloqueio absoluto e global de opt-out. Qualquer
    número aqui presente é rejeitado em TODO envio (fila, imediato=True,
    pergunta de opt-in), sem exceção de cargo/regra — ver
    services/whatsapp_bot.py::_bloqueado."""
    __tablename__ = 'whatsapp_blacklist'
    id = db.Column(db.Integer, primary_key=True)
    celular = db.Column(db.String(20), unique=True, nullable=False, index=True)
    motivo = db.Column(db.String(50), default='OPT_OUT')
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<WhatsappBlacklist {self.celular} ({self.motivo})>'


class MegaApiInstanceEvent(db.Model):
    """PRD Antiban Fase 0: eventos de conexão/desconexão da instância Mega-API,
    capturados no mesmo webhook que recebe mensagens (a Mega-API só permite
    configurar uma única webhookUrl por instância)."""
    __tablename__ = 'megaapi_instance_events'
    id = db.Column(db.Integer, primary_key=True)
    tipo_evento = db.Column(db.String(50))   # 'connected' / 'disconnected' / 'qr_needed' / 'desconhecido'
    payload_raw = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<MegaApiInstanceEvent {self.tipo_evento} em {self.criado_em}>'


# ── Etapa 5: Marketplace ───────────────────────────────────────────────────────

class MarketplaceTurno(db.Model):
    __tablename__ = 'marketplace_turnos'
    id = db.Column(db.Integer, primary_key=True)
    gestor_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    data = db.Column(db.Date, nullable=False)
    turno_id = db.Column(db.Integer, db.ForeignKey('turnos.id'), nullable=False)
    valor_hora = db.Column(db.Numeric(8, 2), default=0)
    # aberto / candidatura / aprovado / cancelado
    status = db.Column(db.String(20), default='aberto')
    descricao = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    gestor = db.relationship('Usuario', backref='vagas_criadas')
    # Relacionamento 'turno' é definido via backref em Turno.vagas_marketplace
    candidaturas = db.relationship('Candidatura', backref='vaga', lazy='dynamic')


class Candidatura(db.Model):
    __tablename__ = 'candidaturas'
    id = db.Column(db.Integer, primary_key=True)
    marketplace_id = db.Column(db.Integer, db.ForeignKey('marketplace_turnos.id'), nullable=False)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)
    # pendente / aprovado / rejeitado
    status = db.Column(db.String(20), default='pendente')
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref='candidaturas')

    __table_args__ = (
        db.UniqueConstraint('marketplace_id', 'funcionario_id', name='uq_candidatura'),
    )


# ── Etapa 5: Prontuário Digital ───────────────────────────────────────────────

class ProntuarioDoc(db.Model):
    __tablename__ = 'prontuario_docs'
    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)
    tipo = db.Column(db.String(100))          # ASO / Curso / CNH / Outro
    nome_arquivo = db.Column(db.String(300))
    arquivo_path = db.Column(db.Text)
    data_vencimento = db.Column(db.Date)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref='documentos')

    __table_args__ = (
        db.Index('idx_doc_vencimento', 'data_vencimento'),
    )


# ── Fase 4: Motor de Regras de Notificação WhatsApp ──────────────────────────

class NotificationRule(db.Model):
    __tablename__ = 'notification_rules'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    ativo = db.Column(db.Boolean, default=True)

    # Trigger: EVENT_SYNC | EVENT_ABSENCE | DAILY | WEEKLY
    trigger_type    = db.Column(db.String(50), nullable=False, default='EVENT_SYNC')
    trigger_hour    = db.Column(db.Integer, nullable=True, default=8)     # hour for DAILY/WEEKLY
    trigger_weekday = db.Column(db.Integer, nullable=True, default=4)     # 0=Mon … 6=Sun

    # Categoria UI: geral | bot | alerta | fechamento
    categoria = db.Column(db.String(30), nullable=True, default='alerta')

    # Condition: LATE_ENTRY | EARLY_LEAVE | ABSENCE | OVERTIME | INTERJORNADA | ESCALA_ENVIO
    condition_type      = db.Column(db.String(50), nullable=False, default='LATE_ENTRY')
    threshold_minutes   = db.Column(db.Integer, nullable=True, default=15)

    # Recipients
    dest_employee = db.Column(db.Boolean, default=False)
    dest_manager  = db.Column(db.Boolean, default=True)
    dest_rh       = db.Column(db.Boolean, default=False)
    dest_custom   = db.Column(db.Boolean, default=False)
    custom_phone  = db.Column(db.String(20), nullable=True)

    # Message templates (support variables: {name} {full_name} {minutes} {turno} {inicio} {fim} {data})
    template_manager  = db.Column(db.Text, nullable=True)
    template_employee = db.Column(db.Text, nullable=True)
    # Tipo de mensagem interativa por destinatário: texto | botoes | lista
    template_employee_tipo = db.Column(db.String(20), default='texto', nullable=True)
    template_employee_interativo = db.Column(db.Text, nullable=True)
    template_manager_tipo  = db.Column(db.String(20), default='texto', nullable=True)
    template_manager_interativo  = db.Column(db.Text, nullable=True)

    # Constraints
    only_working_hours = db.Column(db.Boolean, default=True)
    send_immediately   = db.Column(db.Boolean, default=False)

    # PRD Antiban Fase 4: opt-in conversacional — default True para toda regra
    # nova (blindagem contra denúncia é a prioridade; RH desmarca manualmente
    # só para regras que já são resposta direta a uma ação do funcionário).
    requer_optin       = db.Column(db.Boolean, default=True)
    optin_janela_horas = db.Column(db.Integer, default=24)
    # 'enviar' | 'reenviar_pergunta' | 'cancelar'
    optin_fallback     = db.Column(db.String(20), default='enviar')

    # Stats
    criado_em         = db.Column(db.DateTime, default=datetime.utcnow)
    ultima_execucao   = db.Column(db.DateTime, nullable=True)
    mensagens_enviadas = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<NotificationRule {self.nome} ({self.condition_type})>'


# ── Módulo de Configuração: Unidades / Líderes ────────────────────────────────

class UnidadeLider(db.Model):
    __tablename__ = 'unidades_lideres'
    id = db.Column(db.Integer, primary_key=True)
    departamento = db.Column(db.String(200), nullable=False, unique=True)
    nome_unidade = db.Column(db.String(200))
    celular_lider = db.Column(db.String(20))
    lider_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    lider = db.relationship('Usuario', backref='unidades_lider')

    # Dados da empresa responsável pelo departamento
    empresa_nome      = db.Column(db.String(300))
    empresa_cnpj      = db.Column(db.String(30))
    empresa_socio     = db.Column(db.String(300))
    socio_cpf         = db.Column(db.String(20))
    empresa_endereco  = db.Column(db.String(400))
    empresa_cidade    = db.Column(db.String(200))
    empresa_uf        = db.Column(db.String(5))
    empresa_cep       = db.Column(db.String(15))
    cidade_ibge       = db.Column(db.String(10), nullable=True)  # código IBGE (ex: 3205200 = Vila Velha)
    experiencia_dias  = db.Column(db.Integer, default=45)

    def __repr__(self):
        return f'<UnidadeLider {self.departamento}>'


# ── Etapa 5: Feedback de Aula ─────────────────────────────────────────────────

class FeedbackAula(db.Model):
    __tablename__ = 'feedbacks_aula'
    id = db.Column(db.Integer, primary_key=True)
    alocacao_id = db.Column(db.Integer, db.ForeignKey('alocacoes_diarias.id'), nullable=False)
    nota = db.Column(db.Integer)              # 1–5
    comentario = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    alocacao = db.relationship('AlocacaoDiaria', backref='feedbacks')


# ── Horários Secullum (cache da API) ──────────────────────────────────────────

class HorarioSecullum(db.Model):
    """Cache dos horários vindos da API Secullum.
    dias_json: dict serializado {dia_semana_str: {entrada, saida, tipo}}
    onde dia_semana 0=Segunda … 6=Domingo (igual ao weekday() do Python).
    """
    __tablename__ = 'horarios_secullum'
    numero = db.Column(db.Integer, primary_key=True)  # HorarioNumero da API
    descricao = db.Column(db.String(100))
    dias_json = db.Column(db.Text)  # JSON {dia: {entrada, saida, tipo}}
    sincronizado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<HorarioSecullum {self.numero} – {self.descricao}>'


# ── Fase PRD: Solicitação de Troca de Turno ───────────────────────────────────

class SolicitacaoTroca(db.Model):
    """Pedido de troca de turno entre dois funcionários, com aprovação do gestor."""
    __tablename__ = 'solicitacoes_troca'
    id = db.Column(db.Integer, primary_key=True)

    # Quem solicita e qual alocação quer abrir mão
    solicitante_id   = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)
    alocacao_origem_id = db.Column(db.Integer, db.ForeignKey('alocacoes_diarias.id'), nullable=False)

    # Quem aceita e qual alocação irá receber (preenchidos na aceitação)
    candidato_id     = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)
    alocacao_destino_id = db.Column(db.Integer, db.ForeignKey('alocacoes_diarias.id'), nullable=True)

    # PENDENTE → AGUARDANDO_APROVACAO → APROVADO / REJEITADO
    status = db.Column(db.String(30), default='PENDENTE', nullable=False)
    obs_solicitante = db.Column(db.Text, nullable=True)
    obs_gestor      = db.Column(db.Text, nullable=True)

    criado_em      = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solicitante   = db.relationship('Funcionario', foreign_keys=[solicitante_id],
                                    backref='trocas_solicitadas')
    candidato     = db.relationship('Funcionario', foreign_keys=[candidato_id],
                                    backref='trocas_candidato')
    alocacao_origem  = db.relationship('AlocacaoDiaria', foreign_keys=[alocacao_origem_id])
    alocacao_destino = db.relationship('AlocacaoDiaria', foreign_keys=[alocacao_destino_id])

    def __repr__(self):
        return f'<SolicitacaoTroca {self.id} [{self.status}]>'


# ── PRD: Padrões de Revezamento ───────────────────────────────────────────────

class PadraoTurno(db.Model):
    """Template de ciclo de trabalho: ex. 6x1 (6 dias on, 1 folga), 5x2, etc."""
    __tablename__ = 'padroes_turno'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)        # "6x1", "5x2 Fixo Dom"
    descricao = db.Column(db.Text, nullable=True)
    dias_trabalho = db.Column(db.Integer, default=5)        # dias consecutivos ON
    dias_folga    = db.Column(db.Integer, default=2)        # dias consecutivos OFF no ciclo
    turno_id      = db.Column(db.Integer, db.ForeignKey('turnos.id'), nullable=True)
    departamento  = db.Column(db.String(200), nullable=True)  # null = global
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    turno = db.relationship('Turno', backref='padroes')

    def __repr__(self):
        return f'<PadraoTurno {self.nome} {self.dias_trabalho}x{self.dias_folga}>'


# ── Grupos de Departamentos ────────────────────────────────────────────────────

class GrupoDepartamento(db.Model):
    """Agrupa unidades do mesmo endereço/franquia para filtros e turnos compartilhados.
    Exemplo: "Praia do Canto" → ["PRAIA FITNESS", "FUNCIONAL DA PRAIA"]
    """
    __tablename__ = 'grupos_departamento'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False, unique=True)
    # JSON list de nomes de departamento que fazem parte do grupo
    departamentos_json = db.Column(db.Text, nullable=False, default='[]')
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def departamentos(self) -> list:
        import json
        try:
            return json.loads(self.departamentos_json)
        except Exception:
            return []

    @departamentos.setter
    def departamentos(self, value: list):
        import json
        self.departamentos_json = json.dumps(value, ensure_ascii=False)

    def __repr__(self):
        return f'<GrupoDepartamento {self.nome}>'


# ── Módulo de Documentos Contratuais ─────────────────────────────────────────

class TemplateDocumento(db.Model):
    """Template .docx armazenado em storage/templates/.
    Tags suportadas: {{nome_funcionario}}, {{cpf_funcionario}}, {{empresa_nome}}, etc.
    """
    __tablename__ = 'template_documentos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)          # Nome exibido na UI
    descricao = db.Column(db.Text, nullable=True)
    arquivo_nome = db.Column(db.String(300), nullable=False)  # Filename em storage/templates/
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<TemplateDocumento {self.nome}>'


class EnvioDocumento(db.Model):
    """Log de envios realizados (auditoria LGPD)."""
    __tablename__ = 'envios_documento'
    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)
    email_destinatario = db.Column(db.String(200), nullable=False)
    templates_enviados = db.Column(db.Text)       # JSON list de nomes
    enviado_por_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref='envios_documento')
    enviado_por = db.relationship('Usuario', backref='envios_realizados')

    def __repr__(self):
        return f'<EnvioDocumento func={self.funcionario_id} para={self.email_destinatario}>'


# ── PRD: Fila de Notificações (Direito à Desconexão) ─────────────────────────

class NotificacaoFila(db.Model):
    """Mensagens aguardando janela de expediente para serem enviadas.
    Implementa o Direito à Desconexão: notificações fora do horário de trabalho
    ficam enfileiradas e são enviadas no início do próximo turno do destinatário.
    """
    __tablename__ = 'notificacao_fila'
    id = db.Column(db.Integer, primary_key=True)
    regra_id = db.Column(db.Integer, db.ForeignKey('notification_rules.id'), nullable=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)
    celular = db.Column(db.String(20), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(50))                   # regra / relatorio
    tipo_regra = db.Column(db.String(50), nullable=True)
    data_referencia = db.Column(db.Date, nullable=True)
    # Enviar somente após este timestamp (próximo início de turno)
    enviar_apos = db.Column(db.DateTime, nullable=True)
    # pendente / enviado / erro / cancelado
    status = db.Column(db.String(20), default='pendente')
    tentativas = db.Column(db.Integer, default=0)
    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    enviado_em = db.Column(db.DateTime, nullable=True)

    regra = db.relationship('NotificationRule', backref='fila')
    funcionario = db.relationship('Funcionario', backref='notificacoes_fila')

    def __repr__(self):
        return f'<NotificacaoFila {self.id} [{self.status}] enviar_apos={self.enviar_apos}>'


class FilaEnvioWhatsapp(db.Model):
    """PRD Antiban Fase 1: camada única de envio de WhatsApp.
    Generaliza NotificacaoFila (que fica preservada, sem uso ativo, como
    histórico/rede de segurança) para TODO envio do sistema, não só os que
    caem fora do expediente. Todo envio passa por aqui e é despachado por
    services/envio_dispatcher.py, que aplica delay/jitter/rate-limit.
    """
    __tablename__ = 'fila_envio_whatsapp'
    id = db.Column(db.Integer, primary_key=True)
    regra_id = db.Column(db.Integer, db.ForeignKey('notification_rules.id'), nullable=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)
    celular = db.Column(db.String(20), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(50))                   # saida / regra / relatorio / manual / ...
    tipo_regra = db.Column(db.String(50), nullable=True)
    tipo_msg = db.Column(db.String(20), default='texto')       # texto / botoes / lista / documento
    interativo_json = db.Column(db.Text, nullable=True)
    anexo_ref = db.Column(db.String(255), nullable=True)       # reservado (documentos futuros)
    data_referencia = db.Column(db.Date, nullable=True)
    prioridade = db.Column(db.Integer, default=10)              # menor = mais prioritário
    primeiro_contato = db.Column(db.Boolean, default=False)     # reservado (lint futuro, Fase 5)
    enviar_apos = db.Column(db.DateTime, nullable=True)
    # pendente / processando / enviado / erro / cancelado
    status = db.Column(db.String(20), default='pendente')
    tentativas = db.Column(db.Integer, default=0)
    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    enviado_em = db.Column(db.DateTime, nullable=True)

    regra = db.relationship('NotificationRule', backref='fila_envio')
    funcionario = db.relationship('Funcionario', backref='fila_envio_whatsapp')

    def __repr__(self):
        return f'<FilaEnvioWhatsapp {self.id} [{self.status}] enviar_apos={self.enviar_apos}>'


class BotKeywordRule(db.Model):
    """Regra de resposta automática baseada em palavra-chave.
    O robô verifica estas regras antes de qualquer outra lógica.
    """
    __tablename__ = 'bot_keyword_rules'
    id = db.Column(db.Integer, primary_key=True)
    # Palavra-chave ou frase (case-insensitive)
    keyword = db.Column(db.String(100), nullable=False)
    # Texto principal da resposta; suporta {{nome}}, {{data}}, {{turno}}
    resposta = db.Column(db.Text, nullable=False)
    # Tipo de mensagem: texto | botoes | lista
    tipo_msg = db.Column(db.String(20), default='texto', nullable=False)
    # JSON com botões ou seções (quando tipo_msg != 'texto')
    interativo_json = db.Column(db.Text, nullable=True)
    # Se True, a resposta só é enviada para o funcionário (não encaminha ao gestor)
    apenas_funcionario = db.Column(db.Boolean, default=True)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<BotKeywordRule "{self.keyword}">'


class ChatState(db.Model):
    """Máquina de estados da conversa WhatsApp por funcionário.
    Permite ao bot saber "o que estava perguntando" quando chega uma resposta.
    """
    __tablename__ = 'chat_states'
    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False, unique=True)
    # Estado atual: IDLE | AGUARDANDO_ATESTADO | AGUARDANDO_MINUTOS_ATRASO | AGUARDANDO_AUSENCIA
    estado = db.Column(db.String(50), default='IDLE', nullable=False)
    # JSON livre para guardar contexto (ex: {"turno_id": 5, "data": "2025-06-01"})
    contexto = db.Column(db.Text, nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref=db.backref('chat_state', uselist=False))

    def __repr__(self):
        return f'<ChatState {self.funcionario_id} [{self.estado}]>'


class TabelaSalarial(db.Model):
    """Benefícios por função, configurados pelo administrador.
    Tags: {{salario}}, {{auxilio_alimentacao}}, {{premiacao}} e variantes _extenso.
    """
    __tablename__ = 'tabela_salarial'
    id = db.Column(db.Integer, primary_key=True)
    funcao = db.Column(db.String(200), nullable=False, unique=True)
    salario = db.Column(db.Numeric(10, 2), nullable=True)
    auxilio_alimentacao = db.Column(db.Numeric(10, 2), nullable=True)
    premiacao = db.Column(db.Numeric(10, 2), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<TabelaSalarial {self.funcao} = {self.salario}>'


class Feriado(db.Model):
    __tablename__ = 'feriados'
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False)
    descricao = db.Column(db.String(200))
    # nacional | estadual | municipal | personalizado
    tipo = db.Column(db.String(20), default='personalizado', nullable=False)
    uf = db.Column(db.String(2), nullable=True)
    cidade_ibge = db.Column(db.String(10), nullable=True)
    fonte = db.Column(db.String(50), nullable=True)   # brasilapi / calendario / holidays / manual
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    criado_por_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    criado_por = db.relationship('Usuario', backref='feriados_criados')

    def __repr__(self):
        return f'<Feriado {self.data} [{self.tipo}]: {self.descricao}>'


# ── Módulo Avaliação 360° ──────────────────────────────────────────────────────

class CicloAvaliacao(db.Model):
    """Um ciclo completo de avaliação 360° para um departamento/unidade.
    Disparado aleatoriamente entre 30 e 90 dias após o ciclo anterior.
    """
    __tablename__ = 'ciclos_avaliacao'
    id = db.Column(db.Integer, primary_key=True)
    departamento = db.Column(db.String(200), nullable=True)   # None = todos os departamentos
    data_inicio = db.Column(db.Date, nullable=False)
    data_fim_coleta = db.Column(db.Date, nullable=False)      # data_inicio + 3 dias (72h)
    # ativo | fechado | inconclusivo | cancelado
    status = db.Column(db.String(20), default='ativo', nullable=False)
    proximo_ciclo_data = db.Column(db.Date, nullable=True)    # próxima data sorteada
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    fechado_em = db.Column(db.DateTime, nullable=True)

    tokens = db.relationship('TokenAvaliacao', backref='ciclo', lazy=True,
                             cascade='all, delete-orphan')
    scores = db.relationship('ScoreAvaliacao', backref='ciclo', lazy=True,
                              cascade='all, delete-orphan')

    def __repr__(self):
        return f'<CicloAvaliacao {self.id} [{self.status}] {self.data_inicio}>'


class AlunoUnidade(db.Model):
    """Base de alunos importados via CSV/Excel para disparo de pesquisa.
    Cada aluno está vinculado a um horário/turno de frequência.
    """
    __tablename__ = 'alunos_unidade'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    celular = db.Column(db.String(20), nullable=False)
    # horário de frequência — deve coincidir com o turno do professor avaliado
    horario = db.Column(db.String(100), nullable=True)
    departamento = db.Column(db.String(200), nullable=True)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AlunoUnidade {self.nome} [{self.horario}]>'


class TokenAvaliacao(db.Model):
    """Token único para cada respondente em um ciclo.
    O link /r/<token> é enviado via WhatsApp e não exige login.
    """
    __tablename__ = 'tokens_avaliacao'
    id = db.Column(db.Integer, primary_key=True)
    ciclo_id = db.Column(db.Integer, db.ForeignKey('ciclos_avaliacao.id'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)  # UUID hex

    # Tipo de avaliação:
    # professor_por_gerente  → gerente avalia o professor   (peso 40%)
    # par_por_professor      → colega avalia o professor    (peso 30%)
    # aluno_por_equipe       → aluno avalia a equipe        (peso 30%)
    # gerente_por_professor  → professor avalia o gerente   (score separado)
    tipo = db.Column(db.String(30), nullable=False)

    # Quem está sendo avaliado (funcionario_id)
    avaliado_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)
    # Quem responde (funcionario_id — null para alunos)
    avaliador_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=True)

    # Dados do avaliador quando não é funcionário (aluno)
    avaliador_nome = db.Column(db.String(200), nullable=True)
    avaliador_celular = db.Column(db.String(20), nullable=True)

    respondido = db.Column(db.Boolean, default=False)
    respondido_em = db.Column(db.DateTime, nullable=True)
    enviado_em = db.Column(db.DateTime, nullable=True)
    lembrete_24h_em = db.Column(db.DateTime, nullable=True)
    lembrete_48h_em = db.Column(db.DateTime, nullable=True)
    expira_em = db.Column(db.DateTime, nullable=True)  # data_inicio + 72h

    avaliado = db.relationship('Funcionario', foreign_keys=[avaliado_id],
                               backref=db.backref('tokens_recebidos', lazy=True))
    avaliador = db.relationship('Funcionario', foreign_keys=[avaliador_id],
                                backref=db.backref('tokens_enviados', lazy=True))
    respostas = db.relationship('RespostaAvaliacao', backref='token_obj', lazy=True,
                                cascade='all, delete-orphan')

    def __repr__(self):
        return f'<TokenAvaliacao {self.token[:8]}… tipo={self.tipo} respondido={self.respondido}>'


class RespostaAvaliacao(db.Model):
    """Resposta individual de uma questão (nota Likert 1-5) por token."""
    __tablename__ = 'respostas_avaliacao'
    id = db.Column(db.Integer, primary_key=True)
    token_id = db.Column(db.Integer, db.ForeignKey('tokens_avaliacao.id'), nullable=False)
    questao_numero = db.Column(db.Integer, nullable=False)  # 1 a 4 conforme banco de perguntas
    nota = db.Column(db.Integer, nullable=False)            # 1 (Nunca) a 5 (Sempre)

    def __repr__(self):
        return f'<Resposta token={self.token_id} q={self.questao_numero} nota={self.nota}>'


class ScoreAvaliacao(db.Model):
    """Score consolidado por funcionário em um ciclo.
    Calculado ao fechar o ciclo ou via endpoint manual.
    """
    __tablename__ = 'scores_avaliacao'
    id = db.Column(db.Integer, primary_key=True)
    ciclo_id = db.Column(db.Integer, db.ForeignKey('ciclos_avaliacao.id'), nullable=False)
    funcionario_id = db.Column(db.String(50), db.ForeignKey('funcionarios.id'), nullable=False)

    # Scores por perspectiva (0–100 normalizados)
    score_gerente = db.Column(db.Float, nullable=True)   # visão superior  — peso 40%
    score_alunos  = db.Column(db.Float, nullable=True)   # visão externa   — peso 30%
    score_pares   = db.Column(db.Float, nullable=True)   # visão lateral   — peso 30%
    score_global  = db.Column(db.Float, nullable=True)   # média ponderada 0–100

    # Nível: bronze | prata | ouro | diamante
    nivel = db.Column(db.String(20), nullable=True)

    # Contadores de respostas recebidas
    respostas_gerente = db.Column(db.Integer, default=0)
    respostas_alunos  = db.Column(db.Integer, default=0)
    respostas_pares   = db.Column(db.Integer, default=0)

    # False quando amostra de alunos < 10 → ciclo inconclusivo para este professor
    conclusivo = db.Column(db.Boolean, default=True)
    calculado_em = db.Column(db.DateTime, nullable=True)

    # Token único para acesso público ao resultado pelo próprio colaborador (PRD §4 Etapa 6)
    token_resultado = db.Column(db.String(64), unique=True, nullable=True)
    resultado_enviado_em = db.Column(db.DateTime, nullable=True)  # quando WhatsApp foi enviado

    funcionario = db.relationship('Funcionario', backref=db.backref('scores_avaliacao', lazy=True))

    def __repr__(self):
        return f'<ScoreAvaliacao ciclo={self.ciclo_id} func={self.funcionario_id} global={self.score_global}>'
