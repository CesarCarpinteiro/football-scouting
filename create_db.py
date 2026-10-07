
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
# TABELA: clubes
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS clubes (

        id SERIAL PRIMARY KEY,

        nome VARCHAR(100) NOT NULL,

        nome_curto VARCHAR(50),

        pais VARCHAR(50),

        transfermarkt_id INTEGER UNIQUE,

        url_transfermarkt VARCHAR(255)

    )
""")


# ==========================================
# TABELA: jogadores
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS jogadores (

        id SERIAL PRIMARY KEY,

        nome VARCHAR(100) NOT NULL,

        numero INTEGER,

        posicao_principal VARCHAR(50),

        posicoes_secundarias TEXT[],

        pe_preferencial VARCHAR(20),

        altura INTEGER,

        data_nascimento DATE,

        nacionalidade VARCHAR(100),

        outras_nacionalidades TEXT[],

        contrato DATE,

        valor_mercado BIGINT,

        transfermarkt_id INTEGER UNIQUE,

        url_transfermarkt VARCHAR(255)

    )
""")


# ==========================================
# TABELA: jogador_clube
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS jogador_clube (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        clube_id INTEGER NOT NULL,

        temporada VARCHAR(20) NOT NULL,

        data_entrada DATE,

        data_fim DATE,

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        FOREIGN KEY (clube_id)
            REFERENCES clubes(id)
            ON DELETE CASCADE,

        UNIQUE (jogador_id, clube_id, temporada)

    )
""")


# ==========================================
# TABELA: disciplina_jogador
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS disciplina_jogador (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        epoca VARCHAR(20) NOT NULL,

        competicao VARCHAR(100) NOT NULL,

        cartoes_amarelos INTEGER DEFAULT 0,

        cartoes_vermelhos INTEGER DEFAULT 0,

        expulsoes_segundo_amarelo INTEGER DEFAULT 0,

        faltas_cometidas INTEGER DEFAULT 0,

        faltas_sofridas INTEGER DEFAULT 0,

        suspensoes INTEGER DEFAULT 0,

        jogos_falhados_castigo INTEGER DEFAULT 0,

        data_recolha DATE DEFAULT CURRENT_DATE,

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        UNIQUE (jogador_id, epoca, competicao)

    )
""")


# ==========================================
# TABELA: lesoes_jogador
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS lesoes_jogador (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        tipo_lesao VARCHAR(150),

        parte_corpo VARCHAR(100),

        data_inicio DATE,

        data_fim DATE,

        dias_indisponivel INTEGER,

        jogos_falhados INTEGER DEFAULT 0,

        estado VARCHAR(50),

        epoca VARCHAR(20),

        competicao VARCHAR(100),

        data_recolha DATE DEFAULT CURRENT_DATE,

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        UNIQUE (jogador_id, tipo_lesao, data_inicio)

    )
""")


# ==========================================
# TABELA: transferencias_jogador
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS transferencias_jogador (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        clube_origem VARCHAR(100),

        clube_destino VARCHAR(100),

        data_transferencia DATE,

        tipo VARCHAR(50),

        valor VARCHAR(100),

        epoca VARCHAR(20),

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        UNIQUE (jogador_id, data_transferencia, clube_destino)

    )
""")


# ==========================================
# TABELA: valores_mercado
# (histórico de valor de mercado ao longo do
# tempo, extraído do gráfico do Transfermarkt)
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS valores_mercado (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        data DATE NOT NULL,

        valor BIGINT,

        clube VARCHAR(100),

        idade INTEGER,

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        UNIQUE (jogador_id, data)

    )
""")


# ==========================================
# TABELA: estatisticas_jogo
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS estatisticas_jogo (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        adversario VARCHAR(100),

        data_jogo DATE,

        competicao VARCHAR(100),

        epoca VARCHAR(20),

        titular BOOLEAN DEFAULT FALSE,

        minutos INTEGER DEFAULT 0,

        golos INTEGER DEFAULT 0,

        assistencias INTEGER DEFAULT 0,

        cartao_amarelo BOOLEAN DEFAULT FALSE,

        cartao_vermelho BOOLEAN DEFAULT FALSE,

        classificacao_media NUMERIC(4,2),

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE

    )
""")


# ==========================================
# TABELA: classificacao_liga
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS classificacao_liga (

        id SERIAL PRIMARY KEY,

        liga VARCHAR(50) NOT NULL,

        epoca VARCHAR(20) NOT NULL,

        posicao INTEGER NOT NULL,

        clube_id INTEGER NOT NULL,

        jogos INTEGER,

        vitorias INTEGER,

        empates INTEGER,

        derrotas INTEGER,

        golos_marcados INTEGER,

        golos_sofridos INTEGER,

        pontos INTEGER,

        data_recolha DATE DEFAULT CURRENT_DATE,

        FOREIGN KEY (clube_id)
            REFERENCES clubes(id)
            ON DELETE CASCADE,

        UNIQUE (liga, epoca, clube_id)

    )
""")


# ==========================================
# TABELA: salarios_jogadores
# (histórico de salários por época, extraído
# do salarysport.com -- ver
# importar_salarios_salarysports.py)
# ==========================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS salarios_jogadores (

        id SERIAL PRIMARY KEY,

        jogador_id INTEGER NOT NULL,

        ano INTEGER NOT NULL,

        temporada VARCHAR(20),

        clube_nome VARCHAR(150),

        liga VARCHAR(100),

        salario_semanal NUMERIC(12,2),

        salario_anual NUMERIC(14,2),

        moeda VARCHAR(10),

        contrato_ate DATE,

        fonte VARCHAR(50) DEFAULT 'salarysport',

        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (jogador_id)
            REFERENCES jogadores(id)
            ON DELETE CASCADE,

        UNIQUE (jogador_id, ano)

    )
""")

cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_salarios_jogadores_jogador
        ON salarios_jogadores (jogador_id)
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


print("Base de dados criada/atualizada com sucesso!")