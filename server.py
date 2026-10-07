import os
from datetime import date
from pathlib import Path
from statistics import mean
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title='Football Scouting API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

DB_CONFIG = {
    'dbname': os.getenv('POSTGRES_DB', 'football'),
    'user': os.getenv('POSTGRES_USER', 'scouting'),
    'password': os.getenv('POSTGRES_PASSWORD', 'scouting'),
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': os.getenv('POSTGRES_PORT', '5432'),
}
BASE_DIR = Path(__file__).resolve().parent

def fetch_all(query, params=()):
    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

@app.get('/')
def index():
    return FileResponse(BASE_DIR / 'index.html')

@app.get('/api/dashboard')
def dashboard():
    return {
        'jogadores': fetch_all('SELECT id, nome, numero, posicao_principal, altura, nacionalidade FROM jogadores ORDER BY nome'),
        'clubes': fetch_all('SELECT id, nome, nome_curto, pais FROM clubes ORDER BY nome'),
        'estatisticas': fetch_all('SELECT id, jogador_id, epoca, competicao, jogos, titularidades, minutos, golos, assistencias, remates_totais, remates_a_baliza, defesas, desarmes, intercecoes, passes_totais, passes_chave, passes_certos_pct, classificacao_media FROM estatisticas_jogador ORDER BY id DESC'),
        'disciplina': fetch_all('SELECT id, jogador_id, epoca, competicao, cartoes_amarelos, cartoes_vermelhos, expulsoes_segundo_amarelo, faltas_cometidas, faltas_sofridas FROM disciplina_jogador ORDER BY id DESC'),
        'sofascore': fetch_all('SELECT jogador_id, liga_id, golos, golos_esperados, assistencias, assistencias_esperadas, alivios FROM estatisticas_sofascore'),
        'valores_mercado': fetch_all('SELECT jogador_id, data, valor FROM valores_mercado WHERE valor IS NOT NULL ORDER BY jogador_id, data'),
        'lesoes': fetch_all('SELECT jogador_id, epoca, dias_indisponivel FROM lesoes_jogador WHERE dias_indisponivel IS NOT NULL'),
    }

@app.get('/api/jogadores')
def players(search: str = '', position: str = ''):
    query = '''SELECT id, nome, numero, posicao_principal, posicoes_secundarias,
                      pe_preferencial, altura, data_nascimento, nacionalidade,
                      outras_nacionalidades, contrato, transfermarkt_id,
                      url_transfermarkt FROM jogadores WHERE 1=1'''
    params = []
    if search:
        query += ' AND nome ILIKE %s'; params.append(f'%{search}%')
    if position:
        query += ''' AND (posicao_principal ILIKE %s OR EXISTS
                     (SELECT 1 FROM unnest(COALESCE(posicoes_secundarias, ARRAY[]::TEXT[])) p WHERE p ILIKE %s))'''
        params.extend([f'%{position}%', f'%{position}%'])
    query += ' ORDER BY nome'
    return fetch_all(query, tuple(params))

@app.get('/api/clubes')
def clubs():
    return fetch_all('''SELECT c.id, c.nome, c.nome_curto, c.pais, c.transfermarkt_id,
                               c.url_transfermarkt, COUNT(jc.jogador_id)::int AS total_jogadores
                        FROM clubes c LEFT JOIN jogador_clube jc ON jc.clube_id = c.id
                        GROUP BY c.id ORDER BY c.nome''')

@app.get('/api/clube/{clube_id}/trofeus')
def trofeus_clube(clube_id: int):
    return fetch_all(
        '''SELECT nome_trofeu, quantidade, epocas, fonte
           FROM trofeus_clube
           WHERE clube_id = %s
           ORDER BY quantidade DESC NULLS LAST, nome_trofeu''',
        (clube_id,)
    )

@app.get('/api/clube/{clube_id}/resultados')
def resultados_clube(clube_id: int):
    return fetch_all(
        '''SELECT data_jogo, adversario, em_casa, golos_casa, golos_fora, resultado, competicao, epoca,
                  golos, mvp_nome, mvp_rating, mvp_equipa, fase
           FROM resultados_clube
           WHERE clube_id = %s
           ORDER BY data_jogo DESC''',
        (clube_id,)
    )

