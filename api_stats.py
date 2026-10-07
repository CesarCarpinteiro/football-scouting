import os
import sys
import json
import time
import unicodedata
import requests
import psycopg
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURAÇÕES
# ============================================================

BASE_URL = "https://v3.football.api-sports.io"

API_KEY = os.environ.get("API_FOOTBALL_KEY")

if not API_KEY:
    sys.exit(
        "Falta a variável de ambiente API_FOOTBALL_KEY.\n"
        "Executa:\n"
        "export API_FOOTBALL_KEY='a-tua-chave-aqui'"
    )

HEADERS = {
    "x-apisports-key": API_KEY
}

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "football",
    "user": "scouting",
    "password": "scouting"
}

EPOCA = 2024

TEMPORADA = f"{EPOCA}/{str(EPOCA + 1)[-2:]}"

# (país, nome da liga tal como aparece em /leagues?country={país})
# -- os IDs são resolvidos dinamicamente, para não dependermos
#    de números "adivinhados".
LIGAS_ALVO = [
    # Bélgica primeiro -- é a que está completamente por fazer
    # (0 equipas). As outras já estão 100% feitas no checkpoint,
    # ficam só skips rápidos.
    ("Belgium", "Jupiler Pro League"),
    ("Norway", "Eliteserien"),
    ("Spain", "La Liga"),
    ("France", "Ligue 1"),
    ("Portugal", "Primeira Liga"),
    ("Portugal", "Segunda Liga"),
]

PROGRESSO_FICHEIRO = "progresso_estatisticas.json"


def agora():
    return datetime.now().strftime("%H:%M:%S")


def log(mensagem):
    print(f"[{agora()}] {mensagem}")


# ============================================================
# PROGRESSO (para poder parar/retomar entre dias, por causa
# do limite de 100 pedidos/dia do plano free)
# ============================================================

def carregar_progresso():
    if not os.path.exists(PROGRESSO_FICHEIRO):
        return {"equipas_concluidas": []}

    with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_progresso(progresso):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, indent=2, ensure_ascii=False)


def marcar_equipa_concluida(progresso, liga_id, team_id):
    chave = f"{liga_id}:{team_id}:{EPOCA}"

    if chave not in progresso["equipas_concluidas"]:
        progresso["equipas_concluidas"].append(chave)

    guardar_progresso(progresso)


def equipa_ja_concluida(progresso, liga_id, team_id):
    chave = f"{liga_id}:{team_id}:{EPOCA}"
    return chave in progresso["equipas_concluidas"]


# ============================================================
# PEDIDOS À API-FOOTBALL
# ============================================================

MAX_TENTATIVAS_429 = 6
ESPERA_BASE_429 = 15  # segundos


