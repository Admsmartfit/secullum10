"""
Serviço de Avaliação 360° Aleatória — PRD v2.0
Responsável por:
  - Banco de perguntas (Likert 1-5)
  - Criação e gerenciamento de ciclos
  - Geração de tokens únicos por respondente
  - Cálculo de scores (40/30/30) e classificação por nível
  - Mensagens de feedback automático por nível
"""
from __future__ import annotations
import uuid
import random
import logging
from datetime import datetime, date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Banco de perguntas ────────────────────────────────────────────────────────
# Estrutura: {tipo: [(numero, texto, criterio, peso_percentual)]}

PERGUNTAS: dict[str, list[dict]] = {
    # 2.1 – Avaliação do Gerente (respondida pelos Professores)
    'gerente_por_professor': [
        {'num': 1, 'texto': 'Liderança: O gerente comunica metas e mudanças de forma clara e respeitosa?',
         'criterio': 'Comunicação', 'peso': 0.25},
        {'num': 2, 'texto': 'Suporte: Quando surge um problema no salão, o gerente te apoia prontamente?',
         'criterio': 'Gestão de Crise', 'peso': 0.25},
        {'num': 3, 'texto': 'Cultura: O gerente demonstra compromisso com as regras e motiva a equipe?',
         'criterio': 'Cultura', 'peso': 0.25},
        {'num': 4, 'texto': 'Processos: O gerente mantém as escalas e tarefas do setor organizadas?',
         'criterio': 'Organização', 'peso': 0.25},
    ],
    # 2.2 – Avaliação do Professor (respondida pelo Gerente)  → visão superior 40%
    'professor_por_gerente': [
        {'num': 1, 'texto': 'Postura: O professor atua ativamente no salão, abordando alunos sem esperar ser chamado?',
         'criterio': 'Proatividade', 'peso': 0.25},
        {'num': 2, 'texto': 'Técnica: O professor demonstra segurança e domínio técnico nas correções?',
         'criterio': 'Competência', 'peso': 0.25},
        {'num': 3, 'texto': 'Disciplina: O professor respeita rigorosamente horários e normas de conduta e uniforme?',
         'criterio': 'Disciplina', 'peso': 0.25},
        {'num': 4, 'texto': 'Conexão: O professor se empenha em reter o aluno e criar um bom ambiente de treino?',
         'criterio': 'Relacionamento', 'peso': 0.25},
    ],
    # 2.3 – Avaliação entre Pares  → visão lateral 30%
    'par_por_professor': [
        {'num': 1, 'texto': 'Parceria: Seu colega te ajuda na organização e atendimento quando o fluxo aumenta?',
         'criterio': 'Colaboração', 'peso': 0.34},
        {'num': 2, 'texto': 'Zelo: O parceiro mantém os pesos e equipamentos organizados durante o turno?',
         'criterio': 'Organização', 'peso': 0.33},
        {'num': 3, 'texto': 'Confiança: Você sente segurança na continuidade do trabalho quando este colega assume o posto?',
         'criterio': 'Confiança', 'peso': 0.33},
    ],
    # 2.4 – Avaliação do Aluno (sobre a equipe do horário)  → visão externa 30%
    'aluno_por_equipe': [
        {'num': 1, 'texto': 'Atenção: Você se sentiu assistido pelos professores durante seu treino hoje?',
         'criterio': 'Presença', 'peso': 0.34},
        {'num': 2, 'texto': 'Segurança: Os professores corrigiram sua postura ou técnica em algum momento?',
         'criterio': 'Técnica', 'peso': 0.33},
        {'num': 3, 'texto': 'Cordialidade: A equipe foi educada e solícita com você?',
         'criterio': 'Atendimento', 'peso': 0.33},
    ],
}

# Títulos amigáveis por tipo
TIPO_LABELS = {
    'gerente_por_professor': 'Avaliação do Gerente',
    'professor_por_gerente': 'Avaliação do Professor',
    'par_por_professor': 'Avaliação entre Pares',
    'aluno_por_equipe': 'Avaliação de Aluno',
}

# ── Classificação por nível ───────────────────────────────────────────────────