@app.get('/api/clube/{clube_id}/mvp')
def mvp_clube(clube_id: int):
    # mvp_equipa != adversario em vez de comparar com o nome do clube
    # na nossa BD -- evita o mesmo problema de grafias diferentes
    # (ex: "SL Benfica" vs "Benfica") que já afetou a correspondência
    # de clubes; adversario já vem com o nome tal como a API o deu
    # para este jogo, por isso a comparação é sempre consistente.
    return fetch_all(
        '''SELECT mvp_nome AS jogador, COUNT(*) AS vezes
           FROM resultados_clube
           WHERE clube_id = %s AND mvp_nome IS NOT NULL AND mvp_equipa != adversario
           GROUP BY mvp_nome
           ORDER BY vezes DESC, mvp_nome''',
        (clube_id,)
    )

@app.get('/api/estatisticas')
def statistics(epoca: str = '', competicao: str = ''):
    query = '''SELECT e.id, j.nome, e.jogador_id, e.epoca, e.competicao,
                      e.jogos, e.titularidades, e.minutos, e.golos,
                      e.assistencias, e.classificacao_media, e.data_recolha
               FROM estatisticas_jogador e JOIN jogadores j ON j.id=e.jogador_id WHERE 1=1'''
    params = []
    if epoca:
        query += ' AND e.epoca ILIKE %s'; params.append(f'%{epoca}%')
    if competicao:
        query += ' AND e.competicao ILIKE %s'; params.append(f'%{competicao}%')
    query += ' ORDER BY e.classificacao_media DESC NULLS LAST, j.nome'
    return fetch_all(query, tuple(params))

@app.get('/api/disciplina')
def discipline(epoca: str = '', competicao: str = ''):
    query = '''SELECT d.id, j.nome, d.jogador_id, d.epoca, d.competicao,
                      d.cartoes_amarelos, d.cartoes_vermelhos,
                      d.expulsoes_segundo_amarelo, d.faltas_cometidas,
                      d.faltas_sofridas, d.suspensoes, d.jogos_falhados_castigo,
                      d.data_recolha
               FROM disciplina_jogador d JOIN jogadores j ON j.id=d.jogador_id WHERE 1=1'''
    params = []
    if epoca:
        query += ' AND d.epoca ILIKE %s'; params.append(f'%{epoca}%')
    if competicao:
        query += ' AND d.competicao ILIKE %s'; params.append(f'%{competicao}%')
    query += ' ORDER BY j.nome'
    return fetch_all(query, tuple(params))

@app.get('/api/epocas')
def epocas():
    rows = fetch_all('''SELECT DISTINCT epoca FROM estatisticas_jogador
                         UNION SELECT DISTINCT epoca FROM disciplina_jogador
                         ORDER BY epoca DESC''')
    return [r['epoca'] for r in rows]

@app.get('/api/classificacao')
def classificacao(liga: str = ''):
    query = '''SELECT cl.liga, cl.epoca, cl.posicao, c.id AS clube_id, c.nome AS clube,
                      c.nome_curto, c.transfermarkt_id AS clube_transfermarkt_id,
                      cl.jogos, cl.vitorias, cl.empates, cl.derrotas,
                      cl.golos_marcados, cl.golos_sofridos,
                      (cl.golos_marcados - cl.golos_sofridos) AS diferenca, cl.pontos
               FROM classificacao_liga cl
               JOIN clubes c ON c.id = cl.clube_id
               WHERE 1=1'''
    params = []
    if liga:
        query += ' AND cl.liga = %s'; params.append(liga)
    query += ' ORDER BY cl.liga, cl.posicao'
    return fetch_all(query, tuple(params))

