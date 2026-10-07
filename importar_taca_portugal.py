"""
Recolhe os jogos da Taça de Portugal 2024/25 (liga_id=96 na
API-Football, confirmado ao vivo) para os clubes que já temos na BD
(Primeira Liga + Liga Portugal 2) -- clubes de divisões mais baixas
que só a Taça junta não são rastreados, por isso ficam de fora sem
isso ser um erro.

Reutiliza processar_equipa() de importar_resultados_clube.py tal
qual está (já trata golos, MVP e fase/ronda), só muda o liga_id e o
nome da competição.

Uso:
    python3 importar_taca_portugal.py
"""

import json
import os
import time

import psycopg

import api_stats as ast
import importar_resultados_clube as irc

LIGA_ID_TACA = 96  # confirmado ao vivo: "Taça de Portugal"
PROGRESSO_FICHEIRO = "progresso_taca_portugal.json"

LIGAS_ORIGEM = [
    ("Portugal", "Primeira Liga"),
    ("Portugal", "Segunda Liga"),
]


def carregar_progresso():
    if not os.path.exists(PROGRESSO_FICHEIRO):
        return {"equipas_concluidas": []}
    with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_progresso(progresso):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, indent=2, ensure_ascii=False)


def equipa_ja_concluida(progresso, team_id):
    return f"{team_id}:{ast.EPOCA}" in progresso["equipas_concluidas"]


def marcar_equipa_concluida(progresso, team_id):
    chave = f"{team_id}:{ast.EPOCA}"
    if chave not in progresso["equipas_concluidas"]:
        progresso["equipas_concluidas"].append(chave)
    guardar_progresso(progresso)


def main():
    conn = psycopg.connect(**ast.DB_CONFIG)
    progresso = carregar_progresso()
    clubes_bd = irc.obter_clubes_bd(conn)

    try:
        for pais, nome_liga in LIGAS_ORIGEM:
            liga_id_origem, nome_oficial = ast.obter_id_liga(pais, nome_liga)
            ast.log(f"=== Equipas de origem: {nome_oficial} (id {liga_id_origem}) ===")

            equipas = ast.obter_equipas_liga(liga_id_origem, ast.EPOCA)
            ast.log(f"Equipas encontradas: {len(equipas)}")

            for indice, equipa in enumerate(equipas, start=1):
                team_id = equipa["id"]
                nome_equipa = equipa["nome"]

                if equipa_ja_concluida(progresso, team_id):
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa} -- já processado, a saltar.")
                    continue

                clube_id = irc.encontrar_clube_bd(nome_equipa, clubes_bd)
                if clube_id is None:
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa} -- sem correspondência local, a saltar.")
                    marcar_equipa_concluida(progresso, team_id)
                    continue

                try:
                    guardados = irc.processar_equipa(conn, clube_id, team_id, LIGA_ID_TACA, "Taça de Portugal")
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa}: {guardados} jogos da Taça guardados.")
                    marcar_equipa_concluida(progresso, team_id)
                except Exception as erro:
                    conn.rollback()
                    ast.log(f"  [ERRO] {nome_equipa}: {erro}")

    finally:
        conn.close()
        ast.log("Concluído.")


if __name__ == "__main__":
    main()