def chamar(endpoint, params):
    """
    Faz um pedido à API-Football e mostra quantos pedidos
    ainda restam hoje (vem nos headers da resposta).

    O plano free da API-Football limita também os pedidos POR
    MINUTO (tipicamente ~10/min), separado do limite diário.
    Se vier um 429, esperamos e tentamos outra vez em vez de
    abortar logo -- normalmente resolve-se em poucos segundos.
    """

    url = f"{BASE_URL}/{endpoint}"

    for tentativa in range(1, MAX_TENTATIVAS_429 + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=30
            )

            if response.status_code == 429:
                espera = ESPERA_BASE_429 * tentativa

                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    espera = max(espera, int(retry_after))

                log(
                    f"  [429] Limite por minuto atingido -- "
                    f"a esperar {espera}s antes de repetir "
                    f"(tentativa {tentativa}/{MAX_TENTATIVAS_429})..."
                )

                time.sleep(espera)
                continue

            response.raise_for_status()

            # A API-Football às vezes devolve HTTP 200 (não 429) com o
            # limite por minuto excedido dentro do próprio corpo JSON
            # (data["errors"]["rateLimit"]). Se não apanharmos este caso
            # aqui, o pedido "sucede" com response=[] e a equipa fica
            # marcada como concluída com 0 jogadores guardados.
            data_tentativa = response.json()

            if isinstance(data_tentativa.get("errors"), dict) and data_tentativa["errors"].get("rateLimit"):
                espera = ESPERA_BASE_429 * tentativa

                log(
                    f"  [rateLimit] Limite por minuto atingido (corpo da resposta) -- "
                    f"a esperar {espera}s antes de repetir "
                    f"(tentativa {tentativa}/{MAX_TENTATIVAS_429})..."
                )

                time.sleep(espera)
                continue

            break

        except requests.RequestException as erro:
            if tentativa == MAX_TENTATIVAS_429:
                raise RuntimeError(
                    f"Erro ao chamar o endpoint '{endpoint}': {erro}"
                ) from erro

            log(f"  [aviso] Falha de rede, a repetir: {erro}")
            time.sleep(5)

    else:
        raise RuntimeError(
            f"Endpoint '{endpoint}' continuou a dar 429 após "
            f"{MAX_TENTATIVAS_429} tentativas."
        )

    restantes = response.headers.get("x-ratelimit-requests-remaining")

    if restantes is not None:
        log(f"  (pedidos restantes hoje: {restantes})")

        if restantes.isdigit() and int(restantes) <= 3:
            log(
                "  [AVISO] Quase sem pedidos para hoje. "
                "O progresso já está guardado -- podes parar e "
                "continuar amanhã."
            )

    data = data_tentativa

    if data.get("errors"):
        # IMPORTANTE: quando a quota diária esgota, a API-Football
        # devolve HTTP 200 com "response": [] e o erro dentro de
        # "errors" -- se só avisássemos e continuássemos, o
        # chamador via isto como "0 resultados" (equipa/jogador
        # genuinamente vazio) em vez de "pedido falhou", e a equipa
        # ficava marcada como concluída sem dados nenhuns. Isto já
        # aconteceu com as 20 equipas da La Liga de uma vez. Por
        # isso, se o erro for de limite de pedidos, tem de
        # interromper o processamento (propagar), nunca disfarçar
        # como resultado vazio válido.
        erros = data["errors"]
        if isinstance(erros, dict) and "requests" in erros:
            raise RuntimeError(
                f"Quota diária da API-Football esgotada: {erros['requests']}"
            )

        print(
            f"[aviso] Erros da API no endpoint "
            f"{endpoint}: {data['errors']}"
        )

    return data


# ============================================================
# LIGAS E EQUIPAS
# ============================================================

def obter_id_liga(pais, nome_liga):
    """
    Resolve o ID da liga na API-Football a partir do nome,
    consultando /leagues?country={pais}.
    """

    data = chamar("leagues", {"country": pais})

    for item in data.get("response", []):
        liga = item["league"]

        if liga["name"].strip().lower() == nome_liga.strip().lower():
            return liga["id"], liga["name"]

    raise ValueError(
        f"Liga '{nome_liga}' não encontrada em /leagues?country={pais}."
    )


def obter_equipas_liga(liga_id, epoca):
    """
    Obtém todas as equipas de uma liga, numa época.
    """

    data = chamar(
        "teams",
        {
            "league": liga_id,
            "season": epoca
        }
    )

    equipas = []

    for item in data.get("response", []):
        equipas.append({
            "id": item["team"]["id"],
            "nome": item["team"]["name"],
        })

    return equipas


# ============================================================
# OBTER JOGADORES DE UMA EQUIPA
# ============================================================

def obter_jogadores_do_clube(team_id, epoca):
    """
    Obtém todos os jogadores do clube para a época indicada.
    """

    jogadores = []

    pagina = 1

    while True:
        log(f"    A obter jogadores - página {pagina}")

        data = chamar(
            "players",
            {
                "team": team_id,
                "season": epoca,
                "page": pagina
            }
        )

        resultados = data.get("response", [])

        if not resultados:
            break

        jogadores.extend(resultados)

        paging = data.get("paging", {})

        pagina_atual = paging.get("current", pagina)

        total_paginas = paging.get("total", pagina)

        if pagina_atual >= total_paginas:
            break

        pagina += 1

        time.sleep(3)

    return jogadores


