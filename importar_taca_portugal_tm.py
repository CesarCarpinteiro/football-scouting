"""
Recolhe a Taça de Portugal 2024/25 diretamente do Transfermarkt, em vez
da API-Football (ver importar_taca_portugal.py, a versão antiga).

O calendário da prova é renderizado em JS na página normal
(startseite/pokalwettbewerb/POPO), mas essa página chama por baixo um
endpoint JSON próprio -- confirmado ao vivo via Playwright a observar
os pedidos de rede:

    https://tmapi.transfermarkt.technology/competition/POPO/game-schedule?season=2024

Devolve os 148 jogos da época toda numa única chamada, já com os IDs
de clube do Transfermarkt -- o que já temos guardado em
clubes.transfermarkt_id, por isso a correspondência é por ID exato,
não por nome (sem a fragilidade de normalizar/traduzir nomes que a
versão API-Football precisa). Os adversários que não são nenhum dos
nossos clubes (equipas de divisões inferiores que só a Taça junta)
ficam só com o nome, tal como já acontecia nos resultados de liga.

NOTA: esta versão não traz os marcadores dos golos -- isso está na
página de resumo de cada jogo (spielbericht), mas exigiria mais ~148
pedidos extra (um por jogo) e o minuto vem codificado num sprite CSS
sem equivalente em texto simples. golos/mvp_* ficam a NULL aqui; isto
é um "fica para depois" assumido, não um esquecimento.

Substitui por completo os dados antigos da Taça (fonte API-Football):
apaga as linhas existentes com competicao='Taça de Portugal' antes de
inserir as novas, para não ficar com o mesmo jogo duplicado sob dois
fixture_id diferentes.

Uso:
    python3 importar_taca_portugal_tm.py
"""

import time

import psycopg
import requests

import api_stats as ast
import importar_resultados_clube as irc

COMPETICAO_ID = "POPO"
SEASON = 2024
COMPETICAO_NOME = "Taça de Portugal"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.transfermarkt.pt/",
}


def obter_calendario():
    r = requests.get(
        f"https://tmapi.transfermarkt.technology/competition/{COMPETICAO_ID}/game-schedule",
        params={"season": SEASON}, headers=HEADERS, timeout=20,
    )
    r.raise_for_status()
    dados = r.json()
    jogos = []
    for dia in dados["data"]["gameSchedule"]:
        jogos.extend(dia["games"])
    return jogos


def obter_nomes_clubes(ids):
    """
    Resolve nome de clube a partir do ID do Transfermarkt, em lotes de
    100 (limite generoso, a prova inteira tem menos de 200 clubes
    distintos) -- cobre tanto os nossos clubes como os adversários de
    divisões inferiores que não rastreamos.
    """
    nomes = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 100):
        lote = ids[i:i + 100]
        params = [("ids[]", cid) for cid in lote]
        r = requests.get(
            "https://tmapi.transfermarkt.technology/clubs",
            params=params, headers=HEADERS, timeout=20,
        )
        r.raise_for_status()
        for c in r.json()["data"]:
            nomes[str(c["id"])] = c["name"]
        time.sleep(1)
    return nomes


def apagar_dados_antigos(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM resultados_clube WHERE competicao = %s", (COMPETICAO_NOME,)
        )
        apagados = cursor.rowcount
    conn.commit()
    ast.log(f"Apagadas {apagados} linhas antigas (fonte API-Football) de '{COMPETICAO_NOME}'.")


def main():
    conn = psycopg.connect(**ast.DB_CONFIG)
    try:
        clubes_bd = irc.obter_clubes_bd(conn)
        tm_id_para_clube_id = {}
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, transfermarkt_id FROM clubes WHERE transfermarkt_id IS NOT NULL")
            for clube_id, tm_id in cursor.fetchall():
                tm_id_para_clube_id[str(tm_id)] = clube_id

        ast.log("A obter o calendário da Taça de Portugal 2024/25 do Transfermarkt...")
        jogos = obter_calendario()
        ast.log(f"Jogos encontrados: {len(jogos)}")

        ids_envolvidos = set()
        for j in jogos:
            ids_envolvidos.add(j["homeClub"]["clubId"])
            ids_envolvidos.add(j["awayClub"]["clubId"])
        ast.log(f"A resolver nomes de {len(ids_envolvidos)} clubes...")
        nomes_clubes = obter_nomes_clubes(ids_envolvidos)

        apagar_dados_antigos(conn)

        guardados = 0
        ignorados_por_estado = 0
        for j in jogos:
            if not j.get("isFinished"):
                ignorados_por_estado += 1
                continue

            fixture_id = int(j["gameId"])
            data_jogo = j["baseDetails"]["date"]["dateTimeUTC"][:10]
            fase = j["baseDetails"]["competitionGroup"]["name"]
            tm_casa = j["homeClub"]["clubId"]
            tm_fora = j["awayClub"]["clubId"]
            golos_casa = j["score"]["home"]
            golos_fora = j["score"]["away"]

            for tm_id, em_casa, tm_adversario in (
                (tm_casa, True, tm_fora),
                (tm_fora, False, tm_casa),
            ):
                clube_id = tm_id_para_clube_id.get(tm_id)
                if clube_id is None:
                    continue  # clube de divisão inferior, não rastreado

                resultado = irc.calcular_resultado(em_casa, golos_casa, golos_fora)
                irc.guardar_resultado(conn, {
                    "clube_id": clube_id,
                    "fixture_id": fixture_id,
                    "data_jogo": data_jogo,
                    "adversario": nomes_clubes.get(tm_adversario, "?"),
                    "em_casa": em_casa,
                    "golos_casa": golos_casa,
                    "golos_fora": golos_fora,
                    "resultado": resultado,
                    "competicao": COMPETICAO_NOME,
                    "epoca": ast.TEMPORADA,
                    "golos": None,
                    "mvp_nome": None,
                    "mvp_rating": None,
                    "mvp_equipa": None,
                    "fase": fase,
                })
                guardados += 1

        ast.log(f"Concluído: {guardados} linhas guardadas "
                 f"({ignorados_por_estado} jogos ainda por realizar/ignorados).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
