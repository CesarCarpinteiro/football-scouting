import os
import sys
import time
import unicodedata
import requests
import psycopg
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

NOME_CLUBE = "FC Porto"

EPOCA = 2023

LIGA_ID = 94

COMPETICAO_NOME = "Primeira Liga"

TEMPORADA = f"{EPOCA}/{str(EPOCA + 1)[-2:]}"


# ============================================================
# PEDIDOS À API-FOOTBALL
# ============================================================

def chamar(endpoint, params):
    """
    Faz um pedido à API-Football.
    """

    url = f"{BASE_URL}/{endpoint}"

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=30
        )

        response.raise_for_status()

    except requests.RequestException as erro:
        raise RuntimeError(
            f"Erro ao chamar o endpoint '{endpoint}': {erro}"
        ) from erro

    data = response.json()

    if data.get("errors"):
        print(
            f"[aviso] Erros da API no endpoint "
            f"{endpoint}: {data['errors']}"
        )

    return data


# ============================================================
# OBTER CLUBE
# ============================================================

def obter_id_clube(nome_clube):
    """
    Obtém o ID do clube através do nome.
    """

    data = chamar(
        "teams",
        {
            "search": nome_clube
        }
    )

    equipas = data.get("response", [])

    if not equipas:
        raise ValueError(
            f"Clube '{nome_clube}' não encontrado."
        )

    equipa = equipas[0]

    team_id = equipa["team"]["id"]

    nome_oficial = equipa["team"]["name"]

    return team_id, nome_oficial


# ============================================================
# OBTER JOGADORES DO CLUBE
# ============================================================

def obter_jogadores_do_clube(team_id, epoca):
    """
    Obtém todos os jogadores do clube para a época indicada.
    """

    jogadores = []

    pagina = 1

    while True:
        print(
            f"A obter jogadores da API - página {pagina}"
        )

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

        time.sleep(1)

    return jogadores


# ============================================================
# EXTRAIR ESTATÍSTICAS
# ============================================================

