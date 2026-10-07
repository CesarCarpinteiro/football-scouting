import psycopg


conn = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="football",
    user="scouting",
    password="scouting"
)

cursor = conn.cursor()

cursor.execute("""
    SELECT id, nome
    FROM jogadores
    ORDER BY nome
""")

jogadores = cursor.fetchall()

print("Jogadores encontrados:")
print()

for jogador in jogadores:
    print(f"ID: {jogador[0]} | Nome: {jogador[1]}")

cursor.close()
conn.close()