@app.get('/api/jogadores/completo')
def players_full(epoca: str = '', competicao: str = ''):
    query = '''SELECT j.id AS jogador_id, j.nome, j.numero, j.posicao_principal,
                      j.nacionalidade, j.altura,
                      e.epoca, e.competicao, e.jogos, e.titularidades, e.minutos,
                      e.golos, e.assistencias, e.classificacao_media,
                      COALESCE(d.cartoes_amarelos, 0) AS cartoes_amarelos,
                      COALESCE(d.cartoes_vermelhos, 0) AS cartoes_vermelhos,
                      COALESCE(d.faltas_cometidas, 0) AS faltas_cometidas,
                      COALESCE(d.faltas_sofridas, 0) AS faltas_sofridas
               FROM jogadores j
               JOIN estatisticas_jogador e ON e.jogador_id = j.id
               LEFT JOIN disciplina_jogador d
                      ON d.jogador_id = j.id
                     AND d.epoca = e.epoca
                     AND d.competicao = e.competicao
               WHERE 1=1'''
    params = []
    if epoca:
        query += ' AND e.epoca = %s'; params.append(epoca)
    if competicao:
        query += ' AND e.competicao ILIKE %s'; params.append(f'%{competicao}%')
    query += ' ORDER BY e.minutos DESC NULLS LAST, j.nome'
    rows = fetch_all(query, tuple(params))
    for r in rows:
        jogos = r['jogos'] or 0
        r['pct_amarelos'] = round((r['cartoes_amarelos'] or 0) / jogos * 100, 1) if jogos else 0
        r['pct_vermelhos'] = round((r['cartoes_vermelhos'] or 0) / jogos * 100, 1) if jogos else 0
        r['media_minutos_jogo'] = round(r['minutos'] / jogos, 0) if jogos and r['minutos'] else 0
    return rows

def _safe_div(a, b):
    if not b:
        return None
    return (a or 0) / b

def _por90(a, minutos):
    """
    Valor por 90 minutos jogados -- NUNCA por jogo. Golos/jogo e
    Golos/90 só coincidem para quem joga sempre os 90 minutos
    completos; para suplentes/rotativos, dividir por jogos em vez
    de minutos infla ou esconde a taxa real (ex: 2 golos em 300
    minutos espalhados por 10 jogos é 0.2/jogo mas 0.6/90 -- o
    valor por 90 é o que é comparável entre jogadores com
    utilizações diferentes).
    """
    if not minutos:
        return None
    return (a or 0) / minutos * 90

def _pct(a, b):
    if not b:
        return None
    return round((a or 0) / b * 100, 1)

def _idade(data_nascimento):
    if not data_nascimento:
        return None
    hoje = date.today()
    anos = hoje.year - data_nascimento.year
    if (hoje.month, hoje.day) < (data_nascimento.month, data_nascimento.day):
        anos -= 1
    return anos

def _rating(j):
    return float(j['classificacao_media']) if j.get('classificacao_media') is not None else None

def _pct_passe_certo(j):
    """
    % de passe certo -- temos a mesma métrica em duas fontes
    (API-Football e Sofascore). Em caso de dúvida, o Sofascore
    ganha sempre (dados mais detalhados e mais fiáveis); só se usa
    a API-Football quando não há valor do Sofascore para o jogador.
    """
    if j.get('sofa_passes_certos_pct') is not None:
        return float(j['sofa_passes_certos_pct'])
    if j.get('passes_certos_pct') is not None:
        return float(j['passes_certos_pct'])
    return None

