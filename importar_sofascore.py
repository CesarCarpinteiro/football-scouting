"""
Importa estatísticas avançadas da Sofascore (duelos aéreos, ações
defensivas, xG, xA, guarda-redes avançado, etc.) para os jogadores que já
tens na BD (populados pelo import_ligapt.py a partir do Transfermarkt).

ATENÇÃO: isto usa a API JSON interna da Sofascore, que NÃO É oficial nem
documentada -- pode mudar de estrutura ou deixar de funcionar sem aviso.

Fluxo, por cada liga/época configurada (ver LIGAS abaixo):
  1. Lista TODOS os jogadores da liga/época via paginação em massa
     (`/unique-tournament/{id}/season/{id}/statistics?limit=100&offset=N`)
     -- nunca via `/search/all?q=nome`, que ficou pouco fiável (bloqueios
     403 consistentes, provável causa: parece mais vigiado do que o
     endpoint de listagem).
  2. Para cada jogador da listagem, tenta encontrar o `jogador_id` local
     correspondente (por nome, e por clube quando ajuda a desambiguar).
  3. Pede as estatísticas completas de época em
     `/player/{id}/unique-tournament/{liga_id}/season/{epoca_id}/statistics/overall`
     (é este pedido que tem os ~105 campos todos, a listagem em massa só
     tem um resumo).
  4. Guarda tudo em estatisticas_sofascore (upsert).

Um 404 é normal e esperado (jogador sem minutos suficientes na
competição/época) -- o script regista e continua, não é erro fatal.

PEDIDOS HTTP -- migrado de Playwright para wreq (2026-09-28):
antes disparávamos os pedidos via `page.evaluate(fetch(...))` num browser
Chromium real, porque a API da Sofascore devolve 403 a pedidos feitos com
`requests`/`httpx` normais. Confirmámos que o `wreq` (cliente HTTP com
fingerprint TLS/JA3/JA4 real de Chrome, emulation=Chrome149) passa o mesmo
endpoint sem nenhum aquecimento de sessão -- nem visitar a homepage nem a
página da liga foi preciso no teste. Isto tira a dependência de um browser
completo (mais rápido, sem Playwright a arrancar Chromium), mas o limite de
bloqueio por IP/volume observado com Playwright é independente do cliente
HTTP usado, por isso o resto da estratégia (corridas pequenas, checkpoint,
parar imediatamente num 403) mantém-se tal e qual.

BLOQUEIO CLOUDFLARE -- estado do que já se percebeu (2026-09-28), da
corrida ao Ligue 1 (com Playwright): a listagem em massa (~6 pedidos por
liga) nunca bloqueou; o bloqueio aparece sempre a meio dos pedidos
individuais de estatísticas por jogador, tipicamente por volta dos 90-100
jogadores dentro da mesma corrida -- e nem reutilizar/renovar cookies nem
esperar mais tempo a meio da corrida mudou quando aparece, o que sugere que
o limite está ligado ao IP e/ou ao volume total de pedidos numa janela de
tempo, não a nada que se controle do lado do cliente. Por isso não se tenta
contornar: a estratégia é só limitar cada CORRIDA a
LIMITE_JOGADORES_POR_CORRIDA jogadores e parar de forma limpa antes de
bater no limite, retomando numa corrida seguinte (minutos/horas depois) --
o checkpoint (progresso_sofascore.json) trata de continuar exatamente onde
ficou, sem repetir trabalho. Um 403 já não tenta recuperar (RETRIES_403=0)
-- para tudo de imediato. Ainda por confirmar se o limiar muda com o wreq
(pode ser maior, menor, ou igual -- é o mesmo IP a fazer os pedidos).

Nunca disfarça um 403 como "sem correspondência": se acontecer, o
script para com RuntimeError em vez de continuar como se o jogador não
existisse, para nunca gravar um checkpoint de "concluído" para algo
que na verdade nunca conseguimos consultar (já aconteceu perder >250
jogadores da La Liga de uma vez por causa disto, antes desta proteção
existir).
"""

import asyncio
import json
import random
import time
import unicodedata

import psycopg
from wreq import Client, Emulation

# ==========================================
# configuração
# ==========================================

