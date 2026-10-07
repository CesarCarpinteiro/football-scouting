"""
Preenche as colunas detalhadas (passes, duelos, remates, etc.) de
estatisticas_jogador para registos que já existem, usando um ficheiro
JSON já recolhido anteriormente (sem gastar pedidos à API-Football).

Uso:
    python3 backfill_estatisticas_completas.py estatisticas_fc_porto_completo.json
"""

import sys
import json
import unicodedata
import psycopg

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "football",
    "user": "scouting",
    "password": "scouting",
}


def normalizar_nome(nome):
    if not nome:
        return ""
    nome = str(nome).strip().lower()
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    return " ".join(nome.split())


def nomes_correspondem(nome_api, nome_bd):
    """Ver api_stats.py para a explicação completa dos 3 casos cobertos."""
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


def encontrar_jogador_bd(nome_stats, nome_completo_stats, jogadores_bd):
    nomes_normalizados = [
        normalizar_nome(n) for n in (nome_stats, nome_completo_stats) if n
    ]

    for nome in nomes_normalizados:
        if nome in jogadores_bd:
            return jogadores_bd[nome]

    for nome_api in nomes_normalizados:
        for nome_bd, jogador_id in jogadores_bd.items():
            if nomes_correspondem(nome_api, nome_bd):
                print(f"[aviso] Correspondência parcial: '{nome_api}' -> '{nome_bd}'")
                return jogador_id

    return None


def temporada_de_epoca_liga(epoca_liga):
    if epoca_liga is None:
        return None
    return f"{epoca_liga}/{str(int(epoca_liga) + 1)[-2:]}"


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python3 backfill_estatisticas_completas.py <ficheiro.json>")

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        registos = json.load(f)

    conn = psycopg.connect(**DB_CONFIG)

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, nome FROM jogadores")
            jogadores_bd = {
                normalizar_nome(nome): jogador_id
                for jogador_id, nome in cursor.fetchall()
            }

        atualizados = 0
        nao_encontrados = 0

        for stats in registos:
            jogador_id = encontrar_jogador_bd(
                stats.get("nome"), stats.get("nome_completo"), jogadores_bd
            )

            if jogador_id is None:
                nao_encontrados += 1
                print(f"[aviso] Não encontrado na BD: {stats.get('nome')}")
                continue

            epoca = temporada_de_epoca_liga(stats.get("epoca_liga"))
            competicao = stats.get("liga")

            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE estatisticas_jogador SET
                        posicao_api = %s,
                        equipa = %s,
                        remates_totais = %s,
                        remates_a_baliza = %s,
                        golos_sofridos = %s,
                        defesas = %s,
                        passes_totais = %s,
                        passes_chave = %s,
                        passes_certos_pct = %s,
                        duelos_totais = %s,
                        duelos_ganhos = %s,
                        desarmes = %s,
                        bloqueios = %s,
                        intercecoes = %s,
                        dribles_tentados = %s,
                        dribles_conseguidos = %s,
                        penaltis_marcados = %s,
                        penaltis_falhados = %s,
                        penaltis_defendidos = %s
                    WHERE jogador_id = %s AND epoca = %s AND competicao = %s
                    """,
                    (
                        stats.get("posicao"),
                        stats.get("equipa"),
                        stats.get("remates_totais"),
                        stats.get("remates_a_baliza"),
                        stats.get("golos_sofridos"),
                        stats.get("defesas"),
                        stats.get("passes_totais"),
                        stats.get("passes_chave"),
                        stats.get("passes_certos_pct"),
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
                        jogador_id,
                        epoca,
                        competicao,
                    ),
                )

                if cursor.rowcount:
                    atualizados += 1
                    print(f"[OK] {stats.get('nome')}")
                else:
                    print(
                        f"[aviso] Sem linha correspondente em "
                        f"estatisticas_jogador para {stats.get('nome')} "
                        f"({epoca} / {competicao})"
                    )

        conn.commit()

        print(f"\nAtualizados: {atualizados}")
        print(f"Não encontrados na BD: {nao_encontrados}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