# ============================================================
# EXTRAIR ESTATÍSTICAS
# ============================================================

def extrair_estatisticas_completas(jogador_raw, liga_id):
    """
    Extrai os dados estatísticos da competição indicada.
    """

    info = jogador_raw.get("player", {})

    stats_lista = jogador_raw.get("statistics", [])

    stats_liga = None

    for bloco in stats_lista:
        liga = bloco.get("league", {})

        if liga.get("id") == liga_id:
            stats_liga = bloco
            break

    if stats_liga is None:
        return None

    jogos_info = stats_liga.get("games", {}) or {}
    substitutos_info = stats_liga.get("substitutes", {}) or {}
    remates_info = stats_liga.get("shots", {}) or {}
    golos_info = stats_liga.get("goals", {}) or {}
    passes_info = stats_liga.get("passes", {}) or {}
    duelos_info = stats_liga.get("duels", {}) or {}
    dribles_info = stats_liga.get("dribbles", {}) or {}
    faltas_info = stats_liga.get("fouls", {}) or {}
    cartoes_info = stats_liga.get("cards", {}) or {}
    penaltis_info = stats_liga.get("penalty", {}) or {}
    tackles_info = stats_liga.get("tackles", {}) or {}

    return {
        "api_football_id": info.get("id"),
        "nome": info.get("name"),
        "nome_completo": (
            f"{info.get('firstname', '') or ''} "
            f"{info.get('lastname', '') or ''}"
        ).strip() or None,
        "nacionalidade": info.get("nationality"),
        "idade": info.get("age"),
        "altura": info.get("height"),
        "peso": info.get("weight"),
        "lesionado": info.get("injured"),
        "foto": info.get("photo"),

        "liga": stats_liga.get("league", {}).get("name"),
        "liga_id": stats_liga.get("league", {}).get("id"),
        "epoca_liga": stats_liga.get("league", {}).get("season"),
        "equipa": stats_liga.get("team", {}).get("name"),

        "posicao": jogos_info.get("position"),
        "jogos": jogos_info.get("appearences"),
        "titular": jogos_info.get("lineups"),
        "minutos": jogos_info.get("minutes"),
        "numero_camisola": jogos_info.get("number"),
        "capitao": jogos_info.get("captain"),
        "rating": jogos_info.get("rating"),

        "suplente_entrou": substitutos_info.get("in"),
        "suplente_saiu": substitutos_info.get("out"),
        "suplente_banco": substitutos_info.get("bench"),

        "remates_totais": remates_info.get("total"),
        "remates_a_baliza": remates_info.get("on"),

        "golos": golos_info.get("total"),
        "assistencias": golos_info.get("assists"),
        "golos_sofridos": golos_info.get("conceded"),
        "defesas": golos_info.get("saves"),

        "passes_totais": passes_info.get("total"),
        "passes_chave": passes_info.get("key"),
        "passes_certos_pct": passes_info.get("accuracy"),

        "duelos_totais": duelos_info.get("total"),
        "duelos_ganhos": duelos_info.get("won"),

        "desarmes": tackles_info.get("total"),
        "bloqueios": tackles_info.get("blocks"),
        "intercecoes": tackles_info.get("interceptions"),

        "dribles_tentados": dribles_info.get("attempts"),
        "dribles_conseguidos": dribles_info.get("success"),
        "dribles_sofridos": dribles_info.get("past"),

        "faltas_sofridas": faltas_info.get("drawn"),
        "faltas_cometidas": faltas_info.get("committed"),

        "cartoes_amarelos": cartoes_info.get("yellow"),
        "cartoes_amarelo_vermelho": cartoes_info.get("yellowred"),
        "cartoes_vermelhos": cartoes_info.get("red"),

        "penaltis_ganhos": penaltis_info.get("won"),
        "penaltis_cometidos": penaltis_info.get("commited"),
        "penaltis_marcados": penaltis_info.get("scored"),
        "penaltis_falhados": penaltis_info.get("missed"),
        "penaltis_defendidos": penaltis_info.get("saved"),

        "foras_de_jogo": stats_liga.get("offsides"),
    }