NIVEIS = [
    ('diamante', 95, 100, 0.15, '💎'),
    ('ouro',     85,  94, 0.10, '🥇'),
    ('prata',    75,  84, 0.05, '🥈'),
    ('bronze',    0,  74, 0.00, '🥉'),
]

MENSAGENS_FEEDBACK = {
    'diamante': (
        'Parabéns! Sua performance foi classificada como referência na unidade. '
        'Você demonstra alto domínio técnico e excelente percepção dos alunos. '
        'Continue sendo o exemplo de liderança positiva no salão.'
    ),
    'ouro': (
        'Ótimo trabalho! Sua consistência é notável e a equipe confia no seu suporte. '
        'Os alunos valorizam seu atendimento. Fique atento apenas aos detalhes qualitativos '
        'para alcançar o nível máximo no próximo ciclo.'
    ),
    'prata': (
        'Bom desempenho. Você cumpre os requisitos essenciais da função. '
        'Para evoluir, foque em aumentar sua proatividade no salão e a sintonia '
        'com seu parceiro de turno. Pequenos ajustes trarão grandes resultados.'
    ),
    'bronze': (
        'Recebemos seus feedbacks e este é um momento de atenção. '
        'Identificamos oportunidades de melhoria na sua interação com a equipe e alunos. '
        'O RH agendará um alinhamento para construirmos juntos um Plano de Desenvolvimento Individual (PDI).'
    ),
}

ACOES_RH = {
    'diamante': 'Nenhuma. Compartilhar boas práticas com a equipe.',
    'ouro':     'Autoanálise orientada: identificar 1 ponto de melhoria com o gestor.',
    'prata':    'Reunião quinzenal com gerente para acompanhamento de metas.',
    'bronze':   'PDI obrigatório em até 7 dias. Reavaliação em 30 dias.',
}


def _novo_token() -> str:
    return uuid.uuid4().hex


def classificar_nivel(score: float) -> str:
    for nivel, minv, maxv, _, _ in NIVEIS:
        if minv <= score <= maxv:
            return nivel
    return 'bronze'


def _nota_para_score(notas: list[int], pesos: list[float]) -> float:
    """Converte notas Likert 1-5 para score 0-100 ponderado."""
    if not notas:
        return 0.0
    raw = sum(n * p for n, p in zip(notas, pesos))
    # Escala Likert máx = 5 → normaliza para 0-100
    raw_max = 5.0  # máximo possível na escala ponderada (sum(pesos) ≈ 1.0)
    return round((raw / raw_max) * 100, 2)


# ── Criação de ciclo ──────────────────────────────────────────────────────────

def criar_ciclo(departamento: Optional[str] = None) -> 'CicloAvaliacao':
    """Cria um novo ciclo de avaliação e sorteia a data do próximo ciclo (30-90 dias)."""
    from extensions import db
    from models import CicloAvaliacao

    hoje = date.today()
    fim_coleta = hoje + timedelta(days=3)
    dias_proximo = random.randint(30, 90)
    proximo = hoje + timedelta(days=dias_proximo)

    ciclo = CicloAvaliacao(
        departamento=departamento,
        data_inicio=hoje,
        data_fim_coleta=fim_coleta,
        status='ativo',
        proximo_ciclo_data=proximo,
    )
    db.session.add(ciclo)
    db.session.commit()
    log.info(f'[avaliacao] Ciclo {ciclo.id} criado. Próximo: {proximo}')
    return ciclo


# ── Geração de tokens ─────────────────────────────────────────────────────────

