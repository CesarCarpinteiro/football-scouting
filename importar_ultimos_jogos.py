"""
Recolhe os últimos 5 jogos de cada equipa (por liga/época) e, para
cada jogo, guarda o desempenho de cada jogador nosso que apareceu
nele: minutos, titular/suplente, golos, assistências, rating, e o
resultado da própria equipa nesse jogo (V/E/D).

Processado por CLUBE, não por jogador -- um pedido a /fixtures dá os
últimos 5 jogos da equipa, e um pedido a /fixtures/players por jogo
dá as estatísticas de TODOS os jogadores dessa equipa nesse jogo, o
que evita um pedido por jogador (seriam milhares).

Reutiliza toda a infraestrutura de pedidos/matching já validada do
api_stats.py (chamar(), obter_id_liga(), obter_equipas_liga(),
obter_jogadores_bd(), normalizar_nome(), escolher_candidato()) --
mesmas ligas (LIGAS_ALVO), mesma época, mesmas regras de
correspondência de nomes.

Uso:
    python3 importar_ultimos_jogos.py
"""

import json
import os
import time

import psycopg

import api_stats as ast

PROGRESSO_FICHEIRO = "progresso_ultimos_jogos.json"


def carregar_progresso():
    if not os.path.exists(PROGRESSO_FICHEIRO):
        return {"equipas_concluidas": []}
    with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_progresso(progresso):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, indent=2, ensure_ascii=False)


def equipa_ja_concluida(progresso, liga_id, team_id):
    return f"{liga_id}:{team_id}:{ast.EPOCA}" in progresso["equipas_concluidas"]


def marcar_equipa_concluida(progresso, liga_id, team_id):
    chave = f"{liga_id}:{team_id}:{ast.EPOCA}"
    if chave not in progresso["equipas_concluidas"]:
        progresso["equipas_concluidas"].append(chave)
    guardar_progresso(progresso)


def calcular_resultado(em_casa, golos_casa, golos_fora):
    if golos_casa is None or golos_fora is None:
        return None
    if golos_casa == golos_fora:
        return "E"
    venceu_casa = golos_casa > golos_fora
    return "V" if (em_casa == venceu_casa) else "D"


def guardar_jogo(conn, dados):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ultimos_jogos_jogador (
                jogador_id, fixture_id, data_jogo, adversario, em_casa,
                golos_casa, golos_fora, resultado_equipa, minutos, titular,
                golos, assistencias, rating, epoca, defesas, golos_sofridos, penaltis_defendidos
            ) VALUES (%(jogador_id)s, %(fixture_id)s, %(data_jogo)s, %(adversario)s, %(em_casa)s,
                      %(golos_casa)s, %(golos_fora)s, %(resultado_equipa)s, %(minutos)s, %(titular)s,
                      %(golos)s, %(assistencias)s, %(rating)s, %(epoca)s,
                      %(defesas)s, %(golos_sofridos)s, %(penaltis_defendidos)s)
            ON CONFLICT (jogador_id, fixture_id) DO UPDATE SET
                data_jogo = EXCLUDED.data_jogo, adversario = EXCLUDED.adversario,
                em_casa = EXCLUDED.em_casa, golos_casa = EXCLUDED.golos_casa,
                golos_fora = EXCLUDED.golos_fora, resultado_equipa = EXCLUDED.resultado_equipa,
                minutos = EXCLUDED.minutos, titular = EXCLUDED.titular,
                golos = EXCLUDED.golos, assistencias = EXCLUDED.assistencias,
                rating = EXCLUDED.rating, defesas = EXCLUDED.defesas,
                golos_sofridos = EXCLUDED.golos_sofridos,
                penaltis_defendidos = EXCLUDED.penaltis_defendidos,
                atualizado_em = CURRENT_TIMESTAMP
            """,
            dados,
        )
    conn.commit()


def processar_equipa(conn, jogadores_bd, liga_id, team_id, nome_equipa_api):
    fixtures_data = ast.chamar(
        "fixtures", {"team": team_id, "league": liga_id, "season": ast.EPOCA, "last": 5}
    )
    fixtures = fixtures_data.get("response", [])
    time.sleep(1.5)

    guardados = 0
    for f in fixtures:
        fixture_id = f["fixture"]["id"]
        data_jogo = f["fixture"]["date"][:10]
        casa = f["teams"]["home"]
        fora = f["teams"]["away"]
        em_casa = casa["id"] == team_id
        adversario = fora["name"] if em_casa else casa["name"]
        golos_casa = f["goals"]["home"]
        golos_fora = f["goals"]["away"]
        resultado = calcular_resultado(em_casa, golos_casa, golos_fora)

        fp_data = ast.chamar("fixtures/players", {"fixture": fixture_id})
        time.sleep(1.5)

        for team_block in fp_data.get("response", []):
            if team_block["team"]["id"] != team_id:
                continue

            for p in team_block["players"]:
                nome_normalizado = ast.normalizar_nome(p["player"]["name"])
                candidatos = jogadores_bd.get(nome_normalizado)
                if not candidatos:
                    continue

                jogador_id = ast.escolher_candidato(
                    candidatos, nome_equipa_api, p["player"]["name"]
                )
                if jogador_id is None:
                    continue

                stats = p["statistics"][0]
                jogos_info = stats["games"]
                golos_info = stats["goals"]
                minutos = jogos_info.get("minutes")
                if minutos is None:
                    continue  # não entrou em campo neste jogo

                rating_bruto = jogos_info.get("rating")
                guardar_jogo(conn, {
                    "jogador_id": jogador_id,
                    "fixture_id": fixture_id,
                    "data_jogo": data_jogo,
                    "adversario": adversario,
                    "em_casa": em_casa,
                    "golos_casa": golos_casa,
                    "golos_fora": golos_fora,
                    "resultado_equipa": resultado,
                    "minutos": minutos,
                    "titular": not jogos_info.get("substitute"),
                    "golos": golos_info.get("total") or 0,
                    "assistencias": golos_info.get("assists") or 0,
                    "rating": float(rating_bruto) if rating_bruto else None,
                    "epoca": ast.TEMPORADA,
                    "defesas": golos_info.get("saves"),
                    "golos_sofridos": golos_info.get("conceded"),
                    "penaltis_defendidos": stats.get("penalty", {}).get("saved"),
                })
                guardados += 1

    return guardados


def main():
    conn = psycopg.connect(**ast.DB_CONFIG)
    progresso = carregar_progresso()
    jogadores_bd = ast.obter_jogadores_bd(conn)

    try:
        for pais, nome_liga in ast.LIGAS_ALVO:
            liga_id, nome_oficial_liga = ast.obter_id_liga(pais, nome_liga)
            ast.log(f"=== LIGA: {nome_oficial_liga} (id {liga_id}) ===")

            equipas = ast.obter_equipas_liga(liga_id, ast.EPOCA)
            ast.log(f"Equipas encontradas: {len(equipas)}")

            for indice, equipa in enumerate(equipas, start=1):
                team_id = equipa["id"]
                nome_equipa = equipa["nome"]

                if equipa_ja_concluida(progresso, liga_id, team_id):
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa} -- já processado, a saltar.")
                    continue

                try:
                    guardados = processar_equipa(conn, jogadores_bd, liga_id, team_id, nome_equipa)
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa}: {guardados} registos guardados.")
                    marcar_equipa_concluida(progresso, liga_id, team_id)
                except Exception as erro:
                    conn.rollback()
                    ast.log(f"  [ERRO] {nome_equipa}: {erro}")

    finally:
        conn.close()
        ast.log("Concluído.")


if __name__ == "__main__":
    main()