def extrair_estatisticas_completas(jogador_raw, liga_id):
    """
    Extrai os dados estatísticos da competição indicada.

    A função devolve os campos que existem na resposta
    statistics[] da API-Football.
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

    substitutos_info = (
        stats_liga.get("substitutes", {}) or {}
    )

    remates_info = stats_liga.get("shots", {}) or {}

    golos_info = stats_liga.get("goals", {}) or {}

    passes_info = stats_liga.get("passes", {}) or {}

    duelos_info = stats_liga.get("duels", {}) or {}

    dribles_info = stats_liga.get("dribbles", {}) or {}

    faltas_info = stats_liga.get("fouls", {}) or {}

    cartoes_info = stats_liga.get("cards", {}) or {}

    penaltis_info = stats_liga.get("penalty", {}) or {}

    tackles_info = stats_liga.get("tackles", {}) or {}

    resultado = {
        # ----------------------------------------------------
        # IDENTIFICAÇÃO
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # CLUBE E COMPETIÇÃO
        # ----------------------------------------------------

        "liga": stats_liga.get(
            "league", {}
        ).get("name"),

        "liga_id": stats_liga.get(
            "league", {}
        ).get("id"),

        "epoca_liga": stats_liga.get(
            "league", {}
        ).get("season"),

        "equipa": stats_liga.get(
            "team", {}
        ).get("name"),

        # ----------------------------------------------------
        # JOGOS
        # ----------------------------------------------------

        "posicao": jogos_info.get("position"),

        "jogos": jogos_info.get("appearences"),

        "titular": jogos_info.get("lineups"),

        "minutos": jogos_info.get("minutes"),

        "numero_camisola": jogos_info.get("number"),

        "capitao": jogos_info.get("captain"),

        "rating": jogos_info.get("rating"),

        # ----------------------------------------------------
        # SUPLENTES
        # ----------------------------------------------------

        "suplente_entrou": substitutos_info.get("in"),

        "suplente_saiu": substitutos_info.get("out"),

        "suplente_banco": substitutos_info.get("bench"),

        # ----------------------------------------------------
        # REMATES
        # ----------------------------------------------------

        "remates_totais": remates_info.get("total"),

        "remates_a_baliza": remates_info.get("on"),

        # ----------------------------------------------------
        # GOLOS
        # ----------------------------------------------------

        "golos": golos_info.get("total"),

        "assistencias": golos_info.get("assists"),

        "golos_sofridos": golos_info.get("conceded"),

        "defesas": golos_info.get("saves"),

        # ----------------------------------------------------
        # PASSES
        # ----------------------------------------------------

        "passes_totais": passes_info.get("total"),

        "passes_chave": passes_info.get("key"),

        "passes_certos_pct": passes_info.get("accuracy"),

        # ----------------------------------------------------
        # DUELOS
        # ----------------------------------------------------

        "duelos_totais": duelos_info.get("total"),

        "duelos_ganhos": duelos_info.get("won"),

        # ----------------------------------------------------
        # DESARMES E INTERCEÇÕES
        # ----------------------------------------------------

        "desarmes": tackles_info.get("total"),

        "bloqueios": tackles_info.get("blocks"),

        "intercecoes": tackles_info.get("interceptions"),

        # ----------------------------------------------------
        # DRIBLES
        # ----------------------------------------------------

        "dribles_tentados": dribles_info.get("attempts"),

        "dribles_conseguidos": dribles_info.get("success"),

        "dribles_sofridos": dribles_info.get("past"),

        # ----------------------------------------------------
        # FALTAS
        # ----------------------------------------------------

        "faltas_sofridas": faltas_info.get("drawn"),

        "faltas_cometidas": faltas_info.get("committed"),

        # ----------------------------------------------------
        # CARTÕES
        # ----------------------------------------------------

        "cartoes_amarelos": cartoes_info.get("yellow"),

        "cartoes_amarelo_vermelho": (
            cartoes_info.get("yellowred")
        ),

        "cartoes_vermelhos": cartoes_info.get("red"),

        # ----------------------------------------------------
        # PENÁLTIS
        # ----------------------------------------------------

        "penaltis_ganhos": penaltis_info.get("won"),

        "penaltis_cometidos": (
            penaltis_info.get("commited")
        ),

        "penaltis_marcados": penaltis_info.get("scored"),

        "penaltis_falhados": penaltis_info.get("missed"),

        "penaltis_defendidos": penaltis_info.get("saved"),

        # ----------------------------------------------------
        # FORAS DE JOGO
        # ----------------------------------------------------

        "foras_de_jogo": stats_liga.get("offsides"),
    }

    return resultado


# ============================================================
# NORMALIZAÇÃO DE NOMES
# ============================================================

def normalizar_nome(nome):
    """
    Normaliza nomes para facilitar a correspondência.

    Exemplo:
    João Mário -> joao mario
    """

    if not nome:
        return ""

    nome = str(nome).strip().lower()

    nome = unicodedata.normalize(
        "NFKD",
        nome
    )

    nome = "".join(
        caracter
        for caracter in nome
        if not unicodedata.combining(caracter)
    )

    return " ".join(nome.split())


# ============================================================
# BASE DE DADOS: JOGADORES
# ============================================================

def obter_jogadores_bd(conn):
    """
    Obtém os jogadores existentes na BD.
    """

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT id, nome
            FROM jogadores
        """)

        resultados = cursor.fetchall()

    jogadores = {}

    for jogador_id, nome in resultados:
        nome_normalizado = normalizar_nome(nome)

        jogadores[nome_normalizado] = jogador_id

    return jogadores


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


def encontrar_jogador_bd(stats, jogadores_bd):
    """
    Tenta encontrar o jogador da API na BD.

    Primeiro tenta correspondência exata (nome curto e nome
    completo). Se falhar, tenta correspondência parcial
    (ex: BD "Francisco Evanilson" vs API "Evanilson", ou
    BD "Marko Grujić" vs API "M. Grujić").

    ATENÇÃO: a correspondência parcial pode juntar jogadores
    errados com nomes parecidos -- imprime sempre um aviso
    quando isso acontece, para revisares manualmente. A
    solução definitiva é guardar o api_football_id na tabela
    jogadores e deixar de depender do nome.
    """

    nomes_possiveis = [
        stats.get("nome"),
        stats.get("nome_completo")
    ]

    nomes_normalizados = [
        normalizar_nome(nome)
        for nome in nomes_possiveis
        if nome
    ]

    # Primeiro: correspondência exata
    for nome in nomes_normalizados:
        if nome in jogadores_bd:
            return jogadores_bd[nome]

    # Segundo: correspondência parcial (fallback)
    for nome_api in nomes_normalizados:
        for nome_bd, jogador_id in jogadores_bd.items():
            if nomes_correspondem(nome_api, nome_bd):
                print(
                    f"[aviso] Correspondência parcial: "
                    f"'{nome_api}' -> '{nome_bd}' "
                    f"(confirma que é o jogador certo)"
                )
                return jogador_id

    return None