# Métricas usadas no score de scouting e no radar, por posição
# (tal como vêm da API-Football: Goalkeeper / Defender / Midfielder / Forward).
# Cada uma é calculada a partir de valores brutos e depois comparada com os
# colegas de posição (percentil), para dar um número comparável entre 0-100
# mesmo com estatísticas de escalas muito diferentes (ex: golos vs passes).
METRICAS_POR_POSICAO = {
    'Goalkeeper': [
        ('defesas_90', 'Defesas/90', lambda j: _por90(j['defesas'], j['minutos'])),
        ('pct_remates_salvos', '% Remates Salvos', lambda j: _pct(j['defesas'], (j['defesas'] or 0) + (j['golos_sofridos'] or 0))),
        ('regularidade', 'Regularidade', lambda j: _safe_div(j['minutos'], j['jogos'])),
        ('penaltis_defendidos', 'Pén. Defendidos', lambda j: j['penaltis_defendidos'] if j['penaltis_defendidos'] is not None else None),
        ('rating', 'Solidez', _rating),
    ],
    'Defender': [
        ('pct_duelos', '% Duelos Ganhos', lambda j: _pct(j['duelos_ganhos'], j['duelos_totais'])),
        ('desarmes_90', 'Desarmes/90', lambda j: _por90(j['desarmes'], j['minutos'])),
        ('intercecoes_90', 'Interceções/90', lambda j: _por90(j['intercecoes'], j['minutos'])),
        ('pct_passes', '% Passe Certo', _pct_passe_certo),
        ('rating', 'Rating', _rating),
    ],
    'Midfielder': [
        ('pct_passes', '% Passe Certo', _pct_passe_certo),
        ('passes_chave_90', 'Passes-Chave/90', lambda j: _por90(j['passes_chave'], j['minutos'])),
        ('pct_duelos', '% Duelos Ganhos', lambda j: _pct(j['duelos_ganhos'], j['duelos_totais'])),
        ('assist_90', 'Assist./90', lambda j: _por90(j['assistencias'], j['minutos'])),
        ('rating', 'Rating', _rating),
    ],
    'Forward': [
        ('golos_90', 'Golos/90', lambda j: _por90(j['golos'], j['minutos'])),
        ('assist_90', 'Assist./90', lambda j: _por90(j['assistencias'], j['minutos'])),
        ('pct_remates', '% Remates à Baliza', lambda j: _pct(j['remates_a_baliza'], j['remates_totais'])),
        ('pct_dribles', '% Dribles Conseguidos', lambda j: _pct(j['dribles_conseguidos'], j['dribles_tentados'])),
        ('rating', 'Rating', _rating),
    ],
}

# Métricas usadas só para colorir "chips" tipo Football Manager nos
# atributos per-90/percentagem do perfil (golos/assistências brutos
# não entram aqui de propósito -- dependem demasiado dos minutos
# jogados para serem comparáveis diretamente entre jogadores).
# Percentil calculado sempre dentro do grupo posicional do jogador,
# tal como o radar, mas independente das métricas do scouting_score.
CHIP_METRICAS = {
    'golos_90': lambda j: _por90(j['golos'], j['minutos']),
    'assist_90': lambda j: _por90(j['assistencias'], j['minutos']),
    'pct_passes': _pct_passe_certo,
    'pct_duelos': lambda j: _pct(j['duelos_ganhos'], j['duelos_totais']),
    'pct_remates_salvos': lambda j: _pct(j['defesas'], (j['defesas'] or 0) + (j['golos_sofridos'] or 0)),
    'pct_dribles': lambda j: _pct(j['dribles_conseguidos'], j['dribles_tentados']),
}

def _percentil(valor, todos):
    valores = [v for v in todos if v is not None]
    if valor is None or not valores:
        return None
    menores_ou_iguais = sum(1 for v in valores if v <= valor)
    return round(menores_ou_iguais / len(valores) * 100, 1)