# ============================================================
# NORMALIZAÇÃO DE NOMES
# ============================================================

#  As letras escandinavas (æ, ø, å) não são "acentos" no sentido NFKD
# -- não se decompõem em letra base + marca combinável, por isso o
# unicodedata.normalize("NFKD", ...) abaixo não as trata, e
# simplesmente as apaga em vez de as transliterar (ex: "Bodø" ->
# "Bod", não "Bodo"). Já descoberto e corrigido uma vez no
# importar_sofascore.py; nunca tinha chegado aqui, e por isso 8
# clubes inteiros da Eliteserien (Lillestrøm, Strømsgodset, Tromsø,
# Bodø/Glimt, etc.) nunca batiam certo com a API-Football.
TRANSLITERACOES_ESCANDINAVAS = str.maketrans({
    "æ": "ae", "Æ": "AE",
    "ø": "o", "Ø": "O",
    "å": "a", "Å": "A",
})


def normalizar_nome(nome):
    if not nome:
        return ""

    nome = str(nome).strip().lower()
    nome = nome.translate(TRANSLITERACOES_ESCANDINAVAS)
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))

    return " ".join(nome.split())


# ============================================================
# BASE DE DADOS: JOGADORES
# ============================================================

def obter_jogadores_bd(conn):
    """
    Devolve {nome_normalizado: [(jogador_id, {nomes de clubes na
    temporada}), ...]}.

    IMPORTANTE: nomes comuns em português (ex.: "Jota", "Paulinho",
    "Diogo Costa") pertencem a vários jogadores reais e distintos em
    clubes diferentes -- por isso guardamos SEMPRE uma lista de
    candidatos por nome, nunca um único id, para depois desempatar
    pelo clube que está a ser processado.
    """

    with conn.cursor() as cursor:
        cursor.execute("SELECT id, nome FROM jogadores")
        jogadores = cursor.fetchall()

        cursor.execute(
            """
            SELECT jc.jogador_id, c.nome
            FROM jogador_clube jc
            JOIN clubes c ON c.id = jc.clube_id
            WHERE jc.temporada = %s
            """,
            (TEMPORADA,)
        )
        clubes_por_jogador = {}
        for jogador_id, nome_clube in cursor.fetchall():
            clubes_por_jogador.setdefault(jogador_id, set()).add(nome_clube)

    resultado = {}
    for jogador_id, nome in jogadores:
        chave = normalizar_nome(nome)
        resultado.setdefault(chave, []).append(
            (jogador_id, clubes_por_jogador.get(jogador_id, set()))
        )

    return resultado


def nomes_correspondem(nome_api, nome_bd):
    """
    Compara dois nomes já normalizados (sem acentos, minúsculas).

    Cobre três casos:
    1. Um é substring do outro (ex: "evanilson" em "francisco evanilson").
    2. Nome abreviado tipo "m. grujic" -- compara o apelido (última
       palavra) e a inicial do primeiro nome, em vez do texto todo.
       Isto é comum na API-Football quando o jogador tem um nome de
       exibição curto (acontece com jogadores de vários clubes, não
       só o FC Porto).
    3. Apelido igual e um dos primeiros nomes contém o outro.
    """

    if nome_api in nome_bd or nome_bd in nome_api:
        return True

    tokens_api = nome_api.replace(".", " ").split()
    tokens_bd = nome_bd.replace(".", " ").split()

    if not tokens_api or not tokens_bd:
        return False

    if tokens_api[-1] != tokens_bd[-1]:
        return False

    primeiro_api = tokens_api[0]
    primeiro_bd = tokens_bd[0]

    if len(primeiro_api) == 1 or len(primeiro_bd) == 1:
        return primeiro_api[0] == primeiro_bd[0]

    return primeiro_api in primeiro_bd or primeiro_bd in primeiro_api


