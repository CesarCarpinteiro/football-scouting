"""
Completa os jogos da Taça de Portugal 2024/25 (já importados do
Transfermarkt via importar_taca_portugal_tm.py -- ronda, data,
adversário e resultado) com os marcadores de golos e o MVP, que só a
API-Football dá por um custo razoável (events/players por fixture).

Não mexe em data_jogo/adversario/golos_casa/golos_fora/resultado/fase
-- esses campos continuam a vir do Transfermarkt, que é a fonte
escolhida para a Taça. Este script só faz UPDATE das colunas golos/
mvp_nome/mvp_rating/mvp_equipa, casando por (clube_id, data_jogo) --
os fixture_id não servem para casar porque o Transfermarkt e a
API-Football numeram os jogos de forma completamente diferente.

Reutiliza obter_detalhes_jogo()/encontrar_clube_bd() de
importar_resultados_clube.py (já tratam o cache por fixture e a
correspondência de nome de clube) -- só muda o liga_id (96, "Taça de
Portugal", confirmado ao vivo em importar_taca_portugal.py) e o que
faz com o resultado (UPDATE em vez de INSERT completo).

Uso:
    python3 completar_golos_taca_portugal.py
"""

import json
import time

import psycopg

import api_stats as ast
import importar_resultados_clube as irc

LIGA_ID_TACA = 96
COMPETICAO_NOME = "Taça de Portugal"
LIGAS_ORIGEM = [
    ("Portugal", "Primeira Liga"),
    ("Portugal", "Segunda Liga"),
]


def atualizar_golos_mvp(conn, clube_id, data_jogo, detalhes):
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
                "golos": json.dumps(detalhes["golos"], ensure_ascii=False),
                "mvp_nome": detalhes["mvp_nome"],
                "mvp_rating": detalhes["mvp_rating"],
                "mvp_equipa": detalhes["mvp_equipa"],
                "clube_id": clube_id,
                "data_jogo": data_jogo,
                "competicao": COMPETICAO_NOME,
            },
        )
        atualizadas = cursor.rowcount
    conn.commit()
    return atualizadas


def main():
    conn = psycopg.connect(**ast.DB_CONFIG)
    clubes_bd = irc.obter_clubes_bd(conn)

    total_atualizadas = 0
    total_sem_correspondencia = 0

    try:
        for pais, nome_liga in LIGAS_ORIGEM:
            liga_id_origem, nome_oficial = ast.obter_id_liga(pais, nome_liga)
            ast.log(f"=== Equipas de origem: {nome_oficial} (id {liga_id_origem}) ===")

            equipas = ast.obter_equipas_liga(liga_id_origem, ast.EPOCA)
            ast.log(f"Equipas encontradas: {len(equipas)}")

            for indice, equipa in enumerate(equipas, start=1):
                team_id = equipa["id"]
                nome_equipa = equipa["nome"]

                clube_id = irc.encontrar_clube_bd(nome_equipa, clubes_bd)
                if clube_id is None:
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa} -- sem correspondência local, a saltar.")
                    continue

                try:
                    dados = ast.chamar("fixtures", {"team": team_id, "league": LIGA_ID_TACA, "season": ast.EPOCA})
                    fixtures = dados.get("response", [])
                    time.sleep(1.5)
                except Exception as erro:
                    ast.log(f"  [ERRO] {nome_equipa}: {erro}")
                    continue

                atualizadas_equipa = 0
                for f in fixtures:
                    if f["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
                        continue
                    fixture_id = f["fixture"]["id"]
                    data_jogo = f["fixture"]["date"][:10]

                    try:
                        detalhes = irc.obter_detalhes_jogo(fixture_id)
                    except Exception as erro:
                        ast.log(f"  [ERRO detalhes] {nome_equipa} {data_jogo}: {erro}")
                        continue

                    linhas = atualizar_golos_mvp(conn, clube_id, data_jogo, detalhes)
                    if linhas:
                        atualizadas_equipa += linhas
                    else:
                        total_sem_correspondencia += 1
                        ast.log(f"  [sem correspondência na BD] {nome_equipa} {data_jogo}")

                total_atualizadas += atualizadas_equipa
                ast.log(f"[{indice}/{len(equipas)}] {nome_equipa}: {atualizadas_equipa} jogos completados com golos/MVP.")

    finally:
        conn.close()
        ast.log(f"Concluído: {total_atualizadas} jogos completados, "
                 f"{total_sem_correspondencia} sem correspondência na BD.")


if __name__ == "__main__":
    main()