# ============================================================
# GUARDAR ESTATÍSTICAS GERAIS
# ============================================================

def guardar_estatisticas(conn, jogador_id, stats):
    """
    Guarda ou atualiza as estatísticas acumuladas.
    """

    competicao = stats.get("liga") or COMPETICAO_NOME

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

            ON CONFLICT (
                jogador_id,
                epoca,
                competicao
            )

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
            jogador_id,
            TEMPORADA,
            competicao,
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


# ============================================================
# GUARDAR DISCIPLINA
# ============================================================

def guardar_disciplina(conn, jogador_id, stats):
    """
    Guarda cartões e faltas do jogador.

    A API-Football não fornece necessariamente suspensões
    e jogos falhados por castigo neste endpoint.
    Esses campos ficam a zero até existir uma fonte adequada.
    """

    competicao = stats.get("liga") or COMPETICAO_NOME

    amarelos = stats.get("cartoes_amarelos") or 0

    amarelo_vermelho = (
        stats.get("cartoes_amarelo_vermelho") or 0
    )

    vermelhos = stats.get("cartoes_vermelhos") or 0

    faltas_cometidas = (
        stats.get("faltas_cometidas") or 0
    )

    faltas_sofridas = (
        stats.get("faltas_sofridas") or 0
    )

    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO disciplina_jogador (
                jogador_id,
                epoca,
                competicao,
                cartoes_amarelos,
                cartoes_vermelhos,
                expulsoes_segundo_amarelo,
                faltas_cometidas,
                faltas_sofridas,
                suspensoes,
                jogos_falhados_castigo,
                data_recolha
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                CURRENT_DATE
            )

            ON CONFLICT (
                jogador_id,
                epoca,
                competicao
            )

            DO UPDATE SET
                cartoes_amarelos =
                    EXCLUDED.cartoes_amarelos,

                cartoes_vermelhos =
                    EXCLUDED.cartoes_vermelhos,

                expulsoes_segundo_amarelo =
                    EXCLUDED.expulsoes_segundo_amarelo,

                faltas_cometidas =
                    EXCLUDED.faltas_cometidas,

                faltas_sofridas =
                    EXCLUDED.faltas_sofridas,

                data_recolha = CURRENT_DATE
        """, (
            jogador_id,
            TEMPORADA,
            competicao,
            amarelos,
            vermelhos,
            amarelo_vermelho,
            faltas_cometidas,
            faltas_sofridas,
            0,
            0
        ))


# ============================================================
# PROCESSAMENTO PRINCIPAL
# ============================================================

def main():
    """
    Processo principal de importação.
    """

    team_id, nome_oficial = obter_id_clube(
        NOME_CLUBE
    )

    print(
        f"Clube encontrado: {nome_oficial} "
        f"(ID: {team_id})"
    )

    jogadores_raw = obter_jogadores_do_clube(
        team_id,
        EPOCA
    )

    print(
        f"Jogadores devolvidos pela API: "
        f"{len(jogadores_raw)}"
    )

    conn = psycopg.connect(**DB_CONFIG)

    try:
        jogadores_bd = obter_jogadores_bd(conn)

        encontrados = 0

        nao_encontrados = 0

        for jogador_raw in jogadores_raw:
            stats = extrair_estatisticas_completas(
                jogador_raw,
                LIGA_ID
            )

            if not stats:
                continue

            jogador_id = encontrar_jogador_bd(
                stats,
                jogadores_bd
            )

            if jogador_id is None:
                nao_encontrados += 1

                print(
                    "[aviso] Jogador não encontrado "
                    f"na BD: {stats.get('nome')} "
                    f"| nome completo: "
                    f"{stats.get('nome_completo')}"
                )

                continue

            guardar_estatisticas(
                conn,
                jogador_id,
                stats
            )

            guardar_disciplina(
                conn,
                jogador_id,
                stats
            )

            encontrados += 1

            print(
                f"[OK] {stats.get('nome')} "
                f"| ID BD: {jogador_id}"
            )

            time.sleep(0.2)

        conn.commit()

        print(
            "\nTransação concluída com sucesso."
        )

        print(
            f"Jogadores guardados: {encontrados}"
        )

        print(
            f"Jogadores não encontrados na BD: "
            f"{nao_encontrados}"
        )

    except Exception:
        conn.rollback()

        print(
            "\nErro durante a importação. "
            "Alterações revertidas."
        )

        raise

    finally:
        conn.close()

    print(
        "\nImportação concluída."
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":
    main()