CONECTORES_CLUBE = {"de", "do", "da", "del", "dos", "das", "el", "la", "los", "las"}

# O Transfermarkt.pt traduz alguns nomes de cidades estrangeiras para
# português (ex: "Estrasburgo", "Marselha", "Maiorca", "Sevilha"), ou
# usa o gentílico em vez do nome da cidade (ex: "Rennais" em "Stade
# Rennais"), enquanto a API-Football usa sempre o nome original
# (francês/espanhol/inglês). Sem isto, TODOS os jogadores dessas
# equipas falhavam a verificação de clube -- não por serem candidatos
# errados, mas porque as duas fontes nunca escreviam o nome da cidade
# da mesma forma (ex: "rennes" nunca é substring de "rennais").
ALIASES_CLUBE = {
    "estrasburgo": "strasbourg",
    "marselha": "marseille",
    "rennais": "rennes",
    "maiorca": "mallorca",
    "sevilha": "sevilla",
    # Bélgica: o Transfermarkt.pt traduz o nome da cidade para
    # português ("Antuérpia"), a API-Football usa sempre o nome
    # inglês ("Antwerp") -- sem isto, NENHUM jogador deste clube
    # batia certo com o clube (só "antwerp" como substring de
    # "antuerpia" nunca acontece), e a verificação de clube falhava
    # para o plantel inteiro (só 1 em 42 jogadores ficou associado).
    "antuerpia": "antwerp",
    # "St." (abreviatura de "Saint"/"Sint") aparece de formas
    # diferentes consoante a fonte: a API-Football separa-o num token
    # próprio com ponto ("St. Truiden", "Union St. Gilloise"), o
    # Transfermarkt.pt às vezes usa "Saint" por extenso ("Saint-
    # Gilloise", já tratado como hífen->espaço) e outras vezes cola o
    # ponto ao nome seguinte sem espaço ("St.Truiden", um único
    # token). Normalizar sempre para "st" sem pontuação resolve as
    # três variantes ao mesmo tempo.
    "st.": "st",
    "saint": "st",
    "st.truiden": "st truiden",
}

# Alguns clubes têm abreviaturas/estrutura de nome tão diferentes
# entre as duas fontes que nenhuma combinação de alias por token ou
# substring resolve (ex: "Ham-Kam" é uma alcunha de "Hamarkameratene",
# não uma tradução token a token) -- aqui mapeia-se o nome limpo
# inteiro da API-Football diretamente para o equivalente do nosso
# lado, já limpo da mesma forma.
ALIASES_CLUBE_COMPLETO = {
    "ham kam": "hamarkameratene",
    "kfum oslo": "kfum kameratene oslo",
    "uniao leiria": "ud leiria",
}


def _limpar_conectores_clube(nome):
    """
    Remove só ligações gramaticais (de/do/da/...), nunca palavras
    que identificam o clube (ex: "real", "atletico", "athletic") --
    isso teria o risco oposto de fazer "Real Madrid" coincidir com
    "Atlético de Madrid" só por partilharem "madrid".

    Também troca hífens por espaços antes de tudo -- a Ligue 1 trouxe
    clubes como "AS Saint-Étienne" e "Paris Saint-Germain" (hífen na
    nossa BD, via Transfermarkt) contra "Saint Etienne"/"Paris Saint
    Germain" (espaço, via API-Football); sem isto, "saint etienne"
    nunca é substring de "saint-etienne" e a verificação de clube
    falhava para TODOS os jogadores dessas equipas, mesmo os corretos.
    """
    nome = nome.replace("-", " ")
    tokens = [ALIASES_CLUBE.get(t, t) for t in nome.split() if t not in CONECTORES_CLUBE]
    return " ".join(tokens)