def _calcular_scouting(epoca: str = '', competicao: str = ''):
    query = '''SELECT j.id AS jogador_id, j.nome, j.numero, j.posicao_principal,
                      j.posicoes_secundarias,
                      j.nacionalidade, j.altura, j.pe_preferencial, j.contrato,
                      COALESCE(vm.valor, j.valor_mercado) AS valor_mercado,
                      j.data_nascimento,
                      e.epoca, e.competicao, e.equipa, e.posicao_api,
                      e.jogos, e.titularidades, e.minutos, e.golos, e.assistencias, e.classificacao_media,
                      e.remates_totais, e.remates_a_baliza, e.golos_sofridos, e.defesas,
                      e.passes_totais, e.passes_chave, e.passes_certos_pct,
                      e.duelos_totais, e.duelos_ganhos, e.desarmes, e.bloqueios, e.intercecoes,
                      e.dribles_tentados, e.dribles_conseguidos,
                      e.penaltis_marcados, e.penaltis_falhados, e.penaltis_defendidos,
                      COALESCE(d.cartoes_amarelos, 0) AS cartoes_amarelos,
                      COALESCE(d.cartoes_vermelhos, 0) AS cartoes_vermelhos,
                      c.transfermarkt_id AS clube_transfermarkt_id,
                      sf.passes_certos_pct AS sofa_passes_certos_pct,
                      sf.golos_esperados AS golos_esperados,
                      (taca.jogador_id IS NOT NULL) AS jogou_taca_portugal,
                      emp.clube_destino AS clube_dono,
                      les.tipo_lesao AS lesao_atual_tipo,
                      les.dias_indisponivel AS lesao_atual_dias
               FROM jogadores j
               JOIN estatisticas_jogador e ON e.jogador_id = j.id
               LEFT JOIN disciplina_jogador d
                      ON d.jogador_id = j.id AND d.epoca = e.epoca AND d.competicao = e.competicao
               LEFT JOIN LATERAL (
                      SELECT valor FROM valores_mercado
                      WHERE jogador_id = j.id AND valor IS NOT NULL
                      ORDER BY data DESC LIMIT 1
               ) vm ON true
               LEFT JOIN LATERAL (
                      -- NÃO filtra por "temporada = e.epoca": nem todas
                      -- as ligas usam a mesma notação de época em
                      -- jogador_clube.temporada (ex: Eliteserien usa
                      -- "2025", ano civil, enquanto estatisticas_jogador
                      -- usa sempre "2024/25" via api_stats.py) -- filtrar
                      -- por igualdade estrita deixava o clube (e por
                      -- tabela, o escudo) sempre vazio para essas ligas.
                      -- Pega sempre o clube mais recente do jogador.
                      SELECT clube_id FROM jogador_clube
                      WHERE jogador_id = j.id
                      ORDER BY data_entrada DESC NULLS LAST LIMIT 1
               ) jc ON true
               LEFT JOIN clubes c ON c.id = jc.clube_id
               LEFT JOIN LATERAL (
                      SELECT passes_certos_pct, golos_esperados FROM estatisticas_sofascore
                      WHERE jogador_id = j.id
                      ORDER BY jogos DESC NULLS LAST LIMIT 1
               ) sf ON true
               LEFT JOIN LATERAL (
                      -- Taça de Portugal 2024/25 = liga_id 336 na
                      -- Sofascore (ver importar_sofascore_taca_portugal.py).
                      -- Só serve para assinalar "jogou a Taça" na
                      -- pesquisa -- os dados em si lêem-se à parte, via
                      -- /api/jogador/{id}/sofascore?liga_id=336.
                      SELECT jogador_id FROM estatisticas_sofascore
                      WHERE jogador_id = j.id AND liga_id = 336 LIMIT 1
               ) taca ON true
               LEFT JOIN LATERAL (
                      -- Se há um "Fim do empréstimo" agendado a SAIR do
                      -- clube atual do jogador, é porque ele está lá
                      -- emprestado (clube_destino desse registo é o
                      -- dono/clube de origem). Ver conversa: valida-se
                      -- com casos reais (ex: Sambi Lokonga no Sevilha,
                      -- "Fim do empréstimo" Sevilha->Arsenal).
                      -- Comparação por substring (não igualdade exata):
                      -- o Transfermarkt às vezes regista o clube_origem
                      -- sem o prefixo que a nossa tabela `clubes` usa
                      -- (ex: transferências com "Benfica", clubes com
                      -- "SL Benfica") -- a igualdade estrita deixava
                      -- esses casos passar como "não emprestado" por
                      -- engano (caso real: Zeki Amdouni, Burnley ->
                      -- Benfica 2024/25, só detetado porque o "Fim do
                      -- empréstimo" de volta ao Burnley nunca batia
                      -- certo com "SL Benfica").
                      SELECT clube_destino FROM transferencias_jogador t
                      WHERE t.jogador_id = j.id
                        AND t.tipo ILIKE '%%Fim do empr%%'
                        AND (c.nome ILIKE '%%' || t.clube_origem || '%%'
                             OR t.clube_origem ILIKE '%%' || c.nome || '%%')
                      ORDER BY t.data_transferencia DESC LIMIT 1
               ) emp ON true
               LEFT JOIN LATERAL (
                      -- data_fim IS NULL = Transfermarkt ainda não
                      -- registou volta a jogar, ou seja, lesão em
                      -- curso à data da última recolha.
                      SELECT tipo_lesao, dias_indisponivel FROM lesoes_jogador
                      WHERE jogador_id = j.id AND data_fim IS NULL
                      ORDER BY data_inicio DESC LIMIT 1
               ) les ON true
               WHERE e.posicao_api IS NOT NULL'''
    params = []
    if epoca:
        query += ' AND e.epoca = %s'; params.append(epoca)
    if competicao:
        query += ' AND e.competicao ILIKE %s'; params.append(f'%{competicao}%')
    jogadores = fetch_all(query, tuple(params))

    # A API-Football não é consistente a etiquetar a posição --
    # alguns jogadores vêm com "Attacker" em vez de "Forward" para o
    # mesmo grupo posicional. METRICAS_POR_POSICAO só conhece
    # 'Forward', por isso sem isto esses jogadores caíam no fallback
    # ('Midfielder') e eram comparados com métricas de médio (descoberto
    # ao testar o agrupamento por competição: 138 jogadores afetados).
    for j in jogadores:
        if j['posicao_api'] == 'Attacker':
            j['posicao_api'] = 'Forward'

    # Agrupado por posição + competição, não só posição -- a
    # qualidade da competição varia muito entre ligas (ex: La Liga vs
    # Segunda Liga vs Eliteserien), por isso comparar um avançado da
    # Segunda Liga com um da La Liga no mesmo pool de percentil dava
    # scores enganadores (um avançado medíocre numa liga fraca podia
    # sair com score mais alto que um avançado decente numa liga
    # forte, só por as defesas à frente serem piores).
    por_posicao = {}
    for j in jogadores:
        chave_grupo = (j['posicao_api'], j['competicao'])
        por_posicao.setdefault(chave_grupo, []).append(j)

    # Com poucos minutos, uma taxa por-90 (ex: 1 golo em 1 jogo = "1
    # golo/90 no percentil 100") não é um sinal fiável -- é ruído
    # estatístico apresentado como se fosse confiança total. Por
    # isso, só entram no "pool" de comparação (e só recebem
    # percentil próprio) os jogadores com pelo menos 10 jogos
    # completos; os restantes mantêm as estatísticas em bruto, mas
    # sem score/percentis a fingir uma certeza que não existe.
    MIN_MINUTOS_PERCENTIL = 900

    # "Diamante": jovem com sinais reais de qualidade -- não uma nota
    # de potencial fictícia (tipo FM), mas jovem + score de scouting
    # já alto dentro do grupo posicional, com amostra fiável.
    IDADE_MAX_POTENCIAL = 21
    SCORE_MIN_POTENCIAL = 70

    # Abaixo disto, o grupo de comparação (posição+liga) é pequeno
    # demais para um percentil dizer muito -- ex: só 4 guarda-redes
    # qualificados na Liga Portugal 2 quer dizer que um percentil 100
    # é só "o melhor de 4", não um sinal fiável de qualidade.
    MIN_POOL_PERCENTIL = 15

    for (posicao, _competicao), lista in por_posicao.items():
        metricas = METRICAS_POR_POSICAO.get(posicao, METRICAS_POR_POSICAO['Midfielder'])

        qualificados = [j for j in lista if (j['minutos'] or 0) >= MIN_MINUTOS_PERCENTIL]
        pool_metricas = {chave: [fn(j) for j in qualificados] for chave, _label, fn in metricas}
        pool_chip = {chave: [fn(j) for j in qualificados] for chave, fn in CHIP_METRICAS.items()}
        pool_suficiente = len(qualificados) >= MIN_POOL_PERCENTIL

        for j in lista:
            amostra_suficiente = (j['minutos'] or 0) >= MIN_MINUTOS_PERCENTIL

            radar = []
            percentis = []
            for chave, label, fn in metricas:
                valor = fn(j)
                p = _percentil(valor, pool_metricas[chave]) if amostra_suficiente else None
                radar.append({'eixo': label, 'valor': valor, 'percentil': p if p is not None else 0})
                if p is not None:
                    percentis.append(p)
            j['radar'] = radar
            j['amostra_suficiente'] = amostra_suficiente
            j['pool_suficiente'] = pool_suficiente
            j['pool_tamanho'] = len(qualificados)
            j['scouting_score'] = round(mean(percentis)) if percentis else None
            j['pct_amarelos'] = _pct(j['cartoes_amarelos'], j['jogos']) or 0

            idade_jogador = _idade(j['data_nascimento'])
            j['jovem_potencial'] = bool(
                amostra_suficiente
                and j['scouting_score'] is not None
                and j['scouting_score'] >= SCORE_MIN_POTENCIAL
                and idade_jogador is not None
                and idade_jogador <= IDADE_MAX_POTENCIAL
            )

            j['percentis_chip'] = {
                chave: (_percentil(fn(j), pool_chip[chave]) if amostra_suficiente else None)
                for chave, fn in CHIP_METRICAS.items()
            }

    resultado = [j for lista in por_posicao.values() for j in lista]
    resultado.sort(key=lambda j: j['scouting_score'] if j['scouting_score'] is not None else -1, reverse=True)
    return resultado

