"""
Módulo de Avaliação 360° Aleatória — PRD v2.0
Rotas:
  /avaliacoes/               → painel RH (admin/gerente)
  /avaliacoes/ciclo/<id>     → detalhes de um ciclo
  /avaliacoes/ciclo/novo     → criar ciclo manualmente
  /avaliacoes/ciclo/<id>/fechar   → fechar ciclo e calcular scores
  /avaliacoes/ciclo/<id>/enviar   → disparar convites WhatsApp
  /avaliacoes/alunos          → gestão da base de alunos
  /avaliacoes/alunos/importar → importação CSV
  /r/<token>                 → formulário público (sem login)
  /r/<token>/submit          → submissão de respostas (sem login)
"""
import csv
import io
import random
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, make_response)
from flask_login import login_required, current_user
from extensions import db
from models import (CicloAvaliacao, TokenAvaliacao, RespostaAvaliacao,
                    ScoreAvaliacao, AlunoUnidade, Funcionario)
from services.avaliacao_service import (
    PERGUNTAS, TIPO_LABELS, NIVEIS, MENSAGENS_FEEDBACK, ACOES_RH,
    criar_ciclo, gerar_tokens_ciclo, fechar_ciclo,
    calcular_scores_ciclo, enviar_convites_ciclo,
)

avaliacoes_bp = Blueprint('avaliacoes', __name__, url_prefix='/avaliacoes')
# Blueprint público — rotas sem login em /r/<token>
avaliacoes_public_bp = Blueprint('avaliacoes_public', __name__)


def _admin_ou_gerente():
    return current_user.is_authenticated and current_user.nivel_acesso in ('administrador', 'gerente')


# ── Painel principal ──────────────────────────────────────────────────────────

@avaliacoes_bp.route('/')
@login_required
def index():
    ciclos = CicloAvaliacao.query.order_by(CicloAvaliacao.data_inicio.desc()).limit(20).all()
    return render_template('avaliacoes/index.html', ciclos=ciclos)


# ── Criar ciclo ───────────────────────────────────────────────────────────────

@avaliacoes_bp.route('/ciclo/novo', methods=['GET', 'POST'])
@login_required
def novo_ciclo():
    if not _admin_ou_gerente():
        flash('Acesso restrito.', 'danger')
        return redirect(url_for('avaliacoes.index'))

    if request.method == 'POST':
        departamento = request.form.get('departamento') or None
        ciclo = criar_ciclo(departamento=departamento)
        contagem = gerar_tokens_ciclo(ciclo.id)
        flash(
            f'Ciclo #{ciclo.id} criado! '
            f'Tokens gerados: gerente={contagem.get("professor_por_gerente", 0)}, '
            f'pares={contagem.get("par_por_professor", 0)}, '
            f'alunos={contagem.get("aluno_por_equipe", 0)}.',
            'success',
        )
        return redirect(url_for('avaliacoes.detalhe_ciclo', ciclo_id=ciclo.id))

    # GET — lista departamentos disponíveis
    deps = db.session.query(Funcionario.departamento).filter(
        Funcionario.ativo == True, Funcionario.departamento.isnot(None)
    ).distinct().all()
    departamentos = sorted({d[0] for d in deps if d[0]})
    return render_template('avaliacoes/novo_ciclo.html', departamentos=departamentos)


# ── Detalhe do ciclo ──────────────────────────────────────────────────────────

@avaliacoes_bp.route('/ciclo/<int:ciclo_id>')
@login_required
def detalhe_ciclo(ciclo_id):
    ciclo = CicloAvaliacao.query.get_or_404(ciclo_id)
    scores = ScoreAvaliacao.query.filter_by(ciclo_id=ciclo_id).all()

    # Estatísticas de tokens
    total_tokens = TokenAvaliacao.query.filter_by(ciclo_id=ciclo_id).count()
    respondidos = TokenAvaliacao.query.filter_by(ciclo_id=ciclo_id, respondido=True).count()
    taxa = round((respondidos / total_tokens * 100), 1) if total_tokens else 0

    niveis_info = {n[0]: {'emoji': n[4], 'min': n[1], 'max': n[2]} for n in NIVEIS}

    return render_template(
        'avaliacoes/detalhe_ciclo.html',
        ciclo=ciclo,
        scores=scores,
        total_tokens=total_tokens,
        respondidos=respondidos,
        taxa_resposta=taxa,
        niveis_info=niveis_info,
        mensagens_feedback=MENSAGENS_FEEDBACK,
        acoes_rh=ACOES_RH,
    )


