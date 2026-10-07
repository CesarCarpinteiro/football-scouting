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
# TABELA: estatisticas_jogador
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS estatisticas_jogador (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        epoca VARCHAR(20) NOT NULL,

        competicao VARCHAR(100) NOT NULL,

        jogos INTEGER DEFAULT 0,

        titularidades INTEGER DEFAULT 0,

        minutos INTEGER DEFAULT 0,

        golos INTEGER DEFAULT 0,

        assistencias INTEGER DEFAULT 0,

        classificacao_media NUMERIC(4,2),

        -- posição (bruta, tal como vem da API-Football) e
        -- equipa nessa época, úteis para agrupar por perfil
        -- posicional e para mostrar sem precisar de outro JOIN
        posicao_api VARCHAR(50),

        equipa VARCHAR(100),

        -- estatísticas detalhadas (remates, passes, duelos,
        -- desarmes, dribles, grande penalidades) usadas no
        -- terminal de scouting (score + radar por posição)
        remates_totais INTEGER,

        remates_a_baliza INTEGER,

        golos_sofridos INTEGER,

        defesas INTEGER,

        passes_totais INTEGER,

        passes_chave INTEGER,

        passes_certos_pct NUMERIC(5,2),

        duelos_totais INTEGER,

        duelos_ganhos INTEGER,

        desarmes INTEGER,

        bloqueios INTEGER,

        intercecoes INTEGER,

        dribles_tentados INTEGER,

        dribles_conseguidos INTEGER,

        penaltis_marcados INTEGER,

        penaltis_falhados INTEGER,

        penaltis_defendidos INTEGER,

        data_recolha DATE DEFAULT CURRENT_DATE,

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        UNIQUE (
            jogador_id,
            epoca,
            competicao
        )

    )
""")


# ==========================================
# GUARDAR ALTERAÇÕES
# ==========================================

conn.commit()


# ==========================================
# FECHAR LIGAÇÃO
# ==========================================

cursor.close()
conn.close()

print("Tabela de estatísticas criada com sucesso!")