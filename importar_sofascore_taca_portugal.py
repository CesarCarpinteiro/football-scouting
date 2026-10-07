"""
Completa os golos (com minuto real, não o sprite CSS sem texto do
Transfermarkt) e o MVP da Taça de Portugal 2024/25 com dados da
Sofascore -- substitui o que completar_golos_taca_portugal.py já
tinha preenchido com a API-Football, porque a Sofascore é a fonte
preferida "em caso de dúvida" neste projeto (mesma fonte usada para o
rating de época dos jogadores).

IMPORTANTE -- o que NÃO funciona para esta prova, e porquê (verificado
ao vivo antes de escrever este script, para não repetir a tentativa):
  - /unique-tournament/336/season/65040/statistics (listagem agregada
    de jogadores por época) devolve sempre vazio.
  - /player/{id}/unique-tournament/336/season/65040/statistics/overall
    devolve 404 mesmo para jogadores que disputaram a prova toda
    (ex: Gyökeres).
  Ou seja: o pipeline de importar_sofascore.py (pensado para ligas,
  com estatísticas agregadas de época) não serve aqui -- a Sofascore
  não calcula esse agregado para provas a eliminar.

O que FUNCIONA (confirmado ao vivo) é o calendário por ronda +
detalhe por jogo:
  1. /unique-tournament/336/season/65040/rounds
     -> lista de rondas (round-1..round-5, quarterfinals, semifinals, final)
  2. /unique-tournament/336/season/65040/events/round/{n}/slug/{slug}
     -> jogos dessa ronda (inclui clubes de divisões inferiores, que
        não rastreamos -- ficam de fora sem ser erro, como seria
        qualquer adversário não tracked)
  3. /event/{id}/incidents -> golos, com minuto real (time+addedTime)
  4. /event/{id}/lineups -> rating por jogador -> MVP = maior rating
     dos 2 lados (lineups pode faltar em jogos menores/divisões mais
     baixas -- não é erro, fica sem MVP desse jogo nesse caso)

Os clubes de ambos os lados são resolvidos por NOME (a Sofascore usa
os seus próprios IDs de clube, sem ligação a transfermarkt_id) --
reutiliza encontrar_clube_bd() de importar_resultados_clube.py, já
testado.

Faz UPDATE em cima das linhas já existentes em resultados_clube
(competicao='Taça de Portugal', vindas do Transfermarkt), casando por
(clube_id, data_jogo) -- mesma lógica de
completar_golos_taca_portugal.py. NÃO mexe em data_jogo/adversario/
golos_casa/golos_fora/resultado/fase.

Uso:
    python3 importar_sofascore_taca_portugal.py
"""

import asyncio
import json
import random
import time

import psycopg
from wreq import Client, Emulation

import api_stats as ast
import importar_resultados_clube as irc

TORNEIO_ID = 336
EPOCA_ID = 65040  # "24/25", confirmado ao vivo
COMPETICAO_NOME = "Taça de Portugal"

BASE_URL = "https://www.sofascore.com/api/v1"
EMULATION = Emulation.Chrome149

SLEEP_MIN = 3.0
SLEEP_MAX = 6.0

RETRIES_403 = 2
ESPERA_BASE_403 = 45

PROGRESSO_FICHEIRO = "progresso_sofascore_taca_portugal.json"


def sleep_pedido():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def carregar_progresso():
    import os
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
                f"Bloqueado pela Cloudflare (403) em {url} -- "
                f"mesmo depois de {RETRIES_403} tentativas, a parar tudo."
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


