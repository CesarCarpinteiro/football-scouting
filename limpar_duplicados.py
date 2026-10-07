
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

try:

    # ==========================================
    # 1. REMOVER RELAÇÕES DUPLICADAS
    # ==========================================

    print("A remover relações duplicadas...")

    cursor.execute("""
        DELETE FROM jogador_clube
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM jogador_clube
            GROUP BY jogador_id, clube_id, temporada
        )
    """)

    print(f"Relações duplicadas removidas: {cursor.rowcount}")


    # ==========================================
    # 2. VERIFICAR SE EXISTEM DUPLICADOS
    # ==========================================

    cursor.execute("""
        SELECT
            jogador_id,
            clube_id,
            temporada,
            COUNT(*) AS total
        FROM jogador_clube
        GROUP BY jogador_id, clube_id, temporada
        HAVING COUNT(*) > 1
    """)

    duplicados = cursor.fetchall()

    if duplicados:
        print("Ainda existem relações duplicadas:")
        for duplicado in duplicados:
            print(duplicado)

        raise Exception(
            "Existem duplicados. A restrição UNIQUE não foi criada."
        )

    print("Não existem relações duplicadas.")


    # ==========================================
    # 3. CRIAR RESTRIÇÃO UNIQUE
    # ==========================================

    print("A verificar a restrição UNIQUE...")

    cursor.execute("""
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'jogador_clube_unico'
    """)

    constraint_existe = cursor.fetchone()

    if not constraint_existe:

        cursor.execute("""
            ALTER TABLE jogador_clube
            ADD CONSTRAINT jogador_clube_unico
            UNIQUE (
                jogador_id,
                clube_id,
                temporada
            )
        """)

        print("Restrição UNIQUE criada com sucesso!")

    else:
        print("A restrição UNIQUE já existe.")


    # ==========================================
    # 4. GUARDAR ALTERAÇÕES
    # ==========================================

    conn.commit()

    print("Alterações guardadas com sucesso!")


except Exception as erro:

    # ==========================================
    # CANCELAR ALTERAÇÕES SE EXISTIR UM ERRO
    # ==========================================

    conn.rollback()

    print("Ocorreu um erro. As alterações foram canceladas.")
    print(f"Erro: {erro}")


finally:

    # ==========================================
    # FECHAR LIGAÇÃO
    # ==========================================

    cursor.close()
    conn.close()

    print("Ligação à base de dados fechada.")