LIGAS = [
    {"liga_id": 8, "epoca_id": 61643, "epoca_nome": "24/25", "nome": "La Liga", "competicao": "La Liga",
     "pagina": "https://www.sofascore.com/tournament/football/spain/laliga/8"},
    {"liga_id": 238, "epoca_id": 63670, "epoca_nome": "24/25", "nome": "Primeira Liga", "competicao": "Primeira Liga",
     "pagina": "https://www.sofascore.com/football/tournament/portugal/liga-portugal-betclic/238"},
    {"liga_id": 239, "epoca_id": 63676, "epoca_nome": "24/25", "nome": "Liga Portugal 2", "competicao": "Segunda Liga",
     "pagina": "https://www.sofascore.com/football/tournament/portugal/liga-portugal-2/239"},
    {"liga_id": 34, "epoca_id": 61736, "epoca_nome": "24/25", "nome": "Ligue 1", "competicao": "Ligue 1",
     "pagina": "https://www.sofascore.com/football/tournament/france/ligue-1/34"},
    {"liga_id": 20, "epoca_id": 70174, "epoca_nome": "2025", "nome": "Eliteserien", "competicao": "Eliteserien",
     "pagina": "https://www.sofascore.com/football/tournament/norway/eliteserien/20"},
    {"liga_id": 38, "epoca_id": 61459, "epoca_nome": "24/25", "nome": "Pro League", "competicao": "Jupiler Pro League",
     "pagina": "https://www.sofascore.com/football/tournament/belgium/pro-league/38"},
]

# Se só quiseres correr UMA liga de cada vez (mais seguro), põe o nome
# aqui (tem de bater certo com "nome" em LIGAS); deixa None para
# correr todas de seguida (com pausas maiores entre elas).
LIGA_A_CORRER = "Pro League"

BASE_URL = "https://www.sofascore.com/api/v1"

PROGRESSO_FICHEIRO = "progresso_sofascore.json"

# Perfil de emulação TLS/JA3/JA4 do wreq. Chrome149 foi o que confirmámos
# que passa a Cloudflare da Sofascore sem cair num desafio JS.
EMULATION = Emulation.Chrome149

# pausas aleatórias entre pedidos individuais (segundos)
SLEEP_MIN = 4.0
SLEEP_MAX = 8.0

# Limite de jogadores processados por CORRIDA do script (não por liga).
# O bloqueio tende a aparecer por volta dos 90-100 jogadores dentro da
# mesma corrida -- por isso, em vez de tentar evitar o bloqueio, paramos
# de forma limpa perto desse valor e retomamos numa corrida seguinte (o
# checkpoint trata de continuar sem repetir trabalho). Põe None para
# desligar o limite.
LIMITE_JOGADORES_POR_CORRIDA = 90

# pausa entre ligas diferentes, na mesma corrida
PAUSA_ENTRE_LIGAS_MIN = 15
PAUSA_ENTRE_LIGAS_MAX = 30

# Retries em 403 antes de desistir: 0 -- um 403 para tudo de imediato
# em vez de insistir com esperas de 45s/90s. O checkpoint
# (progresso_sofascore.json) trata de retomar mais tarde.
RETRIES_403 = 0
ESPERA_BASE_403 = 45


def sleep_pedido():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


# ==========================================
# ligação à base de dados
# ==========================================

def ligar_bd():
    return psycopg.connect(
        host="localhost",
        port=5432,
        dbname="football",
        user="scouting",
        password="scouting"
    )


def obter_jogadores_bd(conn):
    """
    Devolve a lista de jogadores guardados na BD, com o clube mais
    recente conhecido (via jogador_clube + clubes), para ajudar a
    desambiguar a correspondência com a listagem da Sofascore.

    NÃO filtra por uma "temporada" fixa: as ligas em LIGAS não usam
    todas a mesma notação de época (24/25 para as ligas cruzadas,
    "2025" para o Eliteserien, que joga por ano civil) -- filtrar por
    um valor fixo deixava o clube sempre vazio para quem não bate
    certo com esse valor, perdendo a proteção de desambiguação por
    clube nessas ligas.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT j.id, j.nome, j.sofascore_id, c.nome
        FROM jogadores j
        LEFT JOIN LATERAL (
            SELECT clube_id FROM jogador_clube
            WHERE jogador_id = j.id
            ORDER BY data_entrada DESC NULLS LAST LIMIT 1
        ) jc ON true
        LEFT JOIN clubes c ON c.id = jc.clube_id
        ORDER BY j.nome
    """)
    linhas = cursor.fetchall()
    cursor.close()

    vistos = set()
    jogadores = []
    for jogador_id, nome, sofascore_id, clube in linhas:
        if jogador_id in vistos:
            continue
        vistos.add(jogador_id)
        jogadores.append({
            "id": jogador_id,
            "nome": nome,
            "sofascore_id": sofascore_id,
            "clube": clube,
        })
    return jogadores


