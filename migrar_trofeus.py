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
    CREATE TABLE IF NOT EXISTS trofeus_clube (
        id SERIAL PRIMARY KEY,
        clube_id INTEGER NOT NULL,
        nome_trofeu VARCHAR(150) NOT NULL,
        quantidade INTEGER,
        epocas TEXT[],
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (clube_id) REFERENCES clubes(id) ON DELETE CASCADE,
        UNIQUE (clube_id, nome_trofeu)
    )
""")

conn.commit()
cursor.close()
conn.close()

print("Migração de troféus aplicada com sucesso.")