def clubes_coincidem(nome_equipa_api, nomes_clubes_bd):
    """
    Compara o nome da equipa (API-Football) com os nomes de clube já
    associados a um candidato (Transfermarkt), de forma leniente
    (substring, sem acentos/maiúsculas/hífens, sem conectores como
    "de"), já que as duas fontes nem sempre usam a mesma grafia (ex.:
    "FC Porto" vs "Porto", "Atletico Madrid" vs "Atlético de Madrid",
    "Celta Vigo" vs "Celta de Vigo", "Saint Etienne" vs
    "Saint-Étienne").

    IMPORTANTE: só se removem conectores puramente gramaticais, para
    não arriscar fazer clubes DIFERENTES coincidirem por partilharem
    uma cidade (ex: "Real Madrid" nunca deve coincidir com "Atlético
    de Madrid" só por causa do "madrid" -- por isso "real" e
    "atletico" não entram na lista de conectores a remover).
    """

    alvo = _limpar_conectores_clube(normalizar_nome(nome_equipa_api))
    alvo = ALIASES_CLUBE_COMPLETO.get(alvo, alvo)

    for nome_clube in nomes_clubes_bd:
        clube = _limpar_conectores_clube(normalizar_nome(nome_clube))
        if alvo in clube or clube in alvo:
            return True

    return False


def escolher_candidato(candidatos, nome_equipa_api, nome_jogador_api=None):
    """
    Recebe [(jogador_id, {clubes}), ...] para um nome já
    correspondido, e desempata pelo clube da equipa a ser
    processada.

    IMPORTANTE: verifica sempre o clube, mesmo com um único
    candidato -- só porque só temos UM "Kaio César" na nossa BD
    não significa que a API-Football não esteja a devolver um
    "Kaio César" completamente diferente, de outro clube. Aceitar
    às cegas o único candidato já causou uma correspondência
    errada (bio de extremo em Vitória SC recebeu estatísticas de
    guarda-redes do Nacional). Só se aceita sem verificação de
    clube quando não há NENHUMA informação de clube para esse
    candidato (jogador_clube vazio) -- nesse caso não há como
    verificar, por isso mantém-se o comportamento antigo só para
    esse cenário.
    """

    for jogador_id, clubes in candidatos:
        if clubes_coincidem(nome_equipa_api, clubes):
            return jogador_id

    sem_clube_conhecido = [c for c in candidatos if not c[1]]
    if len(sem_clube_conhecido) == 1 and len(candidatos) == 1:
        return sem_clube_conhecido[0][0]

    log(
        f"  [aviso] Nome ambíguo ({nome_jogador_api or '?'}, {len(candidatos)} candidatos) e "
        f"nenhum coincide com o clube '{nome_equipa_api}' -- "
        f"a ignorar para não misturar jogadores diferentes."
    )
    return None


def encontrar_jogador_bd(stats, jogadores_bd, nome_equipa_api):
    """
    Correspondência exata primeiro, depois parcial (com aviso).
    Quando o nome tem vários candidatos reais (nomes comuns em
    português), desempata pelo clube da equipa a ser processada
    em vez de escolher às cegas -- ver escolher_candidato().
    """

    nomes_possiveis = [stats.get("nome"), stats.get("nome_completo")]

    nomes_normalizados = [
        normalizar_nome(n) for n in nomes_possiveis if n
    ]

    for nome in nomes_normalizados:
        if nome in jogadores_bd:
            jogador_id = escolher_candidato(jogadores_bd[nome], nome_equipa_api, nome)
            if jogador_id is not None:
                return jogador_id

    for nome_api in nomes_normalizados:
        for nome_bd, candidatos in jogadores_bd.items():
            if nomes_correspondem(nome_api, nome_bd):
                jogador_id = escolher_candidato(candidatos, nome_equipa_api, nome_api)
                if jogador_id is not None:
                    log(
                        f"  [aviso] Correspondência parcial: "
                        f"'{nome_api}' -> '{nome_bd}'"
                    )
                    return jogador_id

    return None


# ============================================================
# GUARDAR ESTATÍSTICAS / DISCIPLINA
# ============================================================

