-- Agrega estatisticas_sofascore_jogo (por-jogo) em estatisticas_sofascore
-- (por-época) para a Taça de Portugal -- a mesma tabela que já usas para
-- as ligas, por isso o perfil do jogador passa a mostrar a Taça assim que
-- o endpoint /api/jogador/{id}/sofascore?liga_id=336 encontrar a linha.
--
-- Corre isto DEPOIS de importar_sofascore_taca_portugal_jogadores.py ter
-- preenchido estatisticas_sofascore_jogo. Podes correr várias vezes --
-- o ON CONFLICT substitui sempre pelo valor agregado mais recente.
--
-- NOTA sobre percentagens: nunca fazemos AVG() de uma percentagem
-- por-jogo (a média de "80%, 60%, 100%" não é a percentagem real da
-- época). Em vez disso somamos os valores brutos (ex: SUM(passes_certos)
-- / SUM(passes_totais)) -- é o que a própria Sofascore faz para calcular
-- a percentagem de época nas ligas.
--
-- duelos_terrestres_ganhos: a Sofascore não tem este campo por-jogo (só
-- dá o total de duelos ganhos + os aéreos à parte) -- por isso é
-- derivado aqui como duelos_totais_ganhos - duelos_aereos_ganhos, nunca
-- somado diretamente de um campo que não existe.
--
-- cartoes_amarelos/vermelhos vêm de uma fonte diferente (API-Football,
-- via completar_cartoes_taca_portugal.py) -- a Sofascore não os dá por
-- jogo (confirmado ao vivo), só no agregado de época que sabemos que
-- não está disponível para esta prova. golos_esperados fica mesmo de
-- fora, não há fonte nenhuma com isso por jogo para esta prova.
--
-- COALESCE(..., 0): a Sofascore só inclui uma chave nas statistics
-- por-jogo quando o valor é maior que zero (confirmado ao vivo,
-- comparando vários jogadores/jogos) -- por isso SUM() de uma coluna
-- sempre ausente dá NULL, não 0, mesmo sabendo que o valor real foi
-- zero em todos os jogos. Aplica-se só às contagens brutas -- as
-- percentagens (passes_certos_pct, etc.) ficam de fora disto porque
-- "0%" e "nunca tentou" são coisas diferentes, não dá para assumir.