# ── Fechar ciclo ──────────────────────────────────────────────────────────────

@avaliacoes_bp.route('/ciclo/<int:ciclo_id>/fechar', methods=['POST'])
@login_required
def fechar(ciclo_id):
    if not _admin_ou_gerente():
        return jsonify({'erro': 'Acesso restrito'}), 403
    resultado = fechar_ciclo(ciclo_id)
    flash(f'Ciclo #{ciclo_id} fechado. {len(resultado["scores"])} scores calculados.', 'success')
    return redirect(url_for('avaliacoes.detalhe_ciclo', ciclo_id=ciclo_id))


# ── Recalcular scores ─────────────────────────────────────────────────────────

@avaliacoes_bp.route('/ciclo/<int:ciclo_id>/calcular', methods=['POST'])
@login_required
def recalcular(ciclo_id):
    if not _admin_ou_gerente():
        return jsonify({'erro': 'Acesso restrito'}), 403
    scores = calcular_scores_ciclo(ciclo_id)
    return jsonify({'ok': True, 'scores': scores})


# ── Enviar convites WhatsApp ──────────────────────────────────────────────────

@avaliacoes_bp.route('/ciclo/<int:ciclo_id>/enviar', methods=['POST'])
@login_required
def enviar_convites(ciclo_id):
    if not _admin_ou_gerente():
        return jsonify({'erro': 'Acesso restrito'}), 403
    resultado = enviar_convites_ciclo(ciclo_id)
    flash(
        f'Convites enviados: {resultado["enviados"]}. Erros: {resultado["erros"]}.',
        'success' if resultado['erros'] == 0 else 'warning',
    )
    return redirect(url_for('avaliacoes.detalhe_ciclo', ciclo_id=ciclo_id))


# ── Gestão de alunos ──────────────────────────────────────────────────────────

@avaliacoes_bp.route('/alunos')
@login_required
def alunos():
    if not _admin_ou_gerente():
        flash('Acesso restrito.', 'danger')
        return redirect(url_for('avaliacoes.index'))
    lista = AlunoUnidade.query.order_by(AlunoUnidade.nome).all()
    deps = db.session.query(Funcionario.departamento).filter(
        Funcionario.ativo == True, Funcionario.departamento.isnot(None)
    ).distinct().all()
    departamentos = sorted({d[0] for d in deps if d[0]})
    return render_template('avaliacoes/alunos.html', alunos=lista, departamentos=departamentos)


@avaliacoes_bp.route('/alunos/adicionar', methods=['POST'])
@login_required
def adicionar_aluno():
    if not _admin_ou_gerente():
        abort(403)
    nome = request.form.get('nome', '').strip()
    celular = request.form.get('celular', '').strip()
    horario = request.form.get('horario', '').strip() or None
    departamento = request.form.get('departamento', '').strip() or None

    if not nome or not celular:
        flash('Nome e celular são obrigatórios.', 'danger')
        return redirect(url_for('avaliacoes.alunos'))

    aluno = AlunoUnidade(nome=nome, celular=celular, horario=horario, departamento=departamento)
    db.session.add(aluno)
    db.session.commit()
    flash(f'Aluno {nome} adicionado.', 'success')
    return redirect(url_for('avaliacoes.alunos'))


@avaliacoes_bp.route('/alunos/<int:aluno_id>/toggle', methods=['POST'])
@login_required
def toggle_aluno(aluno_id):
    if not _admin_ou_gerente():
        abort(403)
    aluno = AlunoUnidade.query.get_or_404(aluno_id)
    aluno.ativo = not aluno.ativo
    db.session.commit()
    return jsonify({'ativo': aluno.ativo})