def guardar_estatisticas(conn, jogador_id, stats):
    competicao = stats.get("liga")

    rating = stats.get("rating")

    if rating is not None:
        try:
            rating = float(rating)
        except (ValueError, TypeError):
            rating = None

    passes_certos_pct = stats.get("passes_certos_pct")
    if passes_certos_pct is not None:
        try:
            passes_certos_pct = float(str(passes_certos_pct).replace("%", ""))
        except (ValueError, TypeError):
            passes_certos_pct = None

    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO estatisticas_jogador (
                jogador_id, epoca, competicao, jogos, titularidades,
                minutos, golos, assistencias, classificacao_media,
                posicao_api, equipa,
                remates_totais, remates_a_baliza, golos_sofridos, defesas,
                passes_totais, passes_chave, passes_certos_pct,
                duelos_totais, duelos_ganhos, desarmes, bloqueios, intercecoes,
                dribles_tentados, dribles_conseguidos,
                penaltis_marcados, penaltis_falhados, penaltis_defendidos,
                data_recolha
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                CURRENT_DATE
            )
            ON CONFLICT (jogador_id, epoca, competicao)
            DO UPDATE SET
                jogos = EXCLUDED.jogos,
                titularidades = EXCLUDED.titularidades,
                minutos = EXCLUDED.minutos,
                golos = EXCLUDED.golos,
                assistencias = EXCLUDED.assistencias,
                classificacao_media = EXCLUDED.classificacao_media,
                posicao_api = EXCLUDED.posicao_api,
                equipa = EXCLUDED.equipa,
                remates_totais = EXCLUDED.remates_totais,
                remates_a_baliza = EXCLUDED.remates_a_baliza,
                golos_sofridos = EXCLUDED.golos_sofridos,
                defesas = EXCLUDED.defesas,
                passes_totais = EXCLUDED.passes_totais,
                passes_chave = EXCLUDED.passes_chave,
                passes_certos_pct = EXCLUDED.passes_certos_pct,
                duelos_totais = EXCLUDED.duelos_totais,
                duelos_ganhos = EXCLUDED.duelos_ganhos,
                desarmes = EXCLUDED.desarmes,
                bloqueios = EXCLUDED.bloqueios,
                intercecoes = EXCLUDED.intercecoes,
                dribles_tentados = EXCLUDED.dribles_tentados,
                dribles_conseguidos = EXCLUDED.dribles_conseguidos,
                penaltis_marcados = EXCLUDED.penaltis_marcados,
                penaltis_falhados = EXCLUDED.penaltis_falhados,
                penaltis_defendidos = EXCLUDED.penaltis_defendidos,
                data_recolha = CURRENT_DATE
        """, (
            jogador_id, TEMPORADA, competicao,
            stats.get("jogos") or 0,
            stats.get("titular") or 0,
            stats.get("minutos") or 0,
            stats.get("golos") or 0,
            stats.get("assistencias") or 0,
            rating,
            stats.get("posicao"),
            stats.get("equipa"),
            stats.get("remates_totais"),
            stats.get("remates_a_baliza"),
            stats.get("golos_sofridos"),
            stats.get("defesas"),
            stats.get("passes_totais"),
            stats.get("passes_chave"),
            passes_certos_pct,
            stats.get("duelos_totais"),
            stats.get("duelos_ganhos"),
            stats.get("desarmes"),
            stats.get("bloqueios"),
            stats.get("intercecoes"),
            stats.get("dribles_tentados"),
            stats.get("dribles_conseguidos"),
            stats.get("penaltis_marcados"),
            stats.get("penaltis_falhados"),
            stats.get("penaltis_defendidos"),
        ))


def guardar_disciplina(conn, jogador_id, stats):
    competicao = stats.get("liga")

    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO disciplina_jogador (
                jogador_id, epoca, competicao, cartoes_amarelos,
                cartoes_vermelhos, expulsoes_segundo_amarelo,
                faltas_cometidas, faltas_sofridas, suspensoes,
                jogos_falhados_castigo, data_recolha
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE)
            ON CONFLICT (jogador_id, epoca, competicao)
            DO UPDATE SET
                cartoes_amarelos = EXCLUDED.cartoes_amarelos,
                cartoes_vermelhos = EXCLUDED.cartoes_vermelhos,
                expulsoes_segundo_amarelo = EXCLUDED.expulsoes_segundo_amarelo,
                faltas_cometidas = EXCLUDED.faltas_cometidas,
                faltas_sofridas = EXCLUDED.faltas_sofridas,
                data_recolha = CURRENT_DATE
        """, (
            jogador_id, TEMPORADA, competicao,
            stats.get("cartoes_amarelos") or 0,
            stats.get("cartoes_vermelhos") or 0,
            stats.get("cartoes_amarelo_vermelho") or 0,
            stats.get("faltas_cometidas") or 0,
            stats.get("faltas_sofridas") or 0,
            0, 0
        ))