def gerar_tokens_ciclo(ciclo_id: int) -> dict:
    """Gera tokens para todos os respondentes de um ciclo.

    Regras:
    - Para cada professor:  1 token professor_por_gerente (gerente responde)
    - Para cada professor:  tokens par_por_professor (colegas do mesmo turno respondem)
    - Para cada professor:  tokens aluno_por_equipe (alunos do turno respondem)
    - Para o gerente:       tokens gerente_por_professor (professores respondem)

    Retorna dict com contagem de tokens criados por tipo.
    """
    from extensions import db
    from models import CicloAvaliacao, TokenAvaliacao, Funcionario, AlocacaoDiaria, UnidadeLider, AlunoUnidade

    ciclo = CicloAvaliacao.query.get(ciclo_id)
    if not ciclo:
        return {'erro': 'Ciclo não encontrado'}

    expira_em = datetime.combine(ciclo.data_fim_coleta, datetime.max.time())
    contagem = {k: 0 for k in TIPO_LABELS}

    # Busca professores ativos do departamento
    q = Funcionario.query.filter(Funcionario.ativo == True)
    if ciclo.departamento:
        q = q.filter(Funcionario.departamento == ciclo.departamento)
    funcionarios = q.all()

    if not funcionarios:
        log.warning(f'[avaliacao] Ciclo {ciclo_id}: nenhum funcionário encontrado.')
        return contagem

    # Identifica gerentes (nível acesso 'gerente' via Usuario vinculado por email)
    from models import Usuario
    emails_gerentes = {u.email for u in Usuario.query.filter_by(nivel_acesso='gerente').all()}
    gerentes = [f for f in funcionarios if f.email and f.email in emails_gerentes]
    professores = [f for f in funcionarios if f not in gerentes]

    # 1. Professor avaliado pelo gerente (um token por professor, respondido pelo gerente)
    for gerente in gerentes:
        for prof in professores:
            t = TokenAvaliacao(
                ciclo_id=ciclo_id,
                token=_novo_token(),
                tipo='professor_por_gerente',
                avaliado_id=prof.id,
                avaliador_id=gerente.id,
                expira_em=expira_em,
            )
            db.session.add(t)
            contagem['professor_por_gerente'] += 1

    # 2. Gerente avaliado pelos professores (um token por professor)
    for gerente in gerentes:
        for prof in professores:
            t = TokenAvaliacao(
                ciclo_id=ciclo_id,
                token=_novo_token(),
                tipo='gerente_por_professor',
                avaliado_id=gerente.id,
                avaliador_id=prof.id,
                expira_em=expira_em,
            )
            db.session.add(t)
            contagem['gerente_por_professor'] += 1

    # 3. Pares: cada professor avalia colegas do mesmo turno na data de início do ciclo
    alocacoes_hoje: dict[int, list[str]] = {}  # turno_id → [func_ids]
    for aloc in AlocacaoDiaria.query.filter_by(data=ciclo.data_inicio).all():
        alocacoes_hoje.setdefault(aloc.turno_id, []).append(aloc.funcionario_id)

    for turno_id, ids_turno in alocacoes_hoje.items():
        profs_turno = [f for f in professores if f.id in ids_turno]
        for avaliador in profs_turno:
            for avaliado in profs_turno:
                if avaliador.id != avaliado.id:
                    t = TokenAvaliacao(
                        ciclo_id=ciclo_id,
                        token=_novo_token(),
                        tipo='par_por_professor',
                        avaliado_id=avaliado.id,
                        avaliador_id=avaliador.id,
                        expira_em=expira_em,
                    )
                    db.session.add(t)
                    contagem['par_por_professor'] += 1

    # 4. Alunos avaliam a equipe do horário
    # PRD §4 Etapa 2 + §7: apenas alunos cujo horário coincide com o turno de algum professor.
    # Match por correspondência do campo aluno.horario com o nome/horario dos turnos de hoje.
    from models import Turno
    turnos_hoje_ids = set(alocacoes_hoje.keys())
    turnos_hoje = Turno.query.filter(Turno.id.in_(turnos_hoje_ids)).all() if turnos_hoje_ids else []
    # Monta conjunto de palavras-chave dos turnos ativos hoje (nome e intervalo de horário)
    turno_keywords: set[str] = set()
    for t_obj in turnos_hoje:
        if t_obj.nome:
            turno_keywords.add(t_obj.nome.strip().lower())
        if t_obj.hora_inicio:
            turno_keywords.add(str(t_obj.hora_inicio)[:5])  # "HH:MM"
        if t_obj.hora_fim:
            turno_keywords.add(str(t_obj.hora_fim)[:5])

    alunos_q = AlunoUnidade.query.filter_by(ativo=True)
    if ciclo.departamento:
        alunos_q = alunos_q.filter_by(departamento=ciclo.departamento)
    alunos = alunos_q.all()

    for aluno in alunos:
        # Se o aluno tem horário definido, verifica se coincide com algum turno ativo
        if aluno.horario and turno_keywords:
            horario_lower = aluno.horario.strip().lower()
            match = any(kw in horario_lower or horario_lower in kw for kw in turno_keywords)
            if not match:
                log.debug(f'[avaliacao] Aluno {aluno.nome} horario={aluno.horario!r} sem turno correspondente — ignorado')
                continue
        # Aluno sem horário definido é incluído (não há filtro possível)
        t = TokenAvaliacao(
            ciclo_id=ciclo_id,
            token=_novo_token(),
            tipo='aluno_por_equipe',
            avaliador_nome=aluno.nome,
            avaliador_celular=aluno.celular,
            expira_em=expira_em,
        )
        db.session.add(t)
        contagem['aluno_por_equipe'] += 1

    db.session.commit()
    log.info(f'[avaliacao] Ciclo {ciclo_id}: tokens gerados → {contagem}')
    return contagem