INSERT INTO estatisticas_sofascore (
    jogador_id, sofascore_id, liga_id, epoca_id, epoca_nome,
    jogos, titularidades, minutos, rating, rating_total, jogos_com_rating,
    golos, assistencias, golos_cabeca, golos_pe_direito, golos_pe_esquerdo, golos_penalti,
    golos_mais_assistencias,
    remates_totais, remates_a_baliza, remates_bloqueados, remates_ao_poste, remates_fora,
    conversao_remates_pct,
    grandes_ocasioes_criadas, grandes_ocasioes_falhadas, erros_geraram_remate,
    passes_totais, passes_certos, passes_errados, passes_certos_pct, passes_chave,
    passes_campo_contrario_certos, passes_campo_contrario_totais,
    passes_proprio_campo_certos, passes_proprio_campo_totais,
    cruzamentos_certos, cruzamentos_totais, cruzamentos_certos_pct,
    bolas_longas_certas, bolas_longas_totais, bolas_longas_certas_pct,
    dribles_tentados, dribles_conseguidos, dribles_conseguidos_pct,
    toques, foras_de_jogo, recuperacoes_bola, alivios,
    duelos_totais_ganhos, duelos_aereos_ganhos, duelos_aereos_perdidos, duelos_terrestres_ganhos, duelos_perdidos,
    duelos_totais_ganhos_pct, duelos_aereos_ganhos_pct, duelos_terrestres_ganhos_pct,
    desarmes, desarmes_ganhos, desarmes_ganhos_pct, intercecoes,
    faltas, sofreu_faltas, posse_perdida,
    penaltis_cometidos, penaltis_conquistados,
    defesas, remates_defendidos_dentro_area, penaltis_sofridos,
    saidas_aereas, saidas_totais, saidas_bem_sucedidas,
    cartoes_amarelos, cartoes_vermelhos
)
SELECT
    jogador_id,
    MAX(sofascore_id) AS sofascore_id,
    liga_id,
    epoca_id,
    '24/25' AS epoca_nome,

    COUNT(*) AS jogos,
    COUNT(*) FILTER (WHERE titular) AS titularidades,
    SUM(minutos) AS minutos,

    ROUND(AVG(rating), 2) AS rating,
    SUM(rating) AS rating_total,
    COUNT(*) FILTER (WHERE rating IS NOT NULL) AS jogos_com_rating,

    COALESCE(SUM((stats_jogo->>'golos')::int), 0) AS golos,
    COALESCE(SUM((stats_jogo->>'assistencias')::int), 0) AS assistencias,
    COALESCE(SUM((stats_jogo->>'golos_cabeca')::int), 0) AS golos_cabeca,
    COALESCE(SUM((stats_jogo->>'golos_pe_direito')::int), 0) AS golos_pe_direito,
    COALESCE(SUM((stats_jogo->>'golos_pe_esquerdo')::int), 0) AS golos_pe_esquerdo,
    COALESCE(SUM((stats_jogo->>'golos_penalti')::int), 0) AS golos_penalti,
    -- derivado: soma simples de dois campos que já temos
    COALESCE(SUM((stats_jogo->>'golos')::int), 0) + COALESCE(SUM((stats_jogo->>'assistencias')::int), 0) AS golos_mais_assistencias,

    COALESCE(SUM((stats_jogo->>'remates_totais')::int), 0) AS remates_totais,
    COALESCE(SUM((stats_jogo->>'remates_a_baliza')::int), 0) AS remates_a_baliza,
    COALESCE(SUM((stats_jogo->>'remates_bloqueados')::int), 0) AS remates_bloqueados,
    COALESCE(SUM((stats_jogo->>'remates_ao_poste')::int), 0) AS remates_ao_poste,
    COALESCE(SUM((stats_jogo->>'remates_fora')::int), 0) AS remates_fora,
    -- derivado: golos / remates totais
    CASE WHEN SUM((stats_jogo->>'remates_totais')::int) > 0
         THEN ROUND(100.0 * COALESCE(SUM((stats_jogo->>'golos')::int), 0) / SUM((stats_jogo->>'remates_totais')::int), 1)
         ELSE NULL END AS conversao_remates_pct,
    COALESCE(SUM((stats_jogo->>'grandes_ocasioes_criadas')::int), 0) AS grandes_ocasioes_criadas,
    COALESCE(SUM((stats_jogo->>'grandes_ocasioes_falhadas')::int), 0) AS grandes_ocasioes_falhadas,
    COALESCE(SUM((stats_jogo->>'erros_geraram_remate')::int), 0) AS erros_geraram_remate,

    COALESCE(SUM((stats_jogo->>'passes_totais')::int), 0) AS passes_totais,
    COALESCE(SUM((stats_jogo->>'passes_certos')::int), 0) AS passes_certos,
    -- derivado: passes totais - passes certos
    COALESCE(SUM((stats_jogo->>'passes_totais')::int), 0) - COALESCE(SUM((stats_jogo->>'passes_certos')::int), 0) AS passes_errados,
    CASE WHEN SUM((stats_jogo->>'passes_totais')::int) > 0
         THEN ROUND(100.0 * SUM((stats_jogo->>'passes_certos')::int) / SUM((stats_jogo->>'passes_totais')::int), 1)
         ELSE NULL END AS passes_certos_pct,
    COALESCE(SUM((stats_jogo->>'passes_chave')::int), 0) AS passes_chave,
    COALESCE(SUM((stats_jogo->>'passes_campo_contrario_certos')::int), 0) AS passes_campo_contrario_certos,
    COALESCE(SUM((stats_jogo->>'passes_campo_contrario_totais')::int), 0) AS passes_campo_contrario_totais,
    COALESCE(SUM((stats_jogo->>'passes_proprio_campo_certos')::int), 0) AS passes_proprio_campo_certos,
    COALESCE(SUM((stats_jogo->>'passes_proprio_campo_totais')::int), 0) AS passes_proprio_campo_totais,

    COALESCE(SUM((stats_jogo->>'cruzamentos_certos')::int), 0) AS cruzamentos_certos,
    COALESCE(SUM((stats_jogo->>'cruzamentos_totais')::int), 0) AS cruzamentos_totais,
    CASE WHEN SUM((stats_jogo->>'cruzamentos_totais')::int) > 0
         THEN ROUND(100.0 * SUM((stats_jogo->>'cruzamentos_certos')::int) / SUM((stats_jogo->>'cruzamentos_totais')::int), 1)
         ELSE NULL END AS cruzamentos_certos_pct,

    COALESCE(SUM((stats_jogo->>'bolas_longas_certas')::int), 0) AS bolas_longas_certas,
    COALESCE(SUM((stats_jogo->>'bolas_longas_totais')::int), 0) AS bolas_longas_totais,
    CASE WHEN SUM((stats_jogo->>'bolas_longas_totais')::int) > 0
         THEN ROUND(100.0 * SUM((stats_jogo->>'bolas_longas_certas')::int) / SUM((stats_jogo->>'bolas_longas_totais')::int), 1)
         ELSE NULL END AS bolas_longas_certas_pct,

    COALESCE(SUM((stats_jogo->>'dribles_tentados')::int), 0) AS dribles_tentados,
    COALESCE(SUM((stats_jogo->>'dribles_conseguidos')::int), 0) AS dribles_conseguidos,
    -- derivado: dribles conseguidos / dribles tentados
    CASE WHEN SUM((stats_jogo->>'dribles_tentados')::int) > 0
         THEN ROUND(100.0 * SUM((stats_jogo->>'dribles_conseguidos')::int) / SUM((stats_jogo->>'dribles_tentados')::int), 1)
         ELSE NULL END AS dribles_conseguidos_pct,

    COALESCE(SUM((stats_jogo->>'toques')::int), 0) AS toques,
    COALESCE(SUM((stats_jogo->>'foras_de_jogo')::int), 0) AS foras_de_jogo,
    COALESCE(SUM((stats_jogo->>'recuperacoes_bola')::int), 0) AS recuperacoes_bola,
    COALESCE(SUM((stats_jogo->>'alivios')::int), 0) AS alivios,

    COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) AS duelos_totais_ganhos,
    COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0) AS duelos_aereos_ganhos,
    COALESCE(SUM((stats_jogo->>'duelos_aereos_perdidos')::int), 0) AS duelos_aereos_perdidos,
    COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) - COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0) AS duelos_terrestres_ganhos,
    COALESCE(SUM((stats_jogo->>'duelos_perdidos')::int), 0) AS duelos_perdidos,
    -- derivado: ganhos / (ganhos + perdidos), nos 3 níveis (total/aéreo/terrestre)
    CASE WHEN (COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) + COALESCE(SUM((stats_jogo->>'duelos_perdidos')::int), 0)) > 0
         THEN ROUND(100.0 * COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0)
                    / (COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) + COALESCE(SUM((stats_jogo->>'duelos_perdidos')::int), 0)), 1)
         ELSE NULL END AS duelos_totais_ganhos_pct,
    CASE WHEN (COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0) + COALESCE(SUM((stats_jogo->>'duelos_aereos_perdidos')::int), 0)) > 0
         THEN ROUND(100.0 * COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0)
                    / (COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0) + COALESCE(SUM((stats_jogo->>'duelos_aereos_perdidos')::int), 0)), 1)
         ELSE NULL END AS duelos_aereos_ganhos_pct,
    CASE WHEN (
             (COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) - COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0))
             + (COALESCE(SUM((stats_jogo->>'duelos_perdidos')::int), 0) - COALESCE(SUM((stats_jogo->>'duelos_aereos_perdidos')::int), 0))
         ) > 0
         THEN ROUND(100.0 * (COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) - COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0))
                    / (
                        (COALESCE(SUM((stats_jogo->>'duelos_totais_ganhos')::int), 0) - COALESCE(SUM((stats_jogo->>'duelos_aereos_ganhos')::int), 0))
                        + (COALESCE(SUM((stats_jogo->>'duelos_perdidos')::int), 0) - COALESCE(SUM((stats_jogo->>'duelos_aereos_perdidos')::int), 0))
                    ), 1)
         ELSE NULL END AS duelos_terrestres_ganhos_pct,

    COALESCE(SUM((stats_jogo->>'desarmes')::int), 0) AS desarmes,
    COALESCE(SUM((stats_jogo->>'desarmes_ganhos')::int), 0) AS desarmes_ganhos,
    -- derivado: desarmes ganhos / desarmes tentados
    CASE WHEN SUM((stats_jogo->>'desarmes')::int) > 0
         THEN ROUND(100.0 * SUM((stats_jogo->>'desarmes_ganhos')::int) / SUM((stats_jogo->>'desarmes')::int), 1)
         ELSE NULL END AS desarmes_ganhos_pct,
    COALESCE(SUM((stats_jogo->>'intercecoes')::int), 0) AS intercecoes,

    COALESCE(SUM((stats_jogo->>'faltas')::int), 0) AS faltas,
    COALESCE(SUM((stats_jogo->>'sofreu_faltas')::int), 0) AS sofreu_faltas,
    COALESCE(SUM((stats_jogo->>'posse_perdida')::int), 0) AS posse_perdida,

    COALESCE(SUM((stats_jogo->>'penaltis_cometidos')::int), 0) AS penaltis_cometidos,
    COALESCE(SUM((stats_jogo->>'penaltis_conquistados')::int), 0) AS penaltis_conquistados,

    COALESCE(SUM((stats_jogo->>'defesas')::int), 0) AS defesas,
    COALESCE(SUM((stats_jogo->>'remates_defendidos_dentro_area')::int), 0) AS remates_defendidos_dentro_area,
    COALESCE(SUM((stats_jogo->>'penaltis_sofridos')::int), 0) AS penaltis_sofridos,
    COALESCE(SUM((stats_jogo->>'saidas_aereas')::int), 0) AS saidas_aereas,
    COALESCE(SUM((stats_jogo->>'saidas_totais')::int), 0) AS saidas_totais,
    COALESCE(SUM((stats_jogo->>'saidas_bem_sucedidas')::int), 0) AS saidas_bem_sucedidas,

    COALESCE(SUM((stats_jogo->>'cartoes_amarelos')::int), 0) AS cartoes_amarelos,
    COALESCE(SUM((stats_jogo->>'cartoes_vermelhos')::int), 0) AS cartoes_vermelhos