@avaliacoes_bp.route('/alunos/importar', methods=['POST'])
@login_required
def importar_alunos():
    """Importa alunos via CSV com colunas: NOME, CELULAR, HORARIO (opcional)."""
    if not _admin_ou_gerente():
        abort(403)
    arquivo = request.files.get('arquivo')
    departamento = request.form.get('departamento') or None

    if not arquivo:
        flash('Arquivo CSV não enviado.', 'danger')
        return redirect(url_for('avaliacoes.alunos'))

    try:
        conteudo = arquivo.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(conteudo))
        criados = 0
        for row in reader:
            nome = (row.get('NOME') or row.get('nome') or '').strip()
            celular = (row.get('CELULAR') or row.get('celular') or '').strip()
            horario = (row.get('HORARIO') or row.get('horario') or '').strip() or None
            if not nome or not celular:
                continue
            aluno = AlunoUnidade(nome=nome, celular=celular,
                                 horario=horario, departamento=departamento)
            db.session.add(aluno)
            criados += 1
        db.session.commit()
        flash(f'{criados} alunos importados com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao importar: {e}', 'danger')

    return redirect(url_for('avaliacoes.alunos'))


# ── Resultado público do colaborador (PRD §4 Etapa 6) ────────────────────────

@avaliacoes_public_bp.route('/r/resultado/<token_resultado>')
def ver_resultado(token_resultado):
    """Tela pessoal do colaborador com seu score, nível e feedback.
    Acessível via link enviado por WhatsApp ao fechar o ciclo (sem login).
    """
    sc = ScoreAvaliacao.query.filter_by(token_resultado=token_resultado).first_or_404()
    ciclo = CicloAvaliacao.query.get_or_404(sc.ciclo_id)

    from services.avaliacao_service import MENSAGENS_FEEDBACK, ACOES_RH, NIVEIS
    nivel = sc.nivel or 'bronze'
    emoji_map = {'diamante': '💎', 'ouro': '🥇', 'prata': '🥈', 'bronze': '🥉'}
    bonus_map = {'diamante': '15%', 'ouro': '10%', 'prata': '5%', 'bronze': '—'}

    return render_template(
        'avaliacoes/resultado.html',
        sc=sc,
        ciclo=ciclo,
        nivel_emoji=emoji_map.get(nivel, ''),
        bonus=bonus_map.get(nivel, '—') if sc.conclusivo else '—',
        mensagem_feedback=MENSAGENS_FEEDBACK.get(nivel, ''),
        acao_rh=ACOES_RH.get(nivel, ''),
    )


# ── Formulário público (sem login) ────────────────────────────────────────────

@avaliacoes_public_bp.route('/r/<token>')
def formulario(token):
    """Exibe o formulário de avaliação para o respondente."""
    tk = TokenAvaliacao.query.filter_by(token=token).first_or_404()

    if tk.respondido:
        return render_template('avaliacoes/ja_respondido.html')

    if tk.expira_em and datetime.utcnow() > tk.expira_em:
        return render_template('avaliacoes/expirado.html')

    perguntas = PERGUNTAS.get(tk.tipo, [])
    # Ordem aleatória dentro do bloco (reduz viés de ordem — PRD §2)
    perguntas_shuffled = perguntas.copy()
    random.shuffle(perguntas_shuffled)

    avaliado = Funcionario.query.get(tk.avaliado_id) if tk.avaliado_id else None
    titulo = TIPO_LABELS.get(tk.tipo, 'Avaliação')

    return render_template(
        'avaliacoes/formulario.html',
        token=token,
        tk=tk,
        titulo=titulo,
        perguntas=perguntas_shuffled,
        avaliado=avaliado,
    )