@app.get('/api/scouting')
def scouting(epoca: str = '', competicao: str = ''):
    return _calcular_scouting(epoca, competicao)

@app.get('/api/jogador/{jogador_id}/similares')
def jogadores_similares(jogador_id: int, epoca: str = '', limite: int = 4):
    """
    Jogadores com perfil (estilo de jogo) parecido, não com nível
    parecido -- a distância é calculada sobre o vetor de percentis do
    radar (já usado no scouting_score), dentro do mesmo grupo
    posicional. Dois jogadores com o mesmo perfil de percentis (ex:
    ambos "finalizadores puros": Golos/90 e Rating altos, Assist./90
    mais baixo) ficam próximos mesmo que um tenha um scouting_score
    muito maior que o outro -- é semelhança de estilo, não de
    qualidade.

    Agrupado em 3 bandas de valor de mercado relativas ao alvo (ex:
    alvo=50M -> "inferior" <=25M, "igual" 25-75M, "superior" >75M),
    para responder a "encontra-me alternativas mais baratas" ou
    "opções de nível superior", não só puros clones de preço. Uma
    banda pode legitimamente vir vazia (ex: não há ninguém mais caro
    que o jogador mais caro da liga) -- devolve-se vazia, não se força
    nada.
    """
    jogadores = _calcular_scouting(epoca, '')
    alvo = next((j for j in jogadores if j['jogador_id'] == jogador_id), None)
    if not alvo or not alvo.get('amostra_suficiente') or not alvo.get('radar'):
        return {'valor_mercado_alvo': None, 'bandas': None}

    def vetor(j):
        return [eixo['percentil'] for eixo in j['radar']]

    v_alvo = vetor(alvo)
    dist_max = (len(v_alvo) ** 0.5) * 100  # distância euclidiana máxima possível (0 a 100 por eixo)

    def distancia(j):
        return sum((a - b) ** 2 for a, b in zip(v_alvo, vetor(j))) ** 0.5

    def formatar(j):
        return {
            'jogador_id': j['jogador_id'],
            'nome': j['nome'],
            'equipa': j['equipa'],
            'competicao': j['competicao'],
            'clube_transfermarkt_id': j['clube_transfermarkt_id'],
            'posicao_principal': j['posicao_principal'],
            'scouting_score': j['scouting_score'],
            'valor_mercado': j.get('valor_mercado'),
            'semelhanca_pct': round((1 - distancia(j) / dist_max) * 100) if dist_max else None,
        }

    candidatos = sorted(
        (j for j in jogadores
         if j['jogador_id'] != jogador_id
         and j['posicao_api'] == alvo['posicao_api']
         and j.get('amostra_suficiente')
         and j.get('radar')),
        key=distancia,
    )

    valor_alvo = alvo.get('valor_mercado')
    if not valor_alvo:
        # sem valor de mercado conhecido para o alvo -- não dá para
        # bandas, mas ainda faz sentido devolver os mais parecidos
        return {'valor_mercado_alvo': None, 'bandas': None, 'geral': [formatar(j) for j in candidatos[:limite]]}

    grupos = {'superior': [], 'igual': [], 'inferior': []}
    for j in candidatos:
        v = j.get('valor_mercado')
        if not v:
            continue
        razao = v / valor_alvo
        if razao > 1.5:
            grupos['superior'].append(j)
        elif razao >= 0.5:
            grupos['igual'].append(j)
        else:
            grupos['inferior'].append(j)

    return {
        'valor_mercado_alvo': valor_alvo,
        'bandas': {chave: [formatar(j) for j in lista[:limite]] for chave, lista in grupos.items()},
    }