# ── Cálculo de score ──────────────────────────────────────────────────────────

def calcular_scores_ciclo(ciclo_id: int) -> list[dict]:
    """Calcula e persiste ScoreAvaliacao para cada professor no ciclo.
    Retorna lista com resumo dos scores calculados.
    """
    from extensions import db
    from models import CicloAvaliacao, TokenAvaliacao, RespostaAvaliacao, ScoreAvaliacao, Funcionario

    ciclo = CicloAvaliacao.query.get(ciclo_id)
    if not ciclo:
        return []

    AMOSTRA_MINIMA_ALUNOS = 10
    resultados = []

    # Coleta todos os avaliados do ciclo
    avaliados_ids = {
        t.avaliado_id
        for t in TokenAvaliacao.query.filter_by(ciclo_id=ciclo_id).all()
        if t.avaliado_id
    }

    for func_id in avaliados_ids:
        func = Funcionario.query.get(func_id)
        if not func:
            continue

        def _media_ponderada(tipo: str) -> tuple[float, int]:
            """Retorna (score_0_100, qtd_respostas_completas) para um tipo."""
            tokens = TokenAvaliacao.query.filter_by(
                ciclo_id=ciclo_id, tipo=tipo, avaliado_id=func_id, respondido=True
            ).all()
            if not tokens:
                return 0.0, 0

            perguntas = PERGUNTAS[tipo]
            pesos = [p['peso'] for p in perguntas]
            scores_individuais = []

            for tk in tokens:
                resps = {r.questao_numero: r.nota for r in tk.respostas}
                if len(resps) < len(perguntas):
                    continue  # resposta incompleta
                notas = [resps.get(p['num'], 3) for p in perguntas]  # 3 = neutro fallback
                scores_individuais.append(_nota_para_score(notas, pesos))

            if not scores_individuais:
                return 0.0, 0
            return round(sum(scores_individuais) / len(scores_individuais), 2), len(scores_individuais)

        # Scores por perspectiva (aluno_por_equipe é compartilhado pelo turno,
        # mas aqui vinculamos diretamente pelo avaliado_id=None — ver nota abaixo)
        score_ger, qtd_ger = _media_ponderada('professor_por_gerente')
        score_par, qtd_par = _media_ponderada('par_por_professor')

        # Alunos: como aluno avalia a equipe (sem avaliado_id fixo),
        # usamos tokens do ciclo/departamento sem avaliado_id vinculado a este func.
        # Para simplificar no MVP, o score de alunos é compartilhado por turno.
        tokens_alunos = TokenAvaliacao.query.filter_by(
            ciclo_id=ciclo_id, tipo='aluno_por_equipe', respondido=True
        ).all()
        if ciclo.departamento:
            pass  # já filtrado na geração
        perguntas_aluno = PERGUNTAS['aluno_por_equipe']
        pesos_aluno = [p['peso'] for p in perguntas_aluno]
        scores_aluno_list = []
        for tk in tokens_alunos:
            resps = {r.questao_numero: r.nota for r in tk.respostas}
            if len(resps) < len(perguntas_aluno):
                continue
            notas = [resps.get(p['num'], 3) for p in perguntas_aluno]
            scores_aluno_list.append(_nota_para_score(notas, pesos_aluno))
        qtd_alunos = len(scores_aluno_list)
        score_alu = round(sum(scores_aluno_list) / qtd_alunos, 2) if scores_aluno_list else 0.0

        # Fórmula: 40% gerente + 30% alunos + 30% pares
        score_global = round(score_ger * 0.40 + score_alu * 0.30 + score_par * 0.30, 2)
        conclusivo = qtd_alunos >= AMOSTRA_MINIMA_ALUNOS
        nivel = classificar_nivel(score_global) if conclusivo else None

        # Persiste ou atualiza ScoreAvaliacao
        sc = ScoreAvaliacao.query.filter_by(ciclo_id=ciclo_id, funcionario_id=func_id).first()
        if not sc:
            sc = ScoreAvaliacao(ciclo_id=ciclo_id, funcionario_id=func_id)
            db.session.add(sc)

        sc.score_gerente = score_ger
        sc.score_alunos = score_alu
        sc.score_pares = score_par
        sc.score_global = score_global
        sc.nivel = nivel
        sc.respostas_gerente = qtd_ger
        sc.respostas_alunos = qtd_alunos
        sc.respostas_pares = qtd_par
        sc.conclusivo = conclusivo
        sc.calculado_em = datetime.utcnow()

        resultados.append({
            'funcionario': func.nome,
            'score_global': score_global,
            'nivel': nivel,
            'conclusivo': conclusivo,
        })

    db.session.commit()
    log.info(f'[avaliacao] Scores calculados para ciclo {ciclo_id}: {len(resultados)} funcionários')
    return resultados