FROM estatisticas_sofascore_jogo
WHERE liga_id = 336  -- Taça de Portugal
GROUP BY jogador_id, liga_id, epoca_id

ON CONFLICT (jogador_id, liga_id, epoca_id) DO UPDATE SET
    sofascore_id = EXCLUDED.sofascore_id,
    jogos = EXCLUDED.jogos,
    titularidades = EXCLUDED.titularidades,
    minutos = EXCLUDED.minutos,
    rating = EXCLUDED.rating,
    rating_total = EXCLUDED.rating_total,
    jogos_com_rating = EXCLUDED.jogos_com_rating,
    golos = EXCLUDED.golos,
    assistencias = EXCLUDED.assistencias,
    golos_cabeca = EXCLUDED.golos_cabeca,
    golos_pe_direito = EXCLUDED.golos_pe_direito,
    golos_pe_esquerdo = EXCLUDED.golos_pe_esquerdo,
    golos_penalti = EXCLUDED.golos_penalti,
    golos_mais_assistencias = EXCLUDED.golos_mais_assistencias,
    remates_totais = EXCLUDED.remates_totais,
    remates_a_baliza = EXCLUDED.remates_a_baliza,
    remates_bloqueados = EXCLUDED.remates_bloqueados,
    remates_ao_poste = EXCLUDED.remates_ao_poste,
    remates_fora = EXCLUDED.remates_fora,
    conversao_remates_pct = EXCLUDED.conversao_remates_pct,
    grandes_ocasioes_criadas = EXCLUDED.grandes_ocasioes_criadas,
    grandes_ocasioes_falhadas = EXCLUDED.grandes_ocasioes_falhadas,
    erros_geraram_remate = EXCLUDED.erros_geraram_remate,
    passes_totais = EXCLUDED.passes_totais,
    passes_certos = EXCLUDED.passes_certos,
    passes_errados = EXCLUDED.passes_errados,
    passes_certos_pct = EXCLUDED.passes_certos_pct,
    passes_chave = EXCLUDED.passes_chave,
    passes_campo_contrario_certos = EXCLUDED.passes_campo_contrario_certos,
    passes_campo_contrario_totais = EXCLUDED.passes_campo_contrario_totais,
    passes_proprio_campo_certos = EXCLUDED.passes_proprio_campo_certos,
    passes_proprio_campo_totais = EXCLUDED.passes_proprio_campo_totais,
    cruzamentos_certos = EXCLUDED.cruzamentos_certos,
    cruzamentos_totais = EXCLUDED.cruzamentos_totais,
    cruzamentos_certos_pct = EXCLUDED.cruzamentos_certos_pct,
    bolas_longas_certas = EXCLUDED.bolas_longas_certas,
    bolas_longas_totais = EXCLUDED.bolas_longas_totais,
    bolas_longas_certas_pct = EXCLUDED.bolas_longas_certas_pct,
    dribles_tentados = EXCLUDED.dribles_tentados,
    dribles_conseguidos = EXCLUDED.dribles_conseguidos,
    dribles_conseguidos_pct = EXCLUDED.dribles_conseguidos_pct,
    toques = EXCLUDED.toques,
    foras_de_jogo = EXCLUDED.foras_de_jogo,
    recuperacoes_bola = EXCLUDED.recuperacoes_bola,
    alivios = EXCLUDED.alivios,
    duelos_totais_ganhos = EXCLUDED.duelos_totais_ganhos,
    duelos_aereos_ganhos = EXCLUDED.duelos_aereos_ganhos,
    duelos_aereos_perdidos = EXCLUDED.duelos_aereos_perdidos,
    duelos_terrestres_ganhos = EXCLUDED.duelos_terrestres_ganhos,
    duelos_perdidos = EXCLUDED.duelos_perdidos,
    duelos_totais_ganhos_pct = EXCLUDED.duelos_totais_ganhos_pct,
    duelos_aereos_ganhos_pct = EXCLUDED.duelos_aereos_ganhos_pct,
    duelos_terrestres_ganhos_pct = EXCLUDED.duelos_terrestres_ganhos_pct,
    desarmes = EXCLUDED.desarmes,
    desarmes_ganhos = EXCLUDED.desarmes_ganhos,
    desarmes_ganhos_pct = EXCLUDED.desarmes_ganhos_pct,
    intercecoes = EXCLUDED.intercecoes,
    faltas = EXCLUDED.faltas,
    sofreu_faltas = EXCLUDED.sofreu_faltas,
    posse_perdida = EXCLUDED.posse_perdida,
    penaltis_cometidos = EXCLUDED.penaltis_cometidos,
    penaltis_conquistados = EXCLUDED.penaltis_conquistados,
    defesas = EXCLUDED.defesas,
    remates_defendidos_dentro_area = EXCLUDED.remates_defendidos_dentro_area,
    penaltis_sofridos = EXCLUDED.penaltis_sofridos,
    saidas_aereas = EXCLUDED.saidas_aereas,
    saidas_totais = EXCLUDED.saidas_totais,
    saidas_bem_sucedidas = EXCLUDED.saidas_bem_sucedidas,
    cartoes_amarelos = EXCLUDED.cartoes_amarelos,
    cartoes_vermelhos = EXCLUDED.cartoes_vermelhos,
    atualizado_em = CURRENT_TIMESTAMP;