@avaliacoes_public_bp.route('/r/<token>/submit', methods=['POST'])
def submeter(token):
    """Recebe e persiste as respostas do formulário público."""
    tk = TokenAvaliacao.query.filter_by(token=token).first_or_404()

    if tk.respondido:
        return render_template('avaliacoes/ja_respondido.html')

    if tk.expira_em and datetime.utcnow() > tk.expira_em:
        return render_template('avaliacoes/expirado.html')

    perguntas = PERGUNTAS.get(tk.tipo, [])
    erros = []

    for p in perguntas:
        val = request.form.get(f'q{p["num"]}')
        if not val or not val.isdigit() or int(val) not in range(1, 6):
            erros.append(f'Resposta inválida para a pergunta {p["num"]}.')
            continue
        resp = RespostaAvaliacao(
            token_id=tk.id,
            questao_numero=p['num'],
            nota=int(val),
        )
        db.session.add(resp)

    if erros:
        db.session.rollback()
        flash(' '.join(erros), 'danger')
        return redirect(url_for('avaliacoes.formulario', token=token))

    tk.respondido = True
    tk.respondido_em = datetime.utcnow()
    db.session.commit()

    return render_template('avaliacoes/obrigado.html')


# ── Relatório PDF ────────────────────────────────────────────────────────────

@avaliacoes_bp.route('/ciclo/<int:ciclo_id>/pdf/<func_id>')
@login_required
def relatorio_pdf(ciclo_id, func_id):
    if not _admin_ou_gerente():
        abort(403)
    from services.avaliacao_service import gerar_relatorio_pdf
    try:
        buf = gerar_relatorio_pdf(ciclo_id, func_id)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('avaliacoes.detalhe_ciclo', ciclo_id=ciclo_id))

    func = Funcionario.query.get(func_id)
    nome = (func.nome.replace(' ', '_') if func else func_id)
    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=avaliacao360_{nome}_ciclo{ciclo_id}.pdf'
    return response


# ── Modo Teste ───────────────────────────────────────────────────────────────