def obter_ids_da_competicao(conn, competicao):
    """
    IDs dos jogadores que realmente jogam nesta competição (via
    estatisticas_jogador.competicao). Sem isto, tentaríamos cruzar
    TODOS os jogadores locais contra a listagem de cada liga --
    incluindo, por exemplo, jogadores portugueses contra a La Liga --
    arriscando falsas correspondências por nome parecido.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT jogador_id FROM estatisticas_jogador WHERE competicao = %s",
        (competicao,),
    )
    ids = {linha[0] for linha in cursor.fetchall()}
    cursor.close()
    return ids


def gravar_sofascore_id(conn, jogador_id, sofascore_id):
    """
    Grava o sofascore_id encontrado. Se este sofascore_id já estiver
    associado a OUTRO jogador_id, a nossa própria BD já tem a prova de
    que este ID pertence a outra pessoa -- por isso NÃO é seguro
    continuar a pedir/guardar estatísticas para o jogador_id atual com
    este mesmo sofascore_id (já aconteceu: dois jogadores reais
    chamados "João Mário" ficaram ligados ao mesmo sofascore_id, e um
    deles ficou com as estatísticas erradas). Devolve True só se a
    associação for mesmo gravada.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE jogadores
            SET sofascore_id = %s
            WHERE id = %s AND sofascore_id IS NULL
        """, (sofascore_id, jogador_id))
        conn.commit()
        return True
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        print(
            f"  [aviso] sofascore_id={sofascore_id} já pertence a outro "
            f"jogador_id -- provável duplicado de nome na nossa BD, a "
            f"ignorar esta correspondência por completo (jogador_id={jogador_id})."
        )
        return False
    finally:
        cursor.close()


# ==========================================
# normalização de nomes
# ==========================================

# Letras escandinavas (æ, ø, å) não são "acentos" no sentido NFKD --
# não se decompõem em letra base + marca combinável, por isso o
# unicodedata.normalize("NFKD", ...) abaixo não as trata, e o
# .encode("ascii", "ignore") simplesmente APAGA-as por completo (ex:
# "Selnæs" -> "Selns", não "Selnaes") em vez de as transliterar.
# Descoberto com o Eliteserien: "Ole Selnæs" na nossa BD nunca batia
# certo com "Ole Selnaes" vindo do Sofascore por causa disto -- não
# era falta de correspondência real, era o nome a ficar corrompido
# antes sequer de comparar.
TRANSLITERACOES_ESCANDINAVAS = str.maketrans({
    "æ": "ae", "Æ": "AE",
    "ø": "o", "Ø": "O",
    "å": "a", "Å": "A",
})


def normalizar(texto):
    if not texto:
        return ""
    texto = texto.translate(TRANSLITERACOES_ESCANDINAVAS)
    sem_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acentos.lower().strip()


CONECTORES = {"de", "do", "da", "del", "dos", "das", "el", "la", "los", "las"}


def _sem_conectores(texto):
    """
    Remove só ligações gramaticais (de/do/da/...), nunca as palavras
    que identificam quem/o quê é (ex: nomes próprios ou "real",
    "atletico"). Sem isto, "Atletico Madrid" (Sofascore) nunca batia
    certo com "Atlético de Madrid" (nome oficial na nossa BD) por
    causa do "de" a meio.
    """
    return " ".join(t for t in texto.split() if t not in CONECTORES)


def nomes_correspondem_forte(nome_a, nome_b):
    """
    Critérios de nome fortes o suficiente para aceitar um candidato
    ÚNICO sem precisar de confirmar o clube: nome igual, ou um nome
    contido no outro (com ou sem conectores gramaticais).
    """
    a, b = normalizar(nome_a), normalizar(nome_b)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    a_limpo, b_limpo = _sem_conectores(a), _sem_conectores(b)
    if a_limpo and b_limpo and (a_limpo in b_limpo or b_limpo in a_limpo):
        return True
    return False


def nomes_correspondem_fraco(nome_a, nome_b):
    """
    Critério de nome FRACO (mesmo apelido + mesma inicial do primeiro
    nome) -- útil para nomes abreviados, mas perigoso sozinho: apelidos
    comuns (ex: "García", "Silva", "Santos") combinados com a mesma
    inicial fazem colidir jogadores diferentes com facilidade,
    sobretudo agora que entrou a La Liga. Por isso nunca deve bastar
    por si só -- quem chama isto tem de exigir confirmação por clube
    a seguir.
    """
    a, b = normalizar(nome_a), normalizar(nome_b)
    if not a or not b:
        return False
    partes_a, partes_b = a.split(), b.split()
    if partes_a and partes_b and partes_a[-1] == partes_b[-1]:
        if partes_a[0][0] == partes_b[0][0]:
            return True
    return False


def nomes_correspondem(nome_a, nome_b):
    """Mantido para uso em nomes de CLUBES, onde não há o mesmo risco de colisão."""
    return nomes_correspondem_forte(nome_a, nome_b) or nomes_correspondem_fraco(nome_a, nome_b)


def encontrar_jogador_local(nome_sofascore, clube_sofascore, jogadores_locais):
    """
    Faz o matching LOCALMENTE (sem nenhum pedido de rede) entre um
    jogador vindo da listagem em massa da Sofascore e os jogadores já
    guardados na nossa BD. Substitui a antiga pesquisa via
    `/search/all`, que deixou de ser fiável.

    Correção (2026-09-27): um candidato ÚNICO encontrado só pelo
    critério fraco (apelido + inicial) deixou de ser aceite sem mais
    -- tem de bater também o clube, tal como acontece quando há vários
    candidatos. Sem isto, dois jogadores diferentes com o mesmo apelido
    e a mesma inicial (comum em espanhol/português) podiam ser trocados
    silenciosamente logo na primeira associação de sofascore_id, sem
    que o UniqueViolation em gravar_sofascore_id alguma vez apanhasse o
    erro (é a primeira vez a usar aquele sofascore_id, não há conflito
    a detetar).
    """
    fortes = [j for j in jogadores_locais if nomes_correspondem_forte(nome_sofascore, j["nome"])]
    fracos = [j for j in jogadores_locais if j not in fortes and nomes_correspondem_fraco(nome_sofascore, j["nome"])]

    if len(fortes) == 1:
        return fortes[0]

    candidatos = fortes if fortes else fracos
    exige_clube = not fortes  # só candidatos fracos exigem sempre confirmação por clube

    if not candidatos:
        return None

    if clube_sofascore:
        for c in candidatos:
            if c["clube"] and nomes_correspondem(clube_sofascore, c["clube"]):
                return c

    if not exige_clube and len(candidatos) == 1:
        return candidatos[0]

    # múltiplos candidatos fortes sem desempate por clube, ou um único
    # candidato fraco sem confirmação de clube -- mais vale não
    # arriscar (evita o erro antigo dos dois "João Mário")
    print(
        f"  [ambíguo] \"{nome_sofascore}\" ({clube_sofascore}) corresponde a "
        f"{len(candidatos)} jogadores locais, a ignorar por segurança."
    )
    return None


# ==========================================
# pedidos HTTP via wreq (fingerprint TLS/JA3/JA4 de Chrome real) -- a API
# da Sofascore devolve 403 a pedidos feitos com `requests`/`httpx` normais
# ==========================================

def _pedir(client, url):
    """
    Faz um único GET com o client wreq (async) a partir de código
    síncrono. Cria e fecha um event loop por pedido -- simples e
    suficiente dado que já há pausas de vários segundos entre pedidos.
    """
    async def _fazer():
        resp = await client.get(url)
        texto = await resp.text()
        return resp.status, texto

    return asyncio.run(_fazer())


def wreq_fetch(client, url, tentativas_403=0):
    status, texto = _pedir(client, url)

    if status == 200:
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            return None

    if status == 404:
        return None

    if status == 429:
        print("  [429] limite atingido, à espera 8s...")
        time.sleep(8)
        return wreq_fetch(client, url, tentativas_403)

    if status == 403:
        # IMPORTANTE: um 403 aqui pode ser a Cloudflare a bloquear-nos
        # temporariamente, não "isto não existe". Se devolvêssemos
        # None como se fosse um 404, o chamador via isto como "sem
        # correspondência" e marcava o item como concluído no
        # checkpoint -- para sempre, mesmo sem nunca lhe termos
        # conseguido perguntar nada. Por isso: tentativas de
        # recuperação com pausas longas antes de desistir, mas nunca
        # disfarçar de "sem correspondência".
        if tentativas_403 >= RETRIES_403:
            raise RuntimeError(
                f"Bloqueado pela Cloudflare (403) em {url} -- "
                f"mesmo depois de {RETRIES_403} tentativas de recuperação, "
                f"a sessão continua bloqueada. A parar tudo."
            )
        espera = ESPERA_BASE_403 * (tentativas_403 + 1)
        print(
            f"  [403] bloqueado pela Cloudflare, à espera {espera}s antes "
            f"de retry ({tentativas_403 + 1}/{RETRIES_403})..."
        )
        time.sleep(espera)
        return wreq_fetch(client, url, tentativas_403 + 1)

    print(f"  [{status}] erro inesperado em {url}")
    return None


def criar_client():
    return Client(emulation=EMULATION)


# ==========================================
# chamadas à API da Sofascore
# ==========================================

def obter_temporada_id(client, liga_id, epoca_nome):
    url = f"{BASE_URL}/unique-tournament/{liga_id}/seasons"
    dados = wreq_fetch(client, url)
    sleep_pedido()

    if not dados or "seasons" not in dados:
        raise RuntimeError(f"Não consegui obter épocas para liga_id={liga_id}")

    for s in dados["seasons"]:
        if s.get("year") == epoca_nome:
            return s["id"], s.get("name", epoca_nome)

    raise RuntimeError(f"Época '{epoca_nome}' não encontrada para liga_id={liga_id}")


def listar_todos_os_jogadores(client, liga_id, epoca_id):
    """
    Lista TODOS os jogadores da liga/época em blocos de 100, via
    paginação -- sem usar `/search/all`. Devolve uma lista de dicts
    com pelo menos {"id", "nome", "clube"} por jogador.
    """
    jogadores = []
    offset = 0

    while True:
        url = (
            f"{BASE_URL}/unique-tournament/{liga_id}/season/{epoca_id}/statistics"
            f"?limit=100&offset={offset}"
        )
        dados = wreq_fetch(client, url)
        sleep_pedido()

        if not dados:
            break

        resultados = dados.get("results", [])
        if not resultados:
            break

        for item in resultados:
            jogador = item.get("player", item)
            if not isinstance(jogador, dict) or not jogador.get("id"):
                continue
            equipa = (jogador.get("team") or item.get("team") or {}).get("name")
            jogadores.append({
                "id": jogador["id"],
                "nome": jogador.get("name", "Desconhecido"),
                "clube": equipa,
            })

        total_paginas = dados.get("pages", 0)
        pagina_atual = (offset // 100) + 1
        print(f"  [listagem] página {pagina_atual}/{total_paginas} -- {len(resultados)} jogadores")

        offset += 100
        if pagina_atual >= total_paginas:
            break

    return jogadores


def obter_estatisticas_epoca(client, sofascore_id, liga_id, epoca_id):
    url = f"{BASE_URL}/player/{sofascore_id}/unique-tournament/{liga_id}/season/{epoca_id}/statistics/overall"
    dados = wreq_fetch(client, url)
    sleep_pedido()

    if not dados:
        return None

    stats = dados.get("statistics", dados)
    equipa = (dados.get("team") or {}).get("name")

    def g(chave):
        return stats.get(chave)

    return {
        "equipa": equipa,
        "jogos": g("appearances"),
        "titularidades": g("matchesStarted"),
        "minutos": g("minutesPlayed"),
        "rating": g("rating"),
        "golos": g("goals"),
        "assistencias": g("assists"),
        "remates_totais": g("totalShots"),
        "remates_a_baliza": g("shotsOnTarget"),
        "grandes_ocasioes_criadas": g("bigChancesCreated"),
        "grandes_ocasioes_falhadas": g("bigChancesMissed"),
        "golos_esperados": g("expectedGoals"),
        "assistencias_esperadas": g("expectedAssists"),
        "golos_cabeca": g("headedGoals"),
        "golos_pe_direito": g("rightFootGoals"),
        "golos_pe_esquerdo": g("leftFootGoals"),
        "golos_penalti": g("penaltyGoals"),
        "conversao_remates_pct": g("goalConversionPercentage"),
        "passes_totais": g("totalPasses"),
        "passes_certos": g("accuratePasses"),
        "passes_certos_pct": g("accuratePassesPercentage"),
        "passes_chave": g("keyPasses"),
        "dribles_conseguidos": g("successfulDribbles"),
        "toques": g("touches"),
        "duelos_terrestres_ganhos": g("groundDuelsWon"),
        "duelos_aereos_ganhos": g("aerialDuelsWon"),
        "duelos_totais_ganhos": g("totalDuelsWon"),
        "desarmes": g("tackles"),
        "intercecoes": g("interceptions"),
        "recuperacoes_bola": g("ballRecovery"),
        "cartoes_amarelos": g("yellowCards"),
        "cartoes_vermelhos": g("redCards"),
        "faltas": g("fouls"),
        "foras_de_jogo": g("offsides"),
        "defesas": g("saves"),
        "golos_sofridos": g("goalsConceded"),
        "golos_evitados": g("goalsPrevented"),
        "balizas_a_zero": g("cleanSheet"),
        "penaltis_defendidos": g("penaltySave"),
        "penaltis_sofridos": g("penaltyFaced"),
        "remates_defendidos_dentro_area": g("savedShotsFromInsideTheBox"),
        "remates_defendidos_fora_area": g("savedShotsFromOutsideTheBox"),
        "socos": g("punches"),
        "saidas_bem_sucedidas": g("successfulRunsOut"),
        "saidas_aereas": g("highClaims"),
        "pontapes_de_baliza": g("goalKicks"),
        "passes_picados_certos": g("accurateChippedPasses"),
        "passes_picados_totais": g("totalChippedPasses"),
        "cruzamentos_certos": g("accurateCrosses"),
        "cruzamentos_totais": g("totalCross"),
        "cruzamentos_certos_pct": g("accurateCrossesPercentage"),
        "passes_terco_final_certos": g("accurateFinalThirdPasses"),
        "bolas_longas_certas": g("accurateLongBalls"),
        "bolas_longas_totais": g("totalLongBalls"),
        "bolas_longas_certas_pct": g("accurateLongBallsPercentage"),
        "passes_campo_contrario_certos": g("accurateOppositionHalfPasses"),
        "passes_campo_contrario_totais": g("totalOppositionHalfPasses"),
        "passes_proprio_campo_certos": g("accurateOwnHalfPasses"),
        "passes_proprio_campo_totais": g("totalOwnHalfPasses"),
        "duelos_aereos_ganhos_pct": g("aerialDuelsWonPercentage"),
        "duelos_aereos_perdidos": g("aerialLost"),
        "penaltis_batidos_fora": g("attemptPenaltyMiss"),
        "penaltis_batidos_poste": g("attemptPenaltyPost"),
        "penaltis_batidos_a_baliza": g("attemptPenaltyTarget"),
        "remates_bloqueados": g("blockedShots"),
        "alivios": g("clearances"),
        "jogos_com_rating": g("countRating"),
        "cruzamentos_nao_agarrados": g("crossesNotClaimed"),
        "cartoes_vermelhos_diretos": g("directRedCards"),
        "perdas_por_desarme": g("dispossessed"),
        "driblado_por_adversario": g("dribbledPast"),
        "duelos_perdidos": g("duelLost"),
        "erros_geraram_golo": g("errorLeadToGoal"),
        "erros_geraram_remate": g("errorLeadToShot"),
        "xg_envolvimento": g("expectedGoalsInvolvement"),
        "golos_livre_direto": g("freeKickGoal"),
        "golos_mais_assistencias": g("goalsAssistsSum"),
        "golos_sofridos_dentro_area": g("goalsConcededInsideTheBox"),
        "golos_sofridos_fora_area": g("goalsConcededOutsideTheBox"),
        "golos_dentro_area": g("goalsFromInsideTheBox"),
        "golos_fora_area": g("goalsFromOutsideTheBox"),
        "duelos_terrestres_ganhos_pct": g("groundDuelsWonPercentage"),
        "remates_ao_poste": g("hitWoodwork"),
        "passes_errados": g("inaccuratePasses"),
        "autogolos": g("ownGoals"),
        "passes_pre_assistencia": g("passToAssist"),
        "penaltis_batidos": g("penaltiesTaken"),
        "penaltis_cometidos": g("penaltyConceded"),
        "penaltis_conversao_pct": g("penaltyConversion"),
        "penaltis_conquistados": g("penaltyWon"),
        "posse_perdida": g("possessionLost"),
        "posse_ganha_terco_final": g("possessionWonAttThird"),
        "saidas_totais": g("runsOut"),
        "defesas_agarradas": g("savesCaught"),
        "defesas_desviadas": g("savesParried"),
        "frequencia_finalizacao": g("scoringFrequency"),
        "conversao_bola_parada_pct": g("setPieceConversion"),
        "remates_bola_parada": g("shotFromSetPiece"),
        "remates_dentro_area": g("shotsFromInsideTheBox"),
        "remates_fora_area": g("shotsFromOutsideTheBox"),
        "remates_fora": g("shotsOffTarget"),
        "dribles_conseguidos_pct": g("successfulDribblesPercentage"),
        "desarmes_ganhos": g("tacklesWon"),
        "desarmes_ganhos_pct": g("tacklesWonPercentage"),
        "assistencias_tentadas": g("totalAttemptAssist"),
        "dribles_tentados": g("totalContest"),
        "duelos_totais_ganhos_pct": g("totalDuelsWonPercentage"),
        "rating_total": g("totalRating"),
        "presencas_equipa_semana": g("totwAppearances"),
        "sofreu_faltas": g("wasFouled"),
        "segundo_amarelo": g("yellowRedCards"),
    }


# ==========================================
# gravar na base de dados
# ==========================================

def guardar_estatisticas(conn, jogador_id, sofascore_id, liga_id, epoca_id, epoca_nome, posicao, stats):
    colunas_stats = list(stats.keys())
    colunas_fixas = ["jogador_id", "sofascore_id", "liga_id", "epoca_id", "epoca_nome", "posicao"]
    valores_fixos = [jogador_id, sofascore_id, liga_id, epoca_id, epoca_nome, posicao]

    todas_colunas = colunas_fixas + colunas_stats
    todos_valores = valores_fixos + [stats[c] for c in colunas_stats]

    placeholders = ", ".join(["%s"] * len(todas_colunas))
    colunas_sql = ", ".join(todas_colunas)
    update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in colunas_stats)

    cursor = conn.cursor()
    cursor.execute(f"""
        INSERT INTO estatisticas_sofascore ({colunas_sql})
        VALUES ({placeholders})
        ON CONFLICT (jogador_id, liga_id, epoca_id) DO UPDATE SET
            sofascore_id = EXCLUDED.sofascore_id,
            posicao = EXCLUDED.posicao,
            {update_sql},
            atualizado_em = CURRENT_TIMESTAMP
    """, todos_valores)
    conn.commit()
    cursor.close()


# ==========================================
# progresso (para poderes parar e continuar depois)
# ==========================================

def carregar_progresso():
    try:
        with open(PROGRESSO_FICHEIRO) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def guardar_progresso(feitos):
    with open(PROGRESSO_FICHEIRO, "w") as f:
        json.dump(sorted(feitos), f)


# ==========================================
# main
# ==========================================

def correr_uma_vez():
    """
    Uma corrida: no máximo LIMITE_JOGADORES_POR_CORRIDA jogadores, um
    único client wreq (com o fingerprint Chrome149) para toda a corrida.
    Devolve (processados, limite_atingido) -- limite_atingido=False quer
    dizer que chegou ao fim de todas as ligas configuradas sem precisar
    de parar, ou seja, não ficou trabalho por fazer.
    """
    conn = ligar_bd()
    try:
        feitos = carregar_progresso()
        todos_jogadores_locais = obter_jogadores_bd(conn)

        ligas_a_processar = [l for l in LIGAS if LIGA_A_CORRER is None or l["nome"] == LIGA_A_CORRER]

        jogadores_processados_nesta_corrida = 0
        limite_atingido = False

        client = criar_client()

        for indice_liga, liga in enumerate(ligas_a_processar):
            if limite_atingido:
                break

            print(f"\n=== {liga['nome']} ({liga['epoca_nome']}) ===")

            ids_relevantes = obter_ids_da_competicao(conn, liga["competicao"])
            if ids_relevantes:
                jogadores_locais = [j for j in todos_jogadores_locais if j["id"] in ids_relevantes]
            else:
                # ainda sem estatisticas_jogador para esta competição
                # (ex: primeira vez a correr) -- não filtra, para não
                # ficar sem nenhum candidato local possível.
                jogadores_locais = todos_jogadores_locais
            print(f"  {len(jogadores_locais)} jogadores locais candidatos para '{liga['competicao']}'.")

            if liga["epoca_id"] is None:
                liga["epoca_id"], nome_epoca_real = obter_temporada_id(client, liga["liga_id"], liga["epoca_nome"])
                print(f"  epoca_id resolvido: {liga['epoca_id']} ({nome_epoca_real})")

            print("  a listar todos os jogadores da liga/época...")
            jogadores_sofascore = listar_todos_os_jogadores(client, liga["liga_id"], liga["epoca_id"])
            print(f"  {len(jogadores_sofascore)} jogadores encontrados na Sofascore para esta liga/época.")

            for indice, jog_sofa in enumerate(jogadores_sofascore):
                chave_progresso = f"{jog_sofa['id']}:{liga['liga_id']}:{liga['epoca_id']}"
                if chave_progresso in feitos:
                    continue

                nome = jog_sofa["nome"]
                sofascore_id = jog_sofa["id"]

                local = encontrar_jogador_local(nome, jog_sofa["clube"], jogadores_locais)
                if not local:
                    print(f"  [sem correspondência local] {nome} ({jog_sofa['clube']})")
                    feitos.add(chave_progresso)
                    guardar_progresso(feitos)
                    continue

                if not local["sofascore_id"]:
                    associacao_valida = gravar_sofascore_id(conn, local["id"], sofascore_id)
                    if not associacao_valida:
                        feitos.add(chave_progresso)
                        guardar_progresso(feitos)
                        continue
                    local["sofascore_id"] = sofascore_id

                stats = obter_estatisticas_epoca(client, sofascore_id, liga["liga_id"], liga["epoca_id"])
                jogadores_processados_nesta_corrida += 1

                if not stats:
                    print(f"  [sem stats nesta competição/época] {nome}")
                    feitos.add(chave_progresso)
                    guardar_progresso(feitos)
                else:
                    guardar_estatisticas(
                        conn, local["id"], sofascore_id,
                        liga["liga_id"], liga["epoca_id"], liga["epoca_nome"],
                        None, stats
                    )
                    print(f"  [{indice + 1}/{len(jogadores_sofascore)}] [guardado] {nome} — "
                          f"{stats['jogos']} jogos, xG={stats.get('golos_esperados')}")

                    feitos.add(chave_progresso)
                    guardar_progresso(feitos)

                # Limite de jogadores por corrida: para de forma
                # limpa em vez de continuar até apanhar 403. O
                # checkpoint já garante que a próxima corrida
                # continua exatamente daqui.
                if LIMITE_JOGADORES_POR_CORRIDA is not None and jogadores_processados_nesta_corrida >= LIMITE_JOGADORES_POR_CORRIDA:
                    print(
                        f"\n[limite atingido] {jogadores_processados_nesta_corrida} jogadores processados "
                        f"nesta corrida -- a parar de forma limpa."
                    )
                    limite_atingido = True
                    break

            if not limite_atingido and indice_liga < len(ligas_a_processar) - 1:
                pausa = random.uniform(PAUSA_ENTRE_LIGAS_MIN, PAUSA_ENTRE_LIGAS_MAX)
                print(f"\n[pausa entre ligas] {pausa:.1f}s...")
                time.sleep(pausa)

        return jogadores_processados_nesta_corrida, limite_atingido
    finally:
        conn.close()


# ==========================================
# corre várias vezes, com espera entre tentativas, até acabar
# ==========================================

ESPERA_ENTRE_TENTATIVAS = 10 * 60  # 10 minutos, a pedido
MAX_TENTATIVAS = 20
MAX_TENTATIVAS_SEM_PROGRESSO_SEGUIDAS = 3


def main():
    tentativa = 0
    tentativas_sem_progresso_seguidas = 0

    while True:
        tentativa += 1
        print(f"\n########## TENTATIVA {tentativa} ##########")

        try:
            processados, limite_atingido = correr_uma_vez()
        except RuntimeError as erro:
            print(f"[bloqueado] {erro}")
            processados, limite_atingido = 0, True

        tentativas_sem_progresso_seguidas = 0 if processados > 0 else tentativas_sem_progresso_seguidas + 1

        if not limite_atingido:
            print("\nImportação Sofascore concluída -- não ficou trabalho por fazer.")
            break

        if tentativas_sem_progresso_seguidas >= MAX_TENTATIVAS_SEM_PROGRESSO_SEGUIDAS:
            print(
                f"\n[parar] {tentativas_sem_progresso_seguidas} tentativas seguidas sem processar "
                f"ninguém -- o bloqueio parece mais persistente do que uns minutos. Corre outra vez "
                f"tu próprio mais tarde (horas, não minutos)."
            )
            break

        if tentativa >= MAX_TENTATIVAS:
            print(f"\n[parar] limite de {MAX_TENTATIVAS} tentativas atingido nesta corrida.")
            break

        print(f"\n[espera] {ESPERA_ENTRE_TENTATIVAS / 60:.0f} min antes da próxima tentativa...")
        time.sleep(ESPERA_ENTRE_TENTATIVAS)


if __name__ == "__main__":
    main()