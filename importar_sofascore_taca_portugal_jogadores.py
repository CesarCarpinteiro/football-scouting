"""
Recolhe estatísticas POR JOGO (não agregadas) de cada jogador em cada
partida da Taça de Portugal 2024/25, e guarda em
estatisticas_sofascore_jogo. Depois agrega essas linhas em
estatisticas_sofascore (a mesma tabela que já usas para as ligas) --
ver agregar_estatisticas_taca.sql, a correr DEPOIS deste script.

PORQUÊ POR-JOGO EM VEZ DE PEDIR O AGREGADO DIRETO: já confirmámos ao
vivo (ver importar_sofascore_taca_portugal.py) que a Sofascore não
calcula estatísticas de época para provas a eliminar -- tanto a
listagem em massa como o endpoint /player/.../statistics/overall
devolvem vazio/404 para a Taça, mesmo para jogadores que a disputaram
toda. Por isso agregamos nós: pedimos o /event/{id}/lineups de cada
jogo (que tem as estatísticas desse jogo específico) e somamos no fim.

NOMES DOS CAMPOS -- confirmados ao vivo em 2 jogos reais (incl. a
Final) antes de escrever isto, porque o vocabulário por-jogo da
Sofascore é DIFERENTE do agregado de época que importar_sofascore.py
já usa (ex: "totalPass" aqui, "totalPasses" lá -- mesmo conceito, nome
diferente). Ver CAMPOS_SOMAR abaixo. cartoes_amarelos/vermelhos NÃO
aparecem nas statistics por-jogo (só no agregado de época) -- ficam de
fora, sem adivinhar. duelos_terrestres_ganhos também não tem campo
próprio por-jogo -- é calculado na agregação como duelos_totais_ganhos
(duelWon) menos duelos_aereos_ganhos (aerialWon).

MAPEAMENTO JOGADOR SOFASCORE -> JOGADOR LOCAL: por sofascore_id direto
(já gravado em `jogadores` de uma importação de liga anterior) -- um
jogador sem sofascore_id gravado (nunca visto numa liga que sigas)
fica de fora sem ser erro, mesma filosofia do resto do projeto.

Corre migrar_estatisticas_sofascore_jogo.sql primeiro (já feito).

Uso:
    python3 importar_sofascore_taca_portugal_jogadores.py
"""

import asyncio
import json
import os
import random
import time

import psycopg
from wreq import Client, Emulation

import api_stats as ast

TORNEIO_ID = 336
EPOCA_ID = 65040  # "24/25", confirmado ao vivo (ver importar_sofascore_taca_portugal.py)

BASE_URL = "https://www.sofascore.com/api/v1"
EMULATION = Emulation.Chrome149

SLEEP_MIN = 3.0
SLEEP_MAX = 6.0

RETRIES_403 = 0  # ao primeiro 403, para tudo -- mesma filosofia do importar_sofascore.py
ESPERA_BASE_403 = 45

PROGRESSO_FICHEIRO = "progresso_sofascore_taca_jogadores.json"

# Campos confirmados ao vivo nas statistics por-jogo (2 jogos reais,
# incl. a Final Benfica-Sporting) -- ver nota no topo do ficheiro.
CAMPOS_SOMAR = {
    "golos": "goals",
    "assistencias": "goalAssist",
    "remates_totais": "totalShots",
    "remates_a_baliza": "onTargetScoringAttempt",
    "passes_totais": "totalPass",
    "passes_certos": "accuratePass",
    "passes_chave": "keyPass",
    "dribles_tentados": "totalContest",
    "dribles_conseguidos": "wonContest",
    "toques": "touches",
    "duelos_totais_ganhos": "duelWon",
    "duelos_aereos_ganhos": "aerialWon",
    "desarmes": "totalTackle",
    "desarmes_ganhos": "wonTackle",
    "intercecoes": "interceptionWon",
    "faltas": "fouls",

    # Campos adicionais -- confirmados ao vivo na mesma verificação
    # (jogo de campo + um guarda-redes), inicialmente deixados de fora
    # por cautela, mas o utilizador notou que a secção da Taça ficava
    # pobre comparada com a da liga, e de facto a Sofascore dá muito
    # mais do que isto por-jogo.
    "grandes_ocasioes_criadas": "bigChanceCreated",
    "grandes_ocasioes_falhadas": "bigChanceMissed",
    "remates_bloqueados": "blockedScoringAttempt",
    "remates_ao_poste": "hitWoodwork",
    "remates_fora": "shotOffTarget",
    "erros_geraram_remate": "errorLeadToAShot",
    "cruzamentos_certos": "accurateCross",
    "cruzamentos_totais": "totalCross",
    "bolas_longas_certas": "accurateLongBalls",
    "bolas_longas_totais": "totalLongBalls",
    "passes_campo_contrario_certos": "accurateOppositionHalfPasses",
    "passes_campo_contrario_totais": "totalOppositionHalfPasses",
    "passes_proprio_campo_certos": "accurateOwnHalfPasses",
    "passes_proprio_campo_totais": "totalOwnHalfPasses",
    "foras_de_jogo": "totalOffside",
    "recuperacoes_bola": "ballRecovery",
    "duelos_aereos_perdidos": "aerialLost",
    "duelos_perdidos": "duelLost",
    "posse_perdida": "possessionLostCtrl",
    "sofreu_faltas": "wasFouled",
    "penaltis_cometidos": "penaltyConceded",
    "penaltis_conquistados": "penaltyWon",
    "alivios": "totalClearance",

    # guarda-redes
    "defesas": "saves",
    "remates_defendidos_dentro_area": "savedShotsFromInsideTheBox",
    "penaltis_sofridos": "penaltyFaced",
    "saidas_aereas": "goodHighClaim",
    "saidas_totais": "totalKeeperSweeper",
    "saidas_bem_sucedidas": "accurateKeeperSweeper",
}


def sleep_pedido():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def carregar_progresso():
    if not os.path.exists(PROGRESSO_FICHEIRO):
        return {"eventos_concluidos": []}
    with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_progresso(progresso):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, indent=2, ensure_ascii=False)


def _pedir(client, url):
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
        ast.log("  [429] limite atingido, à espera 8s...")
        time.sleep(8)
        return wreq_fetch(client, url, tentativas_403)
    if status == 403:
        if tentativas_403 >= RETRIES_403:
            raise RuntimeError(
                f"Bloqueado pela Cloudflare (403) em {url} -- a parar tudo "
                f"(checkpoint já guardado continua válido)."
            )
        espera = ESPERA_BASE_403 * (tentativas_403 + 1)
        ast.log(f"  [403] bloqueado, à espera {espera}s antes de retry...")
        time.sleep(espera)
        return wreq_fetch(client, url, tentativas_403 + 1)

    ast.log(f"  [{status}] erro inesperado em {url}")
    return None


def obter_rondas(client):
    dados = wreq_fetch(client, f"{BASE_URL}/unique-tournament/{TORNEIO_ID}/season/{EPOCA_ID}/rounds")
    sleep_pedido()
    return dados["rounds"] if dados else []


def obter_jogos_ronda(client, ronda):
    url = f"{BASE_URL}/unique-tournament/{TORNEIO_ID}/season/{EPOCA_ID}/events/round/{ronda['round']}/slug/{ronda['slug']}"
    dados = wreq_fetch(client, url)
    sleep_pedido()
    return dados["events"] if dados else []


def obter_lineups(client, event_id):
    dados = wreq_fetch(client, f"{BASE_URL}/event/{event_id}/lineups")
    sleep_pedido()
    return dados


def obter_golos_por_tipo(client, event_id):
    """
    O shotmap não tem xG para esta prova (confirmado ao vivo), mas tem
    bodyPart/goalType por remate -- dá para contar golos de cabeça/pé
    direito/pé esquerdo/penálti por jogador, que a lineups/statistics
    não tem. Devolve {sofascore_player_id: {chave: contagem}}.
    """
    dados = wreq_fetch(client, f"{BASE_URL}/event/{event_id}/shotmap")
    sleep_pedido()
    if not dados:
        return {}

    por_jogador = {}
    for remate in dados.get("shotmap", []):
        if remate.get("shotType") != "goal":
            continue
        jogador_id_sofa = (remate.get("player") or {}).get("id")
        if not jogador_id_sofa:
            continue
        contagens = por_jogador.setdefault(jogador_id_sofa, {
            "golos_cabeca": 0, "golos_pe_direito": 0, "golos_pe_esquerdo": 0, "golos_penalti": 0,
        })
        parte = remate.get("bodyPart")
        if parte == "head":
            contagens["golos_cabeca"] += 1
        elif parte == "right-foot":
            contagens["golos_pe_direito"] += 1
        elif parte == "left-foot":
            contagens["golos_pe_esquerdo"] += 1
        if remate.get("goalType") == "penalty":
            contagens["golos_penalti"] += 1
    return por_jogador