# ── Fechar ciclo ──────────────────────────────────────────────────────────────

def _notificar_gerentes_proximo_ciclo(ciclo: 'CicloAvaliacao') -> None:
    """PRD §7: Gerente notificado via WhatsApp quando o sorteio ocorrer.
    Enviado ao fechar o ciclo atual (quando proximo_ciclo_data é definido).
    """
    try:
        from models import Usuario, Funcionario, UnidadeLider
        from services.whatsapp_bot import enviar_texto

        data_fmt = ciclo.proximo_ciclo_data.strftime('%d/%m/%Y') if ciclo.proximo_ciclo_data else '—'
        msg = (
            f'📋 *Avaliação 360° Agendada*\n\n'
            f'Olá! O próximo ciclo de Avaliação 360°'
            f'{f" do departamento {ciclo.departamento}" if ciclo.departamento else ""} '
            f'foi sorteado para *{data_fmt}*.\n\n'
            f'Você tem até essa data para preparar a equipe. '
            f'A coleta de respostas terá janela de 72h após o início.\n\n'
            f'Atenciosamente, Sistema de RH.'
        )

        # Busca gerentes: usuarios nível 'gerente' com funcionario vinculado por email
        gerentes_usuario = Usuario.query.filter_by(nivel_acesso='gerente', ativo=True).all()
        for ger_u in gerentes_usuario:
            func = Funcionario.query.filter_by(email=ger_u.email, ativo=True).first()
            if func and func.celular:
                if ciclo.departamento and func.departamento != ciclo.departamento:
                    continue
                enviar_texto(func.celular, msg, func_id=func.id, tipo='avaliacao_notif')
                log.info(f'[avaliacao] Gerente {func.nome} notificado sobre próximo ciclo em {data_fmt}')
    except Exception as e:
        log.warning(f'[avaliacao] Falha ao notificar gerentes: {e}')