@avaliacoes_bp.route('/teste', methods=['GET', 'POST'])
@login_required
def teste():
    """Gera um ciclo de teste e redireciona TODOS os envios WhatsApp para
    um único número informado. Útil para validar formulários e mensagens
    antes do disparo real.
    """
    if not _admin_ou_gerente():
        flash('Acesso restrito.', 'danger')
        return redirect(url_for('avaliacoes.index'))

    deps = db.session.query(Funcionario.departamento).filter(
        Funcionario.ativo == True, Funcionario.departamento.isnot(None)
    ).distinct().all()
    departamentos = sorted({d[0] for d in deps if d[0]})

    if request.method == 'GET':
        return render_template('avaliacoes/teste.html', departamentos=departamentos)

    # ── POST: executa o teste ──────────────────────────────────────────────────
    celular_teste = request.form.get('celular_teste', '').strip()
    departamento  = request.form.get('departamento') or None

    if not celular_teste:
        flash('Informe o número de telefone para teste.', 'danger')
        return render_template('avaliacoes/teste.html', departamentos=departamentos)

    # Normaliza: remove tudo que não for dígito
    celular_teste = ''.join(c for c in celular_teste if c.isdigit())

    from services.avaliacao_service import (
        criar_ciclo, PERGUNTAS, TIPO_LABELS, _url_base, _novo_token
    )
    from models import TokenAvaliacao, Funcionario as Func
    from services.whatsapp_bot import enviar_texto, enviar_botoes
    from datetime import timedelta

    # 1. Cria ciclo de teste (marcado no status para fácil identificação)
    ciclo = criar_ciclo(departamento=departamento)
    ciclo.status = 'teste'
    db.session.commit()

    url_base = _url_base()
    expira_em = datetime.utcnow() + timedelta(hours=72)

    # 2. Identifica o funcionário do celular de teste (necessário para o fluxo WhatsApp-first)
    #    O webhook identifica o respondente pelo celular; os tokens precisam ter avaliador_id
    #    apontando para ESTE funcionário para que _oferecer_proximo_token funcione em cadeia.
    digits_teste = celular_teste[-8:]
    func_teste = Func.query.filter(
        Func.celular.like(f'%{digits_teste}%'), Func.ativo == True
    ).first()

    from models import Usuario
    emails_gerentes = {u.email for u in Usuario.query.filter_by(nivel_acesso='gerente').all()}
    q = Func.query.filter_by(ativo=True)
    if departamento:
        q = q.filter_by(departamento=departamento)
    todos = q.limit(10).all()
    professores = [f for f in todos if not (f.email and f.email in emails_gerentes)]

    avaliado_ref = professores[0] if professores else (todos[0] if todos else func_teste)

    # avaliador_id = o funcionário do celular_teste (para a cadeia funcionar)
    # Se o número não está cadastrado como funcionário, usa referência genérica
    avaliador_id_teste = func_teste.id if func_teste else None

    tokens_criados = []
    for tipo in TIPO_LABELS:
        t = TokenAvaliacao(
            ciclo_id=ciclo.id,
            token=_novo_token(),
            tipo=tipo,
            avaliado_id=avaliado_ref.id if avaliado_ref else None,
            avaliador_id=avaliador_id_teste if tipo != 'aluno_por_equipe' else None,
            avaliador_nome='Aluno Teste' if tipo == 'aluno_por_equipe' else None,
            avaliador_celular=celular_teste if tipo == 'aluno_por_equipe' else None,
            expira_em=expira_em,
        )
        db.session.add(t)
        db.session.flush()
        tokens_criados.append(t)

    db.session.commit()

    # 3. Envia convite de teste para o celular informado
    #    - Aluno: link web (sem ChatState)
    #    - Funcionários: envia APENAS o convite do primeiro token não-aluno.
    #      Os demais são encadeados automaticamente via _oferecer_proximo_token
    #      após cada avaliação concluída.
    links_html = []
    enviados = 0
    primeiro_func_enviado = False

    for tk in tokens_criados:
        link = f'{url_base}/r/{tk.token}' if url_base else f'/r/{tk.token}'
        label = TIPO_LABELS.get(tk.tipo, tk.tipo)
        links_html.append({'label': label, 'link': link, 'token': tk.token, 'tipo': tk.tipo})

        if tk.tipo == 'aluno_por_equipe':
            msg = (
                f'🧪 *[TESTE] {label}*\n\n'
                f'Este é um envio de teste. Preencha o formulário para validar:\n'
                f'👉 {link}'
            )
            ok = enviar_texto(celular_teste, msg, tipo='avaliacao_teste')
        elif not primeiro_func_enviado:
            # Envia convite interativo apenas para o 1º token de funcionário.
            # Os demais serão oferecidos em cadeia após cada conclusão.
            n_perguntas = len(PERGUNTAS.get(tk.tipo, []))
            msg_convite = (
                f'🧪 *[TESTE] {label}*\n\n'
                f'São {n_perguntas} pergunta(s) pelo WhatsApp — uma de cada vez.\n'
                f'Ao terminar este formulário, o próximo será enviado automaticamente.\n\n'
                f'Deseja iniciar o teste agora?'
            )
            ok = enviar_botoes(
                celular=celular_teste,
                texto=msg_convite,
                botoes=[
                    {'id': f'avaliacao_iniciar_{tk.id}', 'title': '✅ Sim, iniciar'},
                    {'id': f'avaliacao_recusar_{tk.id}', 'title': '❌ Agora não'},
                ],
                tipo='avaliacao_teste_convite',
            )
            if ok:
                primeiro_func_enviado = True
        else:
            # Demais tokens: criados no banco mas convite enviado pela cadeia automática
            ok = True  # não conta como erro, apenas aguarda a cadeia

        if ok:
            tk.enviado_em = datetime.utcnow()
            enviados += 1

    db.session.commit()

    return render_template(
        'avaliacoes/teste_resultado.html',
        ciclo=ciclo,
        celular_teste=celular_teste,
        links=links_html,
        enviados=enviados,
        url_base=url_base,
    )


# ── API JSON ──────────────────────────────────────────────────────────────────

@avaliacoes_bp.route('/api/ciclos')
@login_required
def api_ciclos():
    ciclos = CicloAvaliacao.query.order_by(CicloAvaliacao.data_inicio.desc()).limit(10).all()
    return jsonify([{
        'id': c.id,
        'departamento': c.departamento,
        'data_inicio': str(c.data_inicio),
        'data_fim_coleta': str(c.data_fim_coleta),
        'status': c.status,
        'proximo_ciclo_data': str(c.proximo_ciclo_data) if c.proximo_ciclo_data else None,
    } for c in ciclos])