# ============================================================
# PROCESSAR UMA EQUIPA
# ============================================================

def processar_equipa(conn, jogadores_bd, liga_id, equipa):
    jogadores_raw = obter_jogadores_do_clube(equipa["id"], EPOCA)

    log(f"    Jogadores devolvidos pela API: {len(jogadores_raw)}")

    encontrados = 0
    nao_encontrados = 0

    for jogador_raw in jogadores_raw:
        stats = extrair_estatisticas_completas(jogador_raw, liga_id)

        if not stats:
            continue

        jogador_id = encontrar_jogador_bd(stats, jogadores_bd, equipa["nome"])

        if jogador_id is None:
            nao_encontrados += 1
            log(
                f"  [aviso] Não encontrado na BD: {stats.get('nome')} "
                f"(nome completo: {stats.get('nome_completo')})"
            )
            continue

        guardar_estatisticas(conn, jogador_id, stats)
        guardar_disciplina(conn, jogador_id, stats)

        encontrados += 1

        log(f"  [OK] {stats.get('nome')} | ID BD: {jogador_id}")

        time.sleep(0.2)

    conn.commit()

    log(
        f"    {equipa['nome']}: {encontrados} guardados, "
        f"{nao_encontrados} não encontrados na BD."
    )


# ============================================================
# PROCESSAMENTO PRINCIPAL
# ============================================================

def main():
    progresso = carregar_progresso()

    conn = psycopg.connect(**DB_CONFIG)

    try:
        jogadores_bd = obter_jogadores_bd(conn)

        log(f"Jogadores na BD (para correspondência): {len(jogadores_bd)}")

        for pais, nome_liga in LIGAS_ALVO:
            liga_id, nome_oficial_liga = obter_id_liga(pais, nome_liga)

            log(f"=== LIGA: {nome_oficial_liga} (id {liga_id}) ===")

            equipas = obter_equipas_liga(liga_id, EPOCA)

            log(f"Equipas encontradas: {len(equipas)}")

            for indice, equipa in enumerate(equipas, start=1):

                if equipa_ja_concluida(progresso, liga_id, equipa["id"]):
                    log(
                        f"[{indice}/{len(equipas)}] {equipa['nome']} "
                        f"-- já processado, a saltar."
                    )
                    continue

                log(
                    f"[{indice}/{len(equipas)}] A processar "
                    f"{equipa['nome']} (ID API: {equipa['id']})"
                )

                try:
                    processar_equipa(conn, jogadores_bd, liga_id, equipa)

                    marcar_equipa_concluida(progresso, liga_id, equipa["id"])

                except Exception as erro_equipa:
                    conn.rollback()

                    log(f"ERRO ao processar {equipa['nome']}: {erro_equipa}")
                    log(
                        "Progresso guardado até este ponto -- podes "
                        "correr o script outra vez para retomar."
                    )

                    raise

                time.sleep(1)

        log("Todas as ligas e equipas foram processadas!")

    finally:
        conn.close()
        log("Ligação à base de dados fechada.")


if __name__ == "__main__":
    main()