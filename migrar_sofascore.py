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
    ALTER TABLE jogadores
    ADD COLUMN IF NOT EXISTS sofascore_id INTEGER UNIQUE
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS estatisticas_sofascore (
        id SERIAL PRIMARY KEY,
        jogador_id INTEGER NOT NULL,
        sofascore_id INTEGER NOT NULL,
        liga_id INTEGER NOT NULL,
        epoca_id INTEGER NOT NULL,
        epoca_nome VARCHAR(20),
        equipa VARCHAR(150),
        posicao VARCHAR(5),
        jogos INTEGER,
        titularidades INTEGER,
        minutos INTEGER,
        rating NUMERIC(4,2),
        golos INTEGER,
        assistencias INTEGER,
        remates_totais INTEGER,
        remates_a_baliza INTEGER,
        grandes_ocasioes_criadas INTEGER,
        grandes_ocasioes_falhadas INTEGER,
        golos_esperados NUMERIC(6,2),
        assistencias_esperadas NUMERIC(6,2),
        golos_cabeca INTEGER,
        golos_pe_direito INTEGER,
        golos_pe_esquerdo INTEGER,
        golos_penalti INTEGER,
        conversao_remates_pct NUMERIC(5,2),
        passes_totais INTEGER,
        passes_certos INTEGER,
        passes_certos_pct NUMERIC(5,2),
        passes_chave INTEGER,
        dribles_conseguidos INTEGER,
        toques INTEGER,
        duelos_terrestres_ganhos INTEGER,
        duelos_aereos_ganhos INTEGER,
        duelos_totais_ganhos INTEGER,
        desarmes INTEGER,
        intercecoes INTEGER,
        recuperacoes_bola INTEGER,
        cartoes_amarelos INTEGER,
        cartoes_vermelhos INTEGER,
        faltas INTEGER,
        foras_de_jogo INTEGER,
        defesas INTEGER,
        golos_sofridos INTEGER,
        golos_evitados NUMERIC(6,2),
        balizas_a_zero INTEGER,
        penaltis_defendidos INTEGER,
        penaltis_sofridos INTEGER,
        remates_defendidos_dentro_area INTEGER,
        remates_defendidos_fora_area INTEGER,
        socos INTEGER,
        saidas_bem_sucedidas INTEGER,
        saidas_aereas INTEGER,
        pontapes_de_baliza INTEGER,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (jogador_id) REFERENCES jogadores(id) ON DELETE CASCADE,
        UNIQUE (jogador_id, liga_id, epoca_id)
    )
""")

colunas_extra = """
    passes_picados_certos INTEGER,
    passes_picados_totais INTEGER,
    cruzamentos_certos INTEGER,
    cruzamentos_totais INTEGER,
    cruzamentos_certos_pct NUMERIC(5,2),
    passes_terco_final_certos INTEGER,
    bolas_longas_certas INTEGER,
    bolas_longas_totais INTEGER,
    bolas_longas_certas_pct NUMERIC(5,2),
    passes_campo_contrario_certos INTEGER,
    passes_campo_contrario_totais INTEGER,
    passes_proprio_campo_certos INTEGER,
    passes_proprio_campo_totais INTEGER,
    duelos_aereos_ganhos_pct NUMERIC(5,2),
    duelos_aereos_perdidos INTEGER,
    penaltis_batidos_fora INTEGER,
    penaltis_batidos_poste INTEGER,
    penaltis_batidos_a_baliza INTEGER,
    remates_bloqueados INTEGER,
    alivios INTEGER,
    jogos_com_rating INTEGER,
    cruzamentos_nao_agarrados INTEGER,
    cartoes_vermelhos_diretos INTEGER,
    perdas_por_desarme INTEGER,
    driblado_por_adversario INTEGER,
    duelos_perdidos INTEGER,
    erros_geraram_golo INTEGER,
    erros_geraram_remate INTEGER,
    xg_envolvimento NUMERIC(6,2),
    golos_livre_direto INTEGER,
    golos_mais_assistencias INTEGER,
    golos_sofridos_dentro_area INTEGER,
    golos_sofridos_fora_area INTEGER,
    golos_dentro_area INTEGER,
    golos_fora_area INTEGER,
    duelos_terrestres_ganhos_pct NUMERIC(5,2),
    remates_ao_poste INTEGER,
    passes_errados INTEGER,
    autogolos INTEGER,
    passes_pre_assistencia INTEGER,
    penaltis_batidos INTEGER,
    penaltis_cometidos INTEGER,
    penaltis_conversao_pct NUMERIC(5,2),
    penaltis_conquistados INTEGER,
    posse_perdida INTEGER,
    posse_ganha_terco_final INTEGER,
    saidas_totais INTEGER,
    defesas_agarradas INTEGER,
    defesas_desviadas INTEGER,
    frequencia_finalizacao NUMERIC(6,2),
    conversao_bola_parada_pct NUMERIC(5,2),
    remates_bola_parada INTEGER,
    remates_dentro_area INTEGER,
    remates_fora_area INTEGER,
    remates_fora INTEGER,
    dribles_conseguidos_pct NUMERIC(5,2),
    desarmes_ganhos INTEGER,
    desarmes_ganhos_pct NUMERIC(5,2),
    assistencias_tentadas INTEGER,
    dribles_tentados INTEGER,
    duelos_totais_ganhos_pct NUMERIC(5,2),
    rating_total NUMERIC(7,2),
    presencas_equipa_semana INTEGER,
    sofreu_faltas INTEGER,
    segundo_amarelo INTEGER
"""

for linha in colunas_extra.strip().split(",\n"):
    nome_coluna, tipo = linha.strip().split(None, 1)
    cursor.execute(
        f"ALTER TABLE estatisticas_sofascore ADD COLUMN IF NOT EXISTS {nome_coluna} {tipo}"
    )

cursor.execute("""
    CREATE TABLE IF NOT EXISTS heatmap_sofascore (
        id SERIAL PRIMARY KEY,
        jogador_id INTEGER NOT NULL,
        liga_id INTEGER NOT NULL,
        epoca_id INTEGER NOT NULL,
        pontos JSONB NOT NULL,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (jogador_id) REFERENCES jogadores(id) ON DELETE CASCADE,
        UNIQUE (jogador_id, liga_id, epoca_id)
    )
""")

conn.commit()
cursor.close()
conn.close()

print("Migração Sofascore aplicada com sucesso.")