@app.get('/api/jogador/{jogador_id}/jogos_por_epoca')
def jogos_por_epoca(jogador_id: int):
    return fetch_all(
        '''SELECT epoca, SUM(jogos)::int AS jogos, SUM(minutos)::int AS minutos
           FROM estatisticas_jogador
           WHERE jogador_id = %s
           GROUP BY epoca ORDER BY epoca''',
        (jogador_id,)
    )

@app.get('/api/jogador/{jogador_id}/valor_mercado')
def valor_mercado_historico(jogador_id: int):
    return fetch_all(
        '''SELECT data, valor, clube, idade FROM valores_mercado
           WHERE jogador_id = %s ORDER BY data''',
        (jogador_id,)
    )

@app.get('/api/jogador/{jogador_id}/transferencias')
def transferencias_historico(jogador_id: int):
    return fetch_all(
        '''SELECT clube_origem, clube_destino, data_transferencia, tipo, valor, epoca
           FROM transferencias_jogador
           WHERE jogador_id = %s ORDER BY data_transferencia DESC''',
        (jogador_id,)
    )

@app.get('/api/jogador/{jogador_id}/salario')
def salario_historico(jogador_id: int):
    return fetch_all(
        '''SELECT ano, temporada, clube_nome, liga, salario_semanal,
                  salario_anual, moeda, contrato_ate
           FROM salarios_jogadores
           WHERE jogador_id = %s ORDER BY ano DESC''',
        (jogador_id,)
    )