def _enviar_resultado_colaborador(ciclo_id: int) -> int:
    """PRD §4 Etapa 6: envia resultado individual via WhatsApp a cada colaborador avaliado.
    Gera um token_resultado único por ScoreAvaliacao e manda o link + nível.
    Retorna número de mensagens enviadas.
    """
    from extensions import db
    from models import ScoreAvaliacao, Funcionario
    from services.whatsapp_bot import enviar_texto

    url_base = _url_base()
    scores = ScoreAvaliacao.query.filter_by(ciclo_id=ciclo_id).all()
    emoji_map = {'diamante': '💎', 'ouro': '🥇', 'prata': '🥈', 'bronze': '🥉'}
    bonus_map = {'diamante': '15%', 'ouro': '10%', 'prata': '5%', 'bronze': '—'}
    enviados = 0

    for sc in scores:
        func = Funcionario.query.get(sc.funcionario_id)
        if not func or not func.celular:
            continue

        # Gera token único de resultado (idempotente)
        if not sc.token_resultado:
            sc.token_resultado = _novo_token()
            db.session.commit()

        nivel = sc.nivel or 'bronze'
        link = f'{url_base}/r/resultado/{sc.token_resultado}' if url_base else ''

        msg_nivel = (
            f'{emoji_map.get(nivel, "")} *{nivel.title()}*'
            f'{" — Bônus: " + bonus_map[nivel] if sc.conclusivo and nivel != "bronze" else ""}'
        )

        if sc.conclusivo:
            msg = (
                f'Olá, {func.nome.split()[0]}! 🎯\n\n'
                f'Seu resultado da *Avaliação 360°* (Ciclo #{sc.ciclo_id}) está disponível:\n\n'
                f'📊 Score Global: *{sc.score_global:.1f}/100*\n'
                f'🏅 Nível: {msg_nivel}\n\n'
                f'{MENSAGENS_FEEDBACK.get(nivel, "")}\n\n'
            )
            if link:
                msg += f'👉 Ver resultado completo: {link}\n\n'
            msg += 'Parabéns pelo seu trabalho! 💪'
        else:
            msg = (
                f'Olá, {func.nome.split()[0]}! 📋\n\n'
                f'Seu ciclo de *Avaliação 360°* (#{sc.ciclo_id}) foi encerrado, mas '
                f'a amostra de alunos foi insuficiente (mínimo 10 respostas).\n\n'
                f'Um novo ciclo será agendado em breve para complementar a avaliação.\n\n'
                f'Obrigado pela sua participação!'
            )

        ok = enviar_texto(func.celular, msg, func_id=func.id, tipo='avaliacao_resultado')
        if ok:
            sc.resultado_enviado_em = datetime.utcnow()
            db.session.commit()
            enviados += 1
            log.info(f'[avaliacao] Resultado enviado para {func.nome} (ciclo {ciclo_id})')
        else:
            log.warning(f'[avaliacao] Falha ao enviar resultado para {func.nome}')

    return enviados


def fechar_ciclo(ciclo_id: int) -> dict:
    """Calcula scores finais, marca o ciclo como fechado,
    envia resultado a cada colaborador e notifica gerentes do próximo ciclo.
    PRD §4 Etapa 6.
    """
    from extensions import db
    from models import CicloAvaliacao

    scores = calcular_scores_ciclo(ciclo_id)
    ciclo = CicloAvaliacao.query.get(ciclo_id)
    if ciclo:
        ciclo.status = 'fechado'
        ciclo.fechado_em = datetime.utcnow()
        db.session.commit()
        # PRD §4 Etapa 6: envia resultado individual a cada colaborador
        enviados = _enviar_resultado_colaborador(ciclo_id)
        log.info(f'[avaliacao] Ciclo {ciclo_id} fechado. Resultados enviados: {enviados}')
        # PRD §7: notifica gerentes sobre o próximo ciclo sorteado
        if ciclo.proximo_ciclo_data:
            _notificar_gerentes_proximo_ciclo(ciclo)
    return {'scores': scores, 'ciclo_id': ciclo_id}


# ── WhatsApp ──────────────────────────────────────────────────────────────────

def _url_base() -> str:
    """Retorna URL base da aplicação (DB > env APP_URL_BASE > fallback vazio)."""
    import os
    from models import Configuracao
    row = Configuracao.query.filter_by(chave='app_url_base').first()
    if row and row.valor:
        return row.valor.rstrip('/')
    return os.getenv('APP_URL_BASE', '').rstrip('/')


