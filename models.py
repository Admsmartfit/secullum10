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
    nivel_acesso = db.Column(db.String(20), default='professor')  # gestor / professor
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

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

    # Horário Secullum (schedule assigned via API)
    horario_secullum_numero = db.Column(db.Integer, nullable=True)
    horario_secullum_nome = db.Column(db.String(100), nullable=True)

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
    tipo = db.Column(db.String(50))      # saida / entrada / checkin / espelho
    mensagem = db.Column(db.Text)
    status = db.Column(db.String(20), default='enviado')   # enviado / erro / recebido
    celular = db.Column(db.String(20))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', backref='whatsapp_logs')


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

    # Condition: LATE_ENTRY | EARLY_LEAVE | ABSENCE | OVERTIME | INTERJORNADA | ESCALA_ENVIO
    condition_type      = db.Column(db.String(50), nullable=False, default='LATE_ENTRY')
    threshold_minutes   = db.Column(db.Integer, nullable=True, default=15)

    # Recipients
    dest_employee = db.Column(db.Boolean, default=False)
    dest_manager  = db.Column(db.Boolean, default=True)
    dest_rh       = db.Column(db.Boolean, default=False)

    # Message templates (support variables: {name} {full_name} {minutes} {turno} {inicio} {fim} {data})
    template_manager  = db.Column(db.Text, nullable=True)
    template_employee = db.Column(db.Text, nullable=True)

    # Constraints
    only_working_hours = db.Column(db.Boolean, default=True)
    send_immediately   = db.Column(db.Boolean, default=False)

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
