"""
Completa cartões amarelos/vermelhos por jogador na Taça de Portugal
2024/25, a partir da API-Football -- a Sofascore não tem isto ao
nível de jogo (confirmado ao vivo: a chave "yellowCards"/"redCards"
simplesmente não aparece nas statistics por-jogo, só no agregado de
época que sabemos que não existe para esta prova). A API-Football tem
(confirmado ao vivo na Final: 6 jogadores amarelados).

MAPEAMENTO JOGADOR API-FOOTBALL -> JOGADOR LOCAL: ao contrário do golos/
MVP (completar_golos_taca_portugal.py, que só precisa do clube_id), aqui
precisamos do JOGADOR exato -- a API-Football não partilha nenhum ID em
comum com a Sofascore, por isso o matching é por NOME, reutilizando
nomes_correspondem_forte() de importar_sofascore.py (já testado). Para
reduzir o risco de falsas correspondências, o conjunto de candidatos por
jogo é restrito aos jogadores do CLUBE em questão (resolvido primeiro
por encontrar_clube_bd(), tal como nos outros scripts da Taça) -- nunca
compara contra a BD toda.

ONDE GUARDA: em vez de inserir linhas novas, faz MERGE (operador jsonb
||) dos cartões para dentro da linha já existente em
estatisticas_sofascore_jogo, casada por (jogador_id, data_jogo) -- essa
linha já existe de importar_sofascore_taca_portugal_jogadores.py para
quem teve estatísticas Sofascore nesse jogo. Jogadores sem essa linha
(ex: jogos onde a Sofascore não preencheu "statistics") ficam sem
cartões guardados -- não há onde os pôr sem inventar uma linha nova a
partir de uma fonte diferente.

Corre agregar_estatisticas_taca.sql DEPOIS disto, para os cartões
chegarem a estatisticas_sofascore (onde o perfil os lê).

Uso:
    python3 completar_cartoes_taca_portugal.py
"""

import time

import psycopg

import api_stats as ast
import importar_resultados_clube as irc
import importar_sofascore as isofa

LIGA_ID_TACA = 96  # API-Football, confirmado em importar_taca_portugal.py
LIGAS_ORIGEM = [
    ("Portugal", "Primeira Liga"),
    ("Portugal", "Segunda Liga"),
]


def obter_jogadores_clube(conn, clube_id, nome_clube):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT j.id, j.nome FROM jogadores j
            JOIN LATERAL (
                SELECT clube_id FROM jogador_clube
                WHERE jogador_id = j.id
                ORDER BY data_entrada DESC NULLS LAST LIMIT 1
            ) jc ON true
            WHERE jc.clube_id = %s
            """,
            (clube_id,),
        )
        # "clube" vai preenchido com o nome do clube que já sabemos ser o
        # certo (o conjunto já está filtrado a este clube) -- é o que
        # permite a encontrar_jogador_local() aceitar um candidato único
        # só por apelido+inicial (match "fraco") sem o rejeitar por
        # "sem clube para confirmar".
        return [{"id": linha[0], "nome": linha[1], "clube": nome_clube} for linha in cursor.fetchall()]


def mesclar_cartoes(conn, jogador_id, data_jogo, amarelos, vermelhos):
    import json
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE estatisticas_sofascore_jogo
            SET stats_jogo = stats_jogo || %(extra)s::jsonb, atualizado_em = CURRENT_TIMESTAMP
            WHERE jogador_id = %(jogador_id)s AND liga_id = 336 AND data_jogo = %(data_jogo)s
            """,
            {
                "extra": json.dumps({"cartoes_amarelos": amarelos, "cartoes_vermelhos": vermelhos}),
                "jogador_id": jogador_id,
                "data_jogo": data_jogo,
            },
        )
        atualizadas = cursor.rowcount
    conn.commit()
    return atualizadas


def main():
    conn = psycopg.connect(**ast.DB_CONFIG)
    clubes_bd = irc.obter_clubes_bd(conn)

    total_atualizados = 0
    total_sem_linha = 0

    try:
        for pais, nome_liga in LIGAS_ORIGEM:
            liga_id_origem, nome_oficial = ast.obter_id_liga(pais, nome_liga)
            ast.log(f"=== Equipas de origem: {nome_oficial} (id {liga_id_origem}) ===")

            equipas = ast.obter_equipas_liga(liga_id_origem, ast.EPOCA)

            for indice, equipa in enumerate(equipas, start=1):
                team_id = equipa["id"]
                nome_equipa = equipa["nome"]

                clube_id = irc.encontrar_clube_bd(nome_equipa, clubes_bd)
                if clube_id is None:
                    continue

                jogadores_clube = obter_jogadores_clube(conn, clube_id, nome_equipa)
                if not jogadores_clube:
                    continue

                try:
                    dados = ast.chamar("fixtures", {"team": team_id, "league": LIGA_ID_TACA, "season": ast.EPOCA})
                    fixtures = dados.get("response", [])
                    time.sleep(1.2)
                except Exception as erro:
                    ast.log(f"  [ERRO fixtures] {nome_equipa}: {erro}")
                    continue

                for f in fixtures:
                    if f["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
                        continue
                    fixture_id = f["fixture"]["id"]
                    data_jogo = f["fixture"]["date"][:10]

                    try:
                        dados_jogadores = ast.chamar("fixtures/players", {"fixture": fixture_id})
                        time.sleep(1.2)
                    except Exception as erro:
                        ast.log(f"  [ERRO players] {nome_equipa} {data_jogo}: {erro}")
                        continue

                    bloco_equipa = next(
                        (b for b in dados_jogadores.get("response", [])
                         if ast.clubes_coincidem(b["team"]["name"], [nome_equipa])),
                        None,
                    )
                    if bloco_equipa is None:
                        continue

                    for p in bloco_equipa["players"]:
                        stats = p["statistics"][0]
                        cartoes = stats.get("cards", {})
                        amarelos = cartoes.get("yellow") or 0
                        vermelhos = cartoes.get("red") or 0
                        # Mescla SEMPRE (mesmo 0/0) -- se só gravássemos
                        # quando havia cartão, um jogador sem nenhum em
                        # todos os jogos nunca teria a chave presente, e
                        # SUM() na agregação dava NULL em vez de 0 (igual
                        # ao problema que já resolvemos para os outros
                        # campos, mas ao contrário: aqui a ausência da
                        # chave não é garantia de "zero", é garantia de
                        # "não processado").

                        candidato = isofa.encontrar_jogador_local(p["player"]["name"], nome_equipa, jogadores_clube)
                        if candidato is None:
                            ast.log(f"  [sem correspondência] {nome_equipa}: {p['player']['name']} ({data_jogo})")
                            continue

                        linhas = mesclar_cartoes(conn, candidato["id"], data_jogo, amarelos, vermelhos)
                        if linhas:
                            total_atualizados += 1
                            if amarelos or vermelhos:
                                ast.log(f"  {candidato['nome']} ({nome_equipa}, {data_jogo}): "
                                         f"{amarelos} amarelo(s), {vermelhos} vermelho(s)")
                        else:
                            total_sem_linha += 1
                            ast.log(f"  [sem linha estatisticas_sofascore_jogo] {candidato['nome']} ({data_jogo})")

                ast.log(f"[{indice}/{len(equipas)}] {nome_equipa} concluído.")

    finally:
        conn.close()
        ast.log(f"Concluído: {total_atualizados} atualizados, {total_sem_linha} sem linha para mesclar.")


if __name__ == "__main__":
    main()