def enviar_convites_ciclo(ciclo_id: int) -> dict:
    """Envia convites WhatsApp para todos os respondentes do ciclo."""
    from models import TokenAvaliacao, Funcionario
    from services.whatsapp_bot import enviar_texto

    tokens = TokenAvaliacao.query.filter_by(ciclo_id=ciclo_id, enviado_em=None).all()
    url_base = _url_base()
    enviados = 0
    erros = 0

    for tk in tokens:
        celular = None
        nome = None

        if tk.tipo == 'aluno_por_equipe':
            celular = tk.avaliador_celular
            nome = tk.avaliador_nome or 'Aluno'
        elif tk.avaliador_id:
            func = Funcionario.query.get(tk.avaliador_id)
            if func:
                celular = func.celular
                nome = func.nome

        if not celular:
            continue

        link = f'{url_base}/r/{tk.token}'
        label = TIPO_LABELS.get(tk.tipo, 'Avaliação')

        if tk.tipo == 'aluno_por_equipe':
            msg = (
                f'Olá, {nome}! 👋\n'
                f'Gostaríamos da sua opinião sobre o treino de hoje. '
                f'São apenas 3 perguntas rápidas — sua resposta é anônima e '
                f'nos ajuda a melhorar cada vez mais!\n\n'
                f'👉 Clique para avaliar: {link}\n\n'
                f'Disponível por 48h. Obrigado pela parceria! 💪'
            )
        else:
            msg = (
                f'Olá, {nome}! 👋\n'
                f'É hora da {label}. Leva menos de 2 minutos e é muito importante '
                f'para o desenvolvimento da equipe.\n\n'
                f'👉 Responder agora: {link}\n\n'
                f'Disponível por 72h. Obrigado!'
            )

        ok = enviar_texto(celular, msg, func_id=tk.avaliador_id, tipo='avaliacao')
        if ok:
            from extensions import db
            tk.enviado_em = datetime.utcnow()
            db.session.commit()
            enviados += 1
        else:
            erros += 1

    return {'enviados': enviados, 'erros': erros}