@app.get('/api/jogador/{jogador_id}/lesoes')
def lesoes_historico(jogador_id: int):
    return fetch_all(
        '''SELECT tipo_lesao, data_inicio, data_fim, dias_indisponivel, jogos_falhados, epoca
           FROM lesoes_jogador
           WHERE jogador_id = %s ORDER BY data_inicio DESC''',
        (jogador_id,)
    )

@app.get('/api/jogador/{jogador_id}/ultimos5')
def ultimos5_jogos(jogador_id: int):
    return fetch_all(
        '''SELECT data_jogo, adversario, em_casa, golos_casa, golos_fora,
                  resultado_equipa, minutos, titular, golos, assistencias, rating,
                  defesas, golos_sofridos, penaltis_defendidos
           FROM ultimos_jogos_jogador
           WHERE jogador_id = %s
           ORDER BY data_jogo DESC
           LIMIT 5''',
        (jogador_id,)
    )

@app.get('/api/jogador/{jogador_id}/sofascore')
def sofascore_detalhe(jogador_id: int, liga_id: int = None):
    if liga_id is not None:
        # Pedido de uma competição específica (ex: Taça de Portugal,
        # liga_id=336) -- o jogador pode não ter jogado essa prova, daí
        # devolver None em vez de cair para a liga com mais jogos.
        linhas = fetch_all(
            '''SELECT * FROM estatisticas_sofascore
               WHERE jogador_id = %s AND liga_id = %s''',
            (jogador_id, liga_id)
        )
        return linhas[0] if linhas else None

    linhas = fetch_all(
        '''SELECT * FROM estatisticas_sofascore
           WHERE jogador_id = %s ORDER BY jogos DESC NULLS LAST''',
        (jogador_id,)
    )
    if not linhas:
        return None
    return linhas[0]

@app.get('/api/jogador/{jogador_id}/heatmap')
def heatmap_detalhe(jogador_id: int):
    linhas = fetch_all(
        '''SELECT h.pontos FROM heatmap_sofascore h
           JOIN estatisticas_sofascore e
                  ON e.jogador_id = h.jogador_id AND e.liga_id = h.liga_id AND e.epoca_id = h.epoca_id
           WHERE h.jogador_id = %s ORDER BY e.jogos DESC NULLS LAST LIMIT 1''',
        (jogador_id,)
    )
    if not linhas:
        return []
    return linhas[0]['pontos']

@app.get('/api/health')
def health():
    try:
        fetch_all('SELECT 1 AS status')
        return {'status': 'ok'}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
