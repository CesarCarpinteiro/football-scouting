"""
Recolhe o resultado final de TODOS os jogos da época de cada clube
(não só os últimos 5, ao contrário de importar_ultimos_jogos.py) e
guarda em resultados_clube: adversário, casa/fora, resultado (V/E/D),
golos marcados/sofridos, data, quem marcou (minuto+jogador) e o MVP
(jogador com rating mais alto) de cada jogo.

Processado por clube -- um único pedido a /fixtures (sem o parâmetro
"last") dá a época toda de uma vez. Os golos/MVP (via
/fixtures/events e /fixtures/players) são cacheados por fixture_id
dentro da corrida, para não duplicar pedidos quando os dois clubes de
um jogo já estão ambos na nossa BD.

Reutiliza a infraestrutura já validada do api_stats.py (chamar(),
obter_id_liga(), obter_equipas_liga(), normalizar_nome(), LIGAS_ALVO,
EPOCA) -- mesmas ligas, mesma época.

Uso:
    python3 importar_resultados_clube.py
"""

import json
import os
import time

import psycopg

import api_stats as ast

PROGRESSO_FICHEIRO = "progresso_resultados_clube.json"


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


def obter_clubes_bd(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT id, nome FROM clubes WHERE transfermarkt_id IS NOT NULL")
        return cursor.fetchall()


def encontrar_clube_bd(nome_equipa_api, clubes_bd):
    """
    Corresponde o nome de equipa da API-Football a um clube_id da
    nossa BD, com a mesma leniência (substring, sem acentos/maiúsculas/
    hífens/conectores) já usada para jogadores -- clubes como "FC
    Porto" vs "Porto" ou "Paris Saint-Germain" vs "Paris Saint
    Germain" só batem certo assim.
    """
    alvo = ast._limpar_conectores_clube(ast.normalizar_nome(nome_equipa_api))
    alvo = ast.ALIASES_CLUBE_COMPLETO.get(alvo, alvo)

    # Correspondência exata primeiro -- sem isto, "Sporting CP" ficava
    # ambíguo só por também ser substring de "Sporting CP B" (equipa B,
    # clube diferente na nossa BD).
    exatos = [
        (clube_id, nome_bd) for clube_id, nome_bd in clubes_bd
        if ast._limpar_conectores_clube(ast.normalizar_nome(nome_bd)) == alvo
    ]
    if len(exatos) == 1:
        return exatos[0][0]

    # A API-Football às vezes devolve só o nome curto da equipa
    # principal (ex: "Benfica" em vez de "SL Benfica") -- sem excluir
    # as equipas B/reservas aqui, "Benfica" ficava ambíguo com "SL
    # Benfica B" só por ser substring de ambos. Só as deixa entrar se
    # for mesmo uma equipa B que se procura.
    if not alvo.endswith(" b"):
        clubes_bd = [
            (clube_id, nome_bd) for clube_id, nome_bd in clubes_bd
            if not ast._limpar_conectores_clube(ast.normalizar_nome(nome_bd)).endswith(" b")
        ]

    candidatos = []
    for clube_id, nome_bd in clubes_bd:
        nome_limpo = ast._limpar_conectores_clube(ast.normalizar_nome(nome_bd))
        if alvo in nome_limpo or nome_limpo in alvo:
            candidatos.append((clube_id, nome_bd))

    if len(candidatos) == 1:
        return candidatos[0][0]
    if len(candidatos) > 1:
        ast.log(f"  [ambíguo] \"{nome_equipa_api}\" corresponde a {len(candidatos)} "
                f"clubes da BD ({[c[1] for c in candidatos]}) -- a ignorar por segurança.")
    return None


def guardar_resultado(conn, dados):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO resultados_clube (
                clube_id, fixture_id, data_jogo, adversario, em_casa,
                golos_casa, golos_fora, resultado, competicao, epoca,
                golos, mvp_nome, mvp_rating, mvp_equipa, fase
            ) VALUES (%(clube_id)s, %(fixture_id)s, %(data_jogo)s, %(adversario)s, %(em_casa)s,
                      %(golos_casa)s, %(golos_fora)s, %(resultado)s, %(competicao)s, %(epoca)s,
                      %(golos)s, %(mvp_nome)s, %(mvp_rating)s, %(mvp_equipa)s, %(fase)s)
            ON CONFLICT (clube_id, fixture_id) DO UPDATE SET
                data_jogo = EXCLUDED.data_jogo, adversario = EXCLUDED.adversario,
                em_casa = EXCLUDED.em_casa, golos_casa = EXCLUDED.golos_casa,
                golos_fora = EXCLUDED.golos_fora, resultado = EXCLUDED.resultado,
                golos = EXCLUDED.golos, mvp_nome = EXCLUDED.mvp_nome,
                mvp_rating = EXCLUDED.mvp_rating, mvp_equipa = EXCLUDED.mvp_equipa,
                fase = EXCLUDED.fase, atualizado_em = CURRENT_TIMESTAMP
            """,
            dados,
        )
    conn.commit()


# Golos e MVP de um jogo -- cacheados por fixture_id dentro da mesma
# corrida, porque o mesmo jogo é processado duas vezes (uma por cada
# clube envolvido, quando os dois estão na nossa BD), e são sempre os
# mesmos independentemente de qual dos dois clubes está a ser
# processado.
CACHE_DETALHES_JOGO = {}


def obter_detalhes_jogo(fixture_id):
    if fixture_id in CACHE_DETALHES_JOGO:
        return CACHE_DETALHES_JOGO[fixture_id]

    golos = []
    ev = ast.chamar("fixtures/events", {"fixture": fixture_id})
    time.sleep(1.2)
    for e in ev.get("response", []):
        if e.get("type") == "Goal" and e.get("detail") != "Missed Penalty":
            golos.append({
                "minuto": e["time"]["elapsed"],
                "jogador": e["player"]["name"],
                "equipa": e["team"]["name"],
                "tipo": e.get("detail"),
            })

    mvp_nome = mvp_rating = mvp_equipa = None
    fp = ast.chamar("fixtures/players", {"fixture": fixture_id})
    time.sleep(1.2)
    for bloco in fp.get("response", []):
        for p in bloco["players"]:
            rating_bruto = p["statistics"][0]["games"].get("rating")
            if rating_bruto is None:
                continue
            rating = float(rating_bruto)
            if mvp_rating is None or rating > mvp_rating:
                mvp_rating = rating
                mvp_nome = p["player"]["name"]
                mvp_equipa = bloco["team"]["name"]

    detalhes = {
        "golos": golos,
        "mvp_nome": mvp_nome,
        "mvp_rating": mvp_rating,
        "mvp_equipa": mvp_equipa,
    }
    CACHE_DETALHES_JOGO[fixture_id] = detalhes
    return detalhes


def processar_equipa(conn, clube_id, team_id, liga_id, competicao):
    dados = ast.chamar("fixtures", {"team": team_id, "league": liga_id, "season": ast.EPOCA})
    fixtures = dados.get("response", [])
    time.sleep(1.5)

    guardados = 0
    for f in fixtures:
        # só jogos já terminados têm resultado final
        if f["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
            continue

        fixture_id = f["fixture"]["id"]
        data_jogo = f["fixture"]["date"][:10]
        casa = f["teams"]["home"]
        fora = f["teams"]["away"]
        em_casa = casa["id"] == team_id
        adversario = fora["name"] if em_casa else casa["name"]
        golos_casa = f["goals"]["home"]
        golos_fora = f["goals"]["away"]
        resultado = calcular_resultado(em_casa, golos_casa, golos_fora)
        fase = f["league"].get("round")

        detalhes = obter_detalhes_jogo(fixture_id)

        guardar_resultado(conn, {
            "clube_id": clube_id,
            "fixture_id": fixture_id,
            "data_jogo": data_jogo,
            "adversario": adversario,
            "em_casa": em_casa,
            "golos_casa": golos_casa,
            "golos_fora": golos_fora,
            "resultado": resultado,
            "competicao": competicao,
            "epoca": ast.TEMPORADA,
            "golos": json.dumps(detalhes["golos"], ensure_ascii=False),
            "mvp_nome": detalhes["mvp_nome"],
            "mvp_rating": detalhes["mvp_rating"],
            "mvp_equipa": detalhes["mvp_equipa"],
            "fase": fase,
        })
        guardados += 1

    return guardados


def main():
    conn = psycopg.connect(**ast.DB_CONFIG)
    progresso = carregar_progresso()
    clubes_bd = obter_clubes_bd(conn)

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

                clube_id = encontrar_clube_bd(nome_equipa, clubes_bd)
                if clube_id is None:
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa} -- sem correspondência local, a saltar.")
                    marcar_equipa_concluida(progresso, liga_id, team_id)
                    continue

                try:
                    guardados = processar_equipa(conn, clube_id, team_id, liga_id, nome_oficial_liga)
                    ast.log(f"[{indice}/{len(equipas)}] {nome_equipa}: {guardados} jogos guardados.")
                    marcar_equipa_concluida(progresso, liga_id, team_id)
                except Exception as erro:
                    conn.rollback()
                    ast.log(f"  [ERRO] {nome_equipa}: {erro}")

    finally:
        conn.close()
        ast.log("Concluído.")


if __name__ == "__main__":
    main()