def gerar_relatorio_pdf(ciclo_id: int, funcionario_id: str) -> 'BytesIO':
    """Gera PDF do relatório de feedback 360° para um funcionário em um ciclo."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer, HRFlowable)
    from models import ScoreAvaliacao, Funcionario, CicloAvaliacao

    sc = ScoreAvaliacao.query.filter_by(ciclo_id=ciclo_id, funcionario_id=funcionario_id).first()
    func = Funcionario.query.get(funcionario_id)
    ciclo = CicloAvaliacao.query.get(ciclo_id)
    if not sc or not func or not ciclo:
        raise ValueError('Score, funcionário ou ciclo não encontrado.')

    DARK   = colors.HexColor('#1e293b')
    ACCENT = colors.HexColor('#0d6efd')
    GRAY   = colors.HexColor('#94a3b8')
    RED    = colors.HexColor('#ef4444')
    NIVEL_COLORS = {
        'diamante': colors.HexColor('#0ea5e9'),
        'ouro':     colors.HexColor('#f59e0b'),
        'prata':    colors.HexColor('#94a3b8'),
        'bronze':   colors.HexColor('#ef4444'),
    }

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=20*mm, leftMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm,
                            title=f'Relatório 360° – {func.nome}')
    styles = getSampleStyleSheet()
    s_title  = ParagraphStyle('T', parent=styles['Heading1'], fontSize=18, textColor=DARK, spaceAfter=2*mm)
    s_sub    = ParagraphStyle('S', parent=styles['Normal'],  fontSize=11, textColor=GRAY, spaceAfter=4*mm)
    s_body   = ParagraphStyle('B', parent=styles['Normal'],  fontSize=10, textColor=DARK, spaceAfter=3*mm)

    nivel = sc.nivel or 'bronze'
    emoji_map = {'diamante': '💎', 'ouro': '🥇', 'prata': '🥈', 'bronze': '🥉'}
    bonus_map = {'diamante': '15%', 'ouro': '10%', 'prata': '5%', 'bronze': '—'}

    story = []
    story.append(Paragraph('Relatório de Avaliação 360°', s_title))
    story.append(Paragraph(f'{func.nome} — Ciclo #{ciclo.id} ({ciclo.data_inicio.strftime("%d/%m/%Y")})', s_sub))
    story.append(HRFlowable(width='100%', thickness=1, color=ACCENT, spaceAfter=6*mm))

    # Scores
    data_table = [
        ['Perspectiva', 'Score', 'Respostas', 'Peso'],
        ['Gerente (Visão Superior)',  f'{sc.score_gerente:.1f}/100' if sc.score_gerente else '—', str(sc.respostas_gerente), '40%'],
        ['Alunos (Visão Externa)',    f'{sc.score_alunos:.1f}/100'  if sc.score_alunos  else '—', str(sc.respostas_alunos),  '30%'],
        ['Pares (Visão Lateral)',     f'{sc.score_pares:.1f}/100'   if sc.score_pares   else '—', str(sc.respostas_pares),   '30%'],
        ['SCORE GLOBAL',              f'{sc.score_global:.1f}/100'  if sc.score_global  else '—', '',                        ''],
    ]
    t = Table(data_table, colWidths=[90*mm, 35*mm, 30*mm, 20*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND',  (0, 0), (-1, 0), DARK),
        ('TEXTCOLOR',   (0, 0), (-1, 0), colors.white),
        ('FONTSIZE',    (0, 0), (-1, 0), 10),
        ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND',  (0, -1), (-1, -1), NIVEL_COLORS.get(nivel, GRAY)),
        ('TEXTCOLOR',   (0, -1), (-1, -1), colors.white),
        ('FONTNAME',    (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8fafc')]),
        ('GRID',        (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('ALIGN',       (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',  (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))

    # Nível e bônus
    story.append(Paragraph(
        f'<b>Nível: {emoji_map.get(nivel, "")} {nivel.title()}</b> &nbsp;·&nbsp; Bônus: <b>{bonus_map.get(nivel, "—")}</b>',
        ParagraphStyle('N', parent=styles['Normal'], fontSize=13, textColor=NIVEL_COLORS.get(nivel, DARK), spaceAfter=4*mm)
    ))

    if not sc.conclusivo:
        story.append(Paragraph(
            '⚠️ Ciclo marcado como inconclusivo (amostra de alunos insuficiente — mínimo 10 respostas).',
            ParagraphStyle('W', parent=styles['Normal'], fontSize=9, textColor=RED, spaceAfter=4*mm)
        ))

    # Feedback
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY, spaceAfter=4*mm))
    story.append(Paragraph('<b>Mensagem de Feedback</b>', s_body))
    story.append(Paragraph(MENSAGENS_FEEDBACK.get(nivel, ''), s_body))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('<b>Ação do RH</b>', s_body))
    story.append(Paragraph(ACOES_RH.get(nivel, ''), s_body))

    # Rodapé
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(
        f'Gerado em {datetime.utcnow().strftime("%d/%m/%Y às %H:%M")} UTC — Avaliação 360° confidencial',
        ParagraphStyle('F', parent=styles['Normal'], fontSize=7, textColor=GRAY, alignment=1)
    ))

    doc.build(story)
    buf.seek(0)
    return buf


def enviar_lembretes(ciclo_id: int, horas: int = 24) -> dict:
    """Envia lembretes para tokens ainda não respondidos.
    horas: 24 ou 48.
    """
    from extensions import db
    from models import TokenAvaliacao, Funcionario
    from services.whatsapp_bot import enviar_texto

    agora = datetime.utcnow()
    tokens = TokenAvaliacao.query.filter_by(
        ciclo_id=ciclo_id, respondido=False
    ).filter(TokenAvaliacao.enviado_em.isnot(None)).all()

    url_base = _url_base()
    enviados = 0

    for tk in tokens:
        if not tk.enviado_em:
            continue
        delta_h = (agora - tk.enviado_em).total_seconds() / 3600

        # Envia lembrete 24h somente uma vez
        if horas == 24 and delta_h >= 24 and not tk.lembrete_24h_em:
            campo = 'lembrete_24h_em'
        elif horas == 48 and delta_h >= 48 and not tk.lembrete_48h_em:
            campo = 'lembrete_48h_em'
        else:
            continue

        celular = None
        nome = None
        if tk.tipo == 'aluno_por_equipe':
            celular = tk.avaliador_celular
            nome = tk.avaliador_nome or 'Aluno'
        elif tk.avaliador_id:
            func = Funcionario.query.get(tk.avaliador_id)
            if func:
                celular = func.celular
                nome = func.nome

        if not celular:
            continue

        link = f'{url_base}/r/{tk.token}'
        msg = (
            f'Olá, {nome}! ⏰ Lembrete: você ainda não respondeu a avaliação.\n'
            f'Restam poucas horas! 👉 {link}'
        )
        ok = enviar_texto(celular, msg, func_id=tk.avaliador_id, tipo='avaliacao_lembrete')
        if ok:
            setattr(tk, campo, agora)
            db.session.commit()
            enviados += 1

    return {'lembretes_enviados': enviados, 'horas': horas}
