import api_stats as ast
import psycopg

conn = psycopg.connect(**ast.DB_CONFIG)
with conn.cursor() as c:
    c.execute("SELECT id, nome FROM clubes WHERE transfermarkt_id IS NOT NULL ORDER BY nome")
    for row in c.fetchall():
        print(row)
conn.close()