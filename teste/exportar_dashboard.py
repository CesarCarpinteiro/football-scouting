import json
import psycopg

conn = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="football",
    user="scouting",
    password="scouting",
)

cursor = conn.cursor()

# descobrir que colunas existem mesmo na tabela jogadores
# (para não rebentar se alguma coluna ainda não tiver sido criada)
cursor.execute("""
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name = 'jogadores'
""")
colunas_existentes = {row[0] for row in cursor.fetchall()}

colunas_desejadas = [
    "id", "nome", "numero", "idade", "posicao_principal",
    "posicoes_secundarias", "pe_preferencial", "altura",
    "nacionalidade", "outras_nacionalidades", "contrato",
    "valor_mercado", "url_transfermarkt",
]

colunas = [c for c in colunas_desejadas if c in colunas_existentes]

cursor.execute(f"SELECT {', '.join(colunas)} FROM jogadores ORDER BY nome")
linhas = cursor.fetchall()

jogadores = []

for linha in linhas:
    jogador = dict(zip(colunas, linha))
    jogador_id = jogador.pop("id", None)

    # ir buscar "no clube desde" à tabela jogador_clube (a entrada mais recente)
    no_clube_desde = None
    if jogador_id is not None:
        cursor.execute("""
            SELECT data_entrada
            FROM jogador_clube
            WHERE jogador_id = %s
            ORDER BY data_entrada DESC NULLS LAST
            LIMIT 1
        """, (jogador_id,))
        resultado = cursor.fetchone()
        if resultado and resultado[0]:
            no_clube_desde = resultado[0].strftime("%d/%m/%Y")

    jogador["no_clube_desde"] = no_clube_desde
    jogador["link_perfil"] = jogador.pop("url_transfermarkt", None)

    if jogador.get("contrato") is not None:
        jogador["contrato"] = jogador["contrato"].strftime("%d/%m/%Y")

    jogadores.append(jogador)

cursor.close()
conn.close()

with open("jogadores_dashboard.json", "w", encoding="utf-8") as f:
    json.dump(jogadores, f, ensure_ascii=False, default=str)

print(f"Exportados {len(jogadores)} jogadores para jogadores_dashboard.json")