def mapear_sofascore_id_para_jogador_local(conn, sofascore_ids):
    """
    Resolve sofascore_id -> jogador_id local, direto (sem matching por
    nome) -- assume que esses jogadores já foram vistos numa liga que
    segues e já têm sofascore_id gravado em `jogadores`. Quem não
    aparecer aqui fica de fora (jogador não rastreado), sem ser erro.
    """
    if not sofascore_ids:
        return {}
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT sofascore_id, id FROM jogadores WHERE sofascore_id = ANY(%s)",
            (list(sofascore_ids),),
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def extrair_stats_relevantes(stats_brutas):
    return {chave_bd: stats_brutas.get(chave_sofa) for chave_bd, chave_sofa in CAMPOS_SOMAR.items()}


def guardar_linha_jogo(conn, jogador_id, sofascore_id, event_id, data_jogo, titular, minutos, rating, stats_relevantes):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO estatisticas_sofascore_jogo
                (jogador_id, sofascore_id, liga_id, epoca_id, event_id, data_jogo, titular, minutos, rating, stats_jogo)
            VALUES (%(jogador_id)s, %(sofascore_id)s, %(liga_id)s, %(epoca_id)s, %(event_id)s,
                    %(data_jogo)s, %(titular)s, %(minutos)s, %(rating)s, %(stats_jogo)s)
            ON CONFLICT (jogador_id, liga_id, epoca_id, event_id) DO UPDATE SET
                minutos = EXCLUDED.minutos, rating = EXCLUDED.rating,
                stats_jogo = EXCLUDED.stats_jogo, atualizado_em = CURRENT_TIMESTAMP
            """,
            {
                "jogador_id": jogador_id, "sofascore_id": sofascore_id,
                "liga_id": TORNEIO_ID, "epoca_id": EPOCA_ID,
                "event_id": event_id, "data_jogo": data_jogo,
                "titular": titular, "minutos": minutos, "rating": rating,
                "stats_jogo": json.dumps(stats_relevantes),
            },
        )
    conn.commit()


def processar_jogo(conn, client, event_id, data_jogo):
    lineups = obter_lineups(client, event_id)
    if not lineups:
        return 0

    golos_por_tipo = obter_golos_por_tipo(client, event_id)

    sofascore_ids = set()
    jogadores_dados = []

    for lado in ("home", "away"):
        for p in lineups.get(lado, {}).get("players", []):
            sofascore_id_jogador = (p.get("player") or {}).get("id")
            stats = p.get("statistics") or {}
            if not sofascore_id_jogador or not stats:
                continue
            sofascore_ids.add(sofascore_id_jogador)
            stats_relevantes = extrair_stats_relevantes(stats)
            stats_relevantes.update(golos_por_tipo.get(sofascore_id_jogador, {}))
            jogadores_dados.append({
                "sofascore_id": sofascore_id_jogador,
                "titular": p.get("substitute") is False,
                "minutos": stats.get("minutesPlayed"),
                "rating": stats.get("rating"),
                "stats": stats_relevantes,
            })

    mapa = mapear_sofascore_id_para_jogador_local(conn, sofascore_ids)

    guardados = 0
    for j in jogadores_dados:
        jogador_id = mapa.get(j["sofascore_id"])
        if jogador_id is None:
            continue  # jogador não rastreado (nunca visto numa liga que sigas) -- normal
        guardar_linha_jogo(
            conn, jogador_id, j["sofascore_id"], event_id, data_jogo,
            j["titular"], j["minutos"], j["rating"], j["stats"],
        )
        guardados += 1
    return guardados


def main():
    client = Client(emulation=EMULATION)
    conn = psycopg.connect(**ast.DB_CONFIG)
    progresso = carregar_progresso()

    try:
        rondas = obter_rondas(client)
        ast.log(f"Rondas encontradas: {[r['name'] for r in rondas]}")

        for ronda in rondas:
            jogos = obter_jogos_ronda(client, ronda)
            ast.log(f"=== {ronda['name']}: {len(jogos)} jogos ===")

            for jogo in jogos:
                event_id = jogo["id"]
                chave = str(event_id)
                if chave in progresso["eventos_concluidos"]:
                    continue
                if jogo.get("status", {}).get("type") != "finished":
                    continue

                data_jogo = time.strftime("%Y-%m-%d", time.gmtime(jogo["startTimestamp"]))

                try:
                    guardados = processar_jogo(conn, client, event_id, data_jogo)
                except RuntimeError as erro:
                    ast.log(f"  [BLOQUEADO] {erro}")
                    ast.log("  A parar -- o checkpoint já guardado continua válido, retoma mais tarde.")
                    return

                ast.log(f"  {jogo['homeTeam']['name']} vs {jogo['awayTeam']['name']} "
                        f"({data_jogo}): {guardados} jogadores guardados")
                progresso["eventos_concluidos"].append(chave)
                guardar_progresso(progresso)

        ast.log("Recolha por-jogo concluída. Agora corre agregar_estatisticas_taca.sql.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