def obter_golos_e_mvp(client, event_id):
    golos = []
    inc = wreq_fetch(client, f"{BASE_URL}/event/{event_id}/incidents")
    sleep_pedido()
    if inc:
        for i in inc.get("incidents", []):
            if i.get("incidentType") != "goal":
                continue
            # Em jogos de divisões mais baixas, a Sofascore às vezes
            # não tem perfil completo do marcador -- só dá o nome em
            # texto solto ("playerName") em vez do objeto "player"
            # habitual. Sem este fallback, estes golos rebentavam o
            # pedido todo (incluindo golos de jogadores que TÊM perfil
            # completo no mesmo jogo).
            nome_jogador = (i.get("player") or {}).get("name") or i.get("playerName") or "?"
            golos.append({
                "minuto": i.get("time"),
                "minuto_extra": i.get("addedTime"),
                "jogador": nome_jogador,
                "equipa": "casa" if i.get("isHome") else "fora",
                "tipo": i.get("incidentClass"),
            })

    mvp_nome = mvp_rating = mvp_lado = None
    lineups = wreq_fetch(client, f"{BASE_URL}/event/{event_id}/lineups")
    sleep_pedido()
    if lineups:
        for lado in ("home", "away"):
            for p in lineups.get(lado, {}).get("players", []):
                rating = p.get("statistics", {}).get("rating")
                if rating is None:
                    continue
                if mvp_rating is None or rating > mvp_rating:
                    mvp_rating = rating
                    mvp_nome = (p.get("player") or {}).get("name") or p.get("playerName") or "?"
                    mvp_lado = lado

    return golos, mvp_nome, mvp_rating, mvp_lado


def atualizar_resultado(conn, clube_id, data_jogo, golos, mvp_nome, mvp_rating, mvp_equipa):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE resultados_clube
            SET golos = %(golos)s, mvp_nome = %(mvp_nome)s,
                mvp_rating = %(mvp_rating)s, mvp_equipa = %(mvp_equipa)s,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE clube_id = %(clube_id)s AND data_jogo = %(data_jogo)s
              AND competicao = %(competicao)s
            """,
            {
                "golos": json.dumps(golos, ensure_ascii=False) if golos else None,
                "mvp_nome": mvp_nome,
                "mvp_rating": mvp_rating,
                "mvp_equipa": mvp_equipa,
                "clube_id": clube_id,
                "data_jogo": data_jogo,
                "competicao": COMPETICAO_NOME,
            },
        )
        atualizadas = cursor.rowcount
    conn.commit()
    return atualizadas


def main():
    client = Client(emulation=EMULATION)
    conn = psycopg.connect(**ast.DB_CONFIG)
    progresso = carregar_progresso()
    clubes_bd = irc.obter_clubes_bd(conn)

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

                nome_casa = jogo["homeTeam"]["name"]
                nome_fora = jogo["awayTeam"]["name"]
                clube_casa_id = irc.encontrar_clube_bd(nome_casa, clubes_bd)
                clube_fora_id = irc.encontrar_clube_bd(nome_fora, clubes_bd)

                if clube_casa_id is None and clube_fora_id is None:
                    progresso["eventos_concluidos"].append(chave)
                    guardar_progresso(progresso)
                    continue  # nenhum dos dois é um clube nosso

                data_jogo = time.strftime(
                    "%Y-%m-%d", time.gmtime(jogo["startTimestamp"])
                )

                try:
                    golos, mvp_nome, mvp_rating, mvp_lado = obter_golos_e_mvp(client, event_id)
                except Exception as erro:
                    ast.log(f"  [ERRO] {nome_casa} vs {nome_fora} ({data_jogo}): {erro}")
                    continue

                mvp_equipa = None
                if mvp_lado == "home":
                    mvp_equipa = nome_casa
                elif mvp_lado == "away":
                    mvp_equipa = nome_fora

                for clube_id in (clube_casa_id, clube_fora_id):
                    if clube_id is None:
                        continue
                    linhas = atualizar_resultado(
                        conn, clube_id, data_jogo, golos, mvp_nome, mvp_rating, mvp_equipa
                    )
                    if not linhas:
                        ast.log(f"  [sem correspondência na BD] clube_id={clube_id} {data_jogo}")

                ast.log(f"  {nome_casa} vs {nome_fora} ({data_jogo}): {len(golos)} golos, MVP={mvp_nome or '—'}")
                progresso["eventos_concluidos"].append(chave)
                guardar_progresso(progresso)

    finally:
        conn.close()
        ast.log("Concluído.")


if __name__ == "__main__":
    main()
