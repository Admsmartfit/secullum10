
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

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
    tipo = db.Column(db.String(50))  # Entrada/Saída
    origem = db.Column(db.String(100))  # REP, App, Manual, etc
    inconsistente = db.Column(db.Boolean, default=False)
    justificativa = db.Column(db.Text)

    # Localização (se disponível)
    latitude = db.Column(db.String(20))
    longitude = db.Column(db.String(20))

    # Controle
    data_sincronizacao = db.Column(db.DateTime, default=datetime.utcnow)

    # Índices para otimizar buscas
    __table_args__ = (
        db.Index('idx_funcionario_data', 'funcionario_id', 'data'),
        db.Index('idx_data', 'data'),
    )

    def __repr__(self):
        return f'<Batida {self.funcionario.nome} em {self.data} às {self.hora}>'

class Configuracao(db.Model):
    __tablename__ = 'configuracoes'
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(50), unique=True)
    valor = db.Column(db.String(255))
