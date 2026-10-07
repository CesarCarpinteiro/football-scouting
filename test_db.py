import psycopg


# ==========================================
# LIGAÇÃO À BASE DE DADOS
# ==========================================

conn = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="football",
    user="scouting",
    password="scouting"
)

cursor = conn.cursor()


# ==========================================
# CONSULTAR JOGADORES
# ==========================================

cursor.execute("""
    SELECT
        id,
        nome,
        numero,
        posicao_principal,
        nacionalidade,
        contrato,
        pe_preferencial
    FROM jogadores
    ORDER BY id
""")

jogadores = cursor.fetchall()


print()
print("===== JOGADORES =====")
print()

for jogador in jogadores:
    print(jogador)


# ==========================================
# CONSULTAR RELAÇÕES JOGADOR-CLUBE
# ==========================================

cursor.execute("""
    SELECT
        jogador_clube.id,
        jogadores.nome,
        jogadores.pe_preferencial,
        clubes.nome,
        jogador_clube.temporada,
        jogador_clube.data_entrada,
        jogador_clube.data_fim
    FROM jogador_clube
    JOIN jogadores
        ON jogador_clube.jogador_id = jogadores.id
    JOIN clubes
        ON jogador_clube.clube_id = clubes.id
    ORDER BY jogador_clube.id
""")

relacoes = cursor.fetchall()


print()
print("===== JOGADORES E CLUBES =====")
print()

for relacao in relacoes:
    print(relacao)


# ==========================================
# CONSULTAR CLUBES
# ==========================================

cursor.execute("""
    SELECT
        id,
        nome,
        nome_curto,
        pais,
        transfermarkt_id
    FROM clubes
    ORDER BY id
""")

clubes = cursor.fetchall()


print()
print("===== CLUBES =====")
print()

for clube in clubes:
    print(clube)


# ==========================================
# FECHAR LIGAÇÃO
# ==========================================

cursor.close()
conn.close()

print()
print("Consulta terminada!")