"""
Recolhe salários de jogadores da Primeira Liga a partir do
salarysport.com, por época, e guarda em salarios_jogadores.

Fonte: https://salarysport.com/football/premeira-liga/
    - Página da liga: lista os clubes com link para cada plantel.
    - Página de clube: lista o plantel com salário semanal/anual atual
      por jogador + link para a página individual.
    - Página de jogador: tem uma tabela "Contract History" com uma
      linha por ano/época (clube, salário semanal/anual, posição,
      idade, fim de contrato) -- é daqui que vem o histórico por
      época, não só o valor atual.

USA wreq EM VEZ DE requests -- mesma razão que o
importar_sofascore_taca_portugal_jogadores.py: `requests` normal
estava a apanhar 403 (bloqueio Cloudflare/anti-bot) ao fim de pouco
tempo. wreq emula a fingerprint TLS/HTTP2 de um Chrome real, o que já
resolveu o mesmo problema para a Sofascore. Mesma filosofia de
retry/paragem: um 429 espera e tenta outra vez; um 403 para tudo e
preserva o progresso já guardado (RETRIES_403=0), porque insistir
contra um bloqueio ativo só piora a situação.

IMPORTANTE -- AINDA NÃO CONFIRMADO AO VIVO (mesmo aviso que no
ficheiro acima): não consegui buscar o HTML em bruto do site daqui
(bloqueado tanto no meu sandbox como no VM do Cowork ligado ao teu
Mac -- só o teu Mac a correr isto localmente vai mesmo lá chegar). Vi
a estrutura das páginas só através de um fetch já convertido para
texto, por isso o parser identifica colunas pelo TEXTO DO CABEÇALHO
(ver PROCURAR_COLUNA), não por classes CSS. Corre primeiro com
DEBUG=1 (só processa o 1º clube, e imprime as colunas detetadas e a
1ª linha de histórico de cada jogador) e confirma que bate certo
antes de correr em massa.

Granularidade do progresso: por CLUBE (como em importar_taca_portugal.py),
não por jogador -- se parar a meio de um clube, os jogadores desse
clube já guardados não são perdidos (UPSERT por jogador_id+ano), só
voltam a ser pedidos à rede quando retomares.

Esta tabela é nova -- corre migrar_salarios_jogadores.sql primeiro.

Uso:
    python3 importar_salarios_salarysport.py            # corre tudo
    DEBUG=1 python3 importar_salarios_salarysport.py     # só 1 clube, prints extra
"""

import asyncio
import json
import os
import random
import re
import time
import unicodedata
from datetime import datetime

import psycopg
from bs4 import BeautifulSoup
from wreq import Client, Emulation

import api_stats as ast

BASE_URL = "https://salarysport.com"
PROGRESSO_FICHEIRO = "progresso_salarios_salarysport.json"

# Uma entrada por liga -- "slug" é o segmento do URL
# (confirmado ao vivo em /football/, ex: .../football/la-liga/barcelona/),
# "nome" só identifica a liga nos logs. Troca LIGA_A_CORRER para
# processar só uma de cada vez (mesmo padrão do importar_sofascore.py).
LIGAS = [
    {"slug": "premeira-liga", "nome": "Primeira Liga"},
    {"slug": "la-liga", "nome": "La Liga"},
]
LIGA_A_CORRER = "La Liga"

EMULATION = Emulation.Chrome149

SLEEP_MIN = 3.0
SLEEP_MAX = 6.0

RETRIES_403 = 0  # ao primeiro 403, para tudo -- mesma filosofia do importar_sofascore.py
ESPERA_BASE_403 = 45

DEBUG = os.environ.get("DEBUG") == "1"

# Nomes de cabeçalho (em minúsculas, sem acentos) que identificam cada
# coluna que nos interessa -- o primeiro que aparecer no cabeçalho da
# tabela "ganha". Ajusta aqui se o site usar outra palavra (confirma
# com DEBUG=1 antes de correr em massa).
PROCURAR_COLUNA = {
    "nome": ["player", "name"],
    "semanal": ["weekly"],
    "anual": ["yearly", "annual"],
    "idade": ["age"],
    "posicao": ["position", "pos"],
    "nacionalidade": ["nationality", "nation"],
    "ano": ["year", "season"],
    "clube": ["club", "team"],
    "liga": ["league", "competition"],
    "fim_contrato": ["expiry", "contract expir", "expires"],
}

# Época-alvo: só guardamos esta, não o histórico completo do jogador.
# ATENÇÃO: o "ano" da tabela "Contract History" do salarysport é o ANO
# EM QUE A ÉPOCA TERMINA, não o ano em que começa -- confirmado ao
# vivo: a linha da época 2024/25 vem identificada como "2025" nessa
# tabela (não "2024"). Por isso ANO_ALVO != ast.EPOCA (que é 2024, a
# nossa convenção interna de nomear a época pelo ano de início).
ANO_ALVO = 2025


def remover_acentos_url(url):
    """'nicolás' -> 'nicolas' -- confirmado ao vivo: o site tira os
    acentos do slug do URL (a página do clube traz o link com acento,
    mas o URL real da página do jogador não tem nenhum -- dá 404 se
    pedires com o acento)."""
    nfkd = unicodedata.normalize("NFKD", url)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def slug_jogador(nome):
    """
    Constrói o slug do URL individual do jogador a partir do nome
    exibido no plantel -- confirmado ao vivo (ex: "Anatolii Trubin" ->
    .../player/anatolii-trubin/): sem acentos, em minúsculas, espaços
    (e qualquer outro separador) trocados por um único hífen.
    """
    slug = remover_acentos_url(nome).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def log(mensagem):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}")


def sleep_pedido():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def carregar_progresso():
    if not os.path.exists(PROGRESSO_FICHEIRO):
        return {"clubes_concluidos": []}
    with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_progresso(progresso):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, indent=2, ensure_ascii=False)


def _pedir(client, url):
    async def _fazer():
        resp = await client.get(url)
        texto = await resp.text()
        return resp.status, texto
    return asyncio.run(_fazer())


RETRIES_REDE = 3  # ligação pode cair a meio (ConnectionReset) sem resposta HTTP nenhuma -- não é bloqueio, só instabilidade transitória, vale a pena repetir algumas vezes antes de desistir


def wreq_fetch_html(client, url, tentativas_403=0, tentativas_rede=0):
    try:
        status, texto = _pedir(client, url)
    except Exception as erro:
        if tentativas_rede >= RETRIES_REDE:
            raise RuntimeError(
                f"Erro de ligação persistente em {url} ({erro}) -- a parar "
                f"tudo (checkpoint já guardado continua válido)."
            )
        espera = 5 * (tentativas_rede + 1)
        log(f"  [erro de ligação] {erro!r} -- à espera {espera}s antes de retry...")
        time.sleep(espera)
        return wreq_fetch_html(client, url, tentativas_403, tentativas_rede + 1)

    if status == 200:
        return texto
    if status == 404:
        return None
    if status == 429:
        log("  [429] limite atingido, à espera 8s...")
        time.sleep(8)
        return wreq_fetch_html(client, url, tentativas_403)
    if status == 403:
        if tentativas_403 >= RETRIES_403:
            raise RuntimeError(
                f"Bloqueado pela Cloudflare (403) em {url} -- a parar tudo "
                f"(checkpoint já guardado continua válido)."
            )
        espera = ESPERA_BASE_403 * (tentativas_403 + 1)
        log(f"  [403] bloqueado, à espera {espera}s antes de retry...")
        time.sleep(espera)
        return wreq_fetch_html(client, url, tentativas_403 + 1)

    log(f"  [{status}] erro inesperado em {url}")
    return None


def pedir_pagina(client, url):
    url = remover_acentos_url(url)
    texto = wreq_fetch_html(client, url)
    sleep_pedido()
    if texto is None:
        return None
    return BeautifulSoup(texto, "html.parser")


def indices_colunas(linha_cabecalho):
    """
    Mapeia nome_logico -> índice da coluna, a partir do texto de cada
    <th>/<td> da linha de cabeçalho. Devolve {} para colunas não
    encontradas (o chamador decide se isso é crítico).

    IMPORTANTE: usa \\b (fronteira de palavra), não substring simples --
    confirmado ao vivo que "age" (variante de "idade") aparece dentro de
    "Wage" (Weekly Wage) e "year" (variante de "ano") aparece dentro de
    "Yearly" (Yearly Salary). Substring simples apanhava a coluna errada
    (idade ficava com o valor do salário semanal). Também marca cada
    índice como usado para não o atribuir a duas colunas lógicas.
    """
    celulas = linha_cabecalho.find_all(["th", "td"])
    textos = [c.get_text(strip=True).lower() for c in celulas]

    indices = {}
    usados = set()
    for nome_logico, variantes in PROCURAR_COLUNA.items():
        for i, texto in enumerate(textos):
            if i in usados:
                continue
            if any(re.search(rf"\b{re.escape(v)}\b", texto) for v in variantes):
                indices[nome_logico] = i
                usados.add(i)
                break
    return indices


def limpar_dinheiro(texto):
    """
    '$94,530' / '£1,276,972' / '€50.000' -> (valor_float, simbolo).
    Devolve (None, None) se não conseguir extrair número nenhum.
    """
    if not texto:
        return None, None
    texto = texto.strip()
    match_simbolo = re.match(r"^([^\d\s-]+)", texto)
    simbolo = match_simbolo.group(1) if match_simbolo else None

    numero = re.sub(r"[^\d.,]", "", texto)
    if not numero:
        return None, simbolo
    numero = numero.replace(",", "")  # milhares com vírgula (formato en-US do site)
    try:
        return float(numero), simbolo
    except ValueError:
        return None, simbolo


def limpar_data(texto):
    if not texto:
        return None
    texto = texto.strip()
    # "%d %B %Y" (ex: "30 June 2028") -- confirmado ao vivo que é o
    # formato real usado na coluna "Contract Expiry" (sem vírgula, mês
    # por extenso); nenhum dos formatos abaixo batia com isto, por
    # isso contrato_ate ficava sempre None para a maioria dos
    # jogadores (só não falhava para quem por acaso tinha outro
    # formato na própria página).
    for formato in ("%d %B %Y", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def obter_clubes_liga(client, liga_slug):
    """
    Lê a página da liga e devolve [(slug, nome_legivel, url_clube), ...],
    a partir dos links /football/<liga_slug>/<slug>/ (ignora links
    de navegação tipo 'highest-paid', 'transfers', etc.). `nome_legivel`
    é só para logs -- já não é usado para resolver nenhum clube na BD
    (a correspondência de jogador agora é global por nome, ver
    encontrar_jogador_bd()).
    """
    sopa = pedir_pagina(client, f"{BASE_URL}/football/{liga_slug}/")
    if sopa is None:
        return []

    clubes = []
    vistos = set()

    padrao_liga = re.escape(liga_slug)
    for link in sopa.find_all("a", href=True):
        href = link["href"]
        if not re.match(rf"^(https?://salarysport\.com)?/football/{padrao_liga}/[^/]+/?$", href):
            continue

        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if slug in ("highest-paid", "transfers", "finances", "wages-by-age", "highest-wage-bills",
                    "prize-money", "shirt-prices", "stadium-tours", liga_slug):
            continue

        if slug in vistos:
            continue
        vistos.add(slug)

        # Só para exibição em logs -- NÃO usar link.get_text() para
        # isto: o texto visível vem colado ao valor da folha salarial
        # sem espaço (ex: "Sport Lisboa e Benfica£1.3m"), confirmado
        # ao vivo.
        nome_legivel = slug.replace("-", " ").title()

        url_completo = href if href.startswith("http") else BASE_URL + href
        clubes.append((slug, nome_legivel, url_completo))

    return clubes


def obter_plantel_clube(client, url_clube):
    """
    Devolve [{"nome":, "url_jogador":, "semanal":, "anual":, "simbolo":,
    "idade":, "posicao":, "nacionalidade":}, ...] a partir da tabela
    de plantel na página do clube.
    """
    sopa = pedir_pagina(client, url_clube)
    if sopa is None:
        return []

    tabela = None
    indices = {}
    for t in sopa.find_all("table"):
        cabecalho = t.find("tr")
        if not cabecalho:
            continue
        candidatos = indices_colunas(cabecalho)
        if "nome" in candidatos and ("semanal" in candidatos or "anual" in candidatos):
            tabela = t
            indices = candidatos
            break

    if tabela is None:
        log(f"  [aviso] não encontrei tabela de plantel em {url_clube}")
        return []

    if DEBUG:
        log(f"  [debug] colunas detetadas (plantel): {indices}")

    jogadores = []
    linhas = tabela.find_all("tr")[1:]  # salta o cabeçalho
    for linha in linhas:
        celulas = linha.find_all(["td", "th"])
        if not celulas or "nome" not in indices or indices["nome"] >= len(celulas):
            continue

        celula_nome = celulas[indices["nome"]]
        nome = celula_nome.get_text(strip=True)
        if not nome:
            continue

        # NÃO confiar no <a href> da tabela de plantel -- confirmado
        # ao vivo que a maioria dos jogadores não tem lá um link
        # fiável (só ~3 em 30 tinham). O URL da página individual é
        # sempre previsível a partir do nome (ver slug_jogador()), por
        # isso construímos sempre o URL em vez de tentar extraí-lo.
        url_jogador = f"{BASE_URL}/football/player/{slug_jogador(nome)}/"

        def valor(chave):
            idx = indices.get(chave)
            if idx is None or idx >= len(celulas):
                return None
            return celulas[idx].get_text(strip=True)

        semanal, simbolo = limpar_dinheiro(valor("semanal"))
        anual, simbolo2 = limpar_dinheiro(valor("anual"))

        jogadores.append({
            "nome": nome,
            "url_jogador": url_jogador,
            "semanal": semanal,
            "anual": anual,
            "simbolo": simbolo or simbolo2,
            "idade": valor("idade"),
            "posicao": valor("posicao"),
            "nacionalidade": valor("nacionalidade"),
        })

    return jogadores


def obter_historico_jogador(client, url_jogador):
    """
    Devolve [{"ano":, "clube":, "liga":, "semanal":, "anual":,
    "simbolo":, "posicao":, "idade":, "fim_contrato":}, ...] a partir
    da tabela "Contract History" na página do jogador.
    """
    sopa = pedir_pagina(client, url_jogador)
    if sopa is None:
        return []

    tabela = None
    indices = {}
    for t in sopa.find_all("table"):
        cabecalho = t.find("tr")
        if not cabecalho:
            continue
        candidatos = indices_colunas(cabecalho)
        if "ano" in candidatos and ("semanal" in candidatos or "anual" in candidatos):
            tabela = t
            indices = candidatos
            break

    if tabela is None:
        log(f"    [aviso] não encontrei histórico de contrato em {url_jogador}")
        return []

    if DEBUG:
        log(f"    [debug] colunas do histórico: {indices}")

    historico = []
    for linha in tabela.find_all("tr")[1:]:
        celulas = linha.find_all(["td", "th"])
        if not celulas or "ano" not in indices or indices["ano"] >= len(celulas):
            continue

        def valor(chave):
            idx = indices.get(chave)
            if idx is None or idx >= len(celulas):
                return None
            return celulas[idx].get_text(strip=True)

        ano_texto = valor("ano")
        match_ano = re.search(r"\d{4}", ano_texto or "")
        if not match_ano:
            continue
        ano = int(match_ano.group())

        semanal, simbolo = limpar_dinheiro(valor("semanal"))
        anual, simbolo2 = limpar_dinheiro(valor("anual"))

        historico.append({
            "ano": ano,
            "clube": valor("clube"),
            "liga": valor("liga"),
            "semanal": semanal,
            "anual": anual,
            "simbolo": simbolo or simbolo2,
            "posicao": valor("posicao"),
            "idade": valor("idade"),
            "fim_contrato": limpar_data(valor("fim_contrato")),
        })

    return historico


# Palavras que identificam o TIPO de instituição, não o clube em si --
# tanto por extenso (como o salarysport escreve, ex: "Sport Lisboa e
# Benfica", "Futebol Clube do Porto") como em sigla (como a nossa BD
# guarda, ex: "SL Benfica", "FC Porto"). Removendo-as dos dois lados,
# sobra só a palavra que de facto identifica o clube (ex: "benfica"),
# que SIM é comum às duas fontes -- ao contrário do nome completo, que
# não é substring um do outro em nenhum dos dois sentidos.
PALAVRAS_GENERICAS_CLUBE = {
    "sport", "sporting", "futebol", "clube", "desportivo", "grupo",
    "club", "football", "atletico", "academico", "uniao", "vitoria",
    "fc", "sc", "sl", "cd", "gd", "cp", "cf", "ud",
    "de", "do", "da", "dos", "das", "e",
}


# Casos em que o nome do clube é inteiramente composto por palavras
# "genéricas" (ex: "Sporting Clube de Portugal" -> só sobra "portugal",
# que não bate com a sigla "Sporting CP"; "Vitória Sport Clube" -> fica
# vazio, porque "vitoria" também está em PALAVRAS_GENERICAS_CLUBE e "SC"
# tem menos de 3 letras) -- confirmado ao testar os 18 clubes da Primeira
# Liga 24/25, só estes dois falham. Mapeados para um token fixo,
# aplicado ANTES do filtro de palavras genéricas, para não ficarem sem
# nenhuma palavra identificadora comum às duas fontes.
ALIASES_CLUBE_IDENTIFICADOR = {
    "sporting clube de portugal": "sportingcp",
    "sporting cp": "sportingcp",
    "vitoria sport clube": "vitoriasc",
    "vitoria sc": "vitoriasc",
}


def _tokens_identificadores_clube(nome):
    nome = ast.normalizar_nome(nome)
    if nome in ALIASES_CLUBE_IDENTIFICADOR:
        return {ALIASES_CLUBE_IDENTIFICADOR[nome]}
    return {t for t in nome.split() if t not in PALAVRAS_GENERICAS_CLUBE and len(t) >= 3}


def _clube_corresponde(nome_clube_salarysport, nomes_clubes_bd):
    alvo = _tokens_identificadores_clube(nome_clube_salarysport)
    if not alvo:
        return False
    for nome_clube_bd in nomes_clubes_bd:
        if alvo & _tokens_identificadores_clube(nome_clube_bd):
            return True
    return False


def _nomes_correspondem_transliteracao(nome_a, nome_b, prefixo_minimo=5):
    """
    Tolera variantes de transliteração no primeiro nome (ex: "Anatolii"
    vs "Anatoliy") quando o apelido (última palavra) é exatamente
    igual -- aceita se os dois primeiros nomes partilharem um prefixo
    comum de pelo menos `prefixo_minimo` caracteres, mesmo que nenhum
    seja substring do outro. `prefixo_minimo=5` evita falsos positivos
    entre primeiros nomes genuinamente diferentes mas parecidos (ex:
    não devia bastar "and"/"anatolii" baterem só 3 letras).
    """
    tokens_a = nome_a.split()
    tokens_b = nome_b.split()
    if not tokens_a or not tokens_b:
        return False
    if tokens_a[-1] != tokens_b[-1]:
        return False

    primeiro_a, primeiro_b = tokens_a[0], tokens_b[0]
    comuns = 0
    for ca, cb in zip(primeiro_a, primeiro_b):
        if ca != cb:
            break
        comuns += 1
    return comuns >= prefixo_minimo


def _escolher_candidato_local(candidatos, nome_clube_na_epoca, nome_jogador=None):
    for jogador_id, clubes in candidatos:
        if _clube_corresponde(nome_clube_na_epoca, clubes):
            return jogador_id

    sem_clube_conhecido = [c for c in candidatos if not c[1]]
    if len(sem_clube_conhecido) == 1 and len(candidatos) == 1:
        return sem_clube_conhecido[0][0]

    clubes_candidatos = [sorted(clubes) for _, clubes in candidatos]
    log(
        f"    [aviso] Nome ambíguo ({nome_jogador or '?'}, {len(candidatos)} candidatos) e "
        f"nenhum coincide com o clube '{nome_clube_na_epoca}' (clubes na BD por candidato: "
        f"{clubes_candidatos}) -- a ignorar para não misturar jogadores diferentes."
    )
    return None


def encontrar_jogador_bd(jogadores_bd, nome_salarysport, nome_clube_na_epoca):
    """
    Correspondência GLOBAL por nome (já não é restrita ao clube atual
    da página do salarysport) -- desempata pelo clube que aparece na
    PRÓPRIA LINHA do histórico de 2024/25 do jogador
    (nome_clube_na_epoca), não pelo clube atual da página do
    salarysport.

    Isto é o que resolve o falso negativo de "sem correspondência
    local": a página do salarysport mostra o PLANTEL ATUAL de cada
    clube, mas um jogador pode ter saído desse clube depois de 24/25
    -- restringir a busca ao clube atual dava zero candidatos para
    esses casos, mesmo quando o jogador existe na BD.

    NÃO reaproveita ast.encontrar_jogador_bd/escolher_candidato
    diretamente para a parte do CLUBE: aquela função espera que os
    nomes de clube das duas fontes sejam substring um do outro (ex.:
    "FC Porto" vs "Porto"), o que não é o caso aqui -- o salarysport
    escreve por extenso ("Sport Lisboa e Benfica") e a nossa BD usa
    abreviatura ("SL Benfica"), e "sl benfica" nunca é substring de
    "sport lisboa e benfica". Por isso usa-se aqui
    _clube_corresponde(), por interseção de palavras identificadoras
    (ver PALAVRAS_GENERICAS_CLUBE), mas continua a reaproveitar
    ast.normalizar_nome()/ast.nomes_correspondem() para a parte do
    NOME do jogador, que não tem este problema.
    """
    alvo = ast.normalizar_nome(nome_salarysport)

    if alvo in jogadores_bd:
        jogador_id = _escolher_candidato_local(jogadores_bd[alvo], nome_clube_na_epoca, alvo)
        if jogador_id is not None:
            return jogador_id

    for nome_bd, candidatos in jogadores_bd.items():
        if ast.nomes_correspondem(alvo, nome_bd):
            jogador_id = _escolher_candidato_local(candidatos, nome_clube_na_epoca, alvo)
            if jogador_id is not None:
                log(f"    [aviso] Correspondência parcial de nome: '{alvo}' -> '{nome_bd}'")
                return jogador_id

    # Último recurso: variantes de transliteração no primeiro nome
    # (confirmado ao vivo: a nossa BD tem "Anatoliy Trubin", o
    # salarysport escreve "Anatolii Trubin" -- nomes eslavos/ucranianos
    # têm mais do que uma forma "correta" em latim). ast.nomes_correspondem()
    # já não apanha isto porque exige que um primeiro nome seja
    # substring do outro, e "anatolii" não é substring de "anatoliy"
    # nem vice-versa.
    for nome_bd, candidatos in jogadores_bd.items():
        if _nomes_correspondem_transliteracao(alvo, nome_bd):
            jogador_id = _escolher_candidato_local(candidatos, nome_clube_na_epoca, alvo)
            if jogador_id is not None:
                log(f"    [aviso] Correspondência por transliteração: '{alvo}' -> '{nome_bd}'")
                return jogador_id

    return None


def guardar_salarios(conn, jogador_id, historico):
    with conn.cursor() as cursor:
        for linha in historico:
            temporada = f"{linha['ano']}/{str(linha['ano'] + 1)[-2:]}"
            cursor.execute(
                """
                INSERT INTO salarios_jogadores (
                    jogador_id, ano, temporada, clube_nome, liga,
                    salario_semanal, salario_anual, moeda,
                    contrato_ate, fonte
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'salarysport')
                ON CONFLICT (jogador_id, ano) DO UPDATE SET
                    temporada = EXCLUDED.temporada,
                    clube_nome = EXCLUDED.clube_nome,
                    liga = EXCLUDED.liga,
                    salario_semanal = EXCLUDED.salario_semanal,
                    salario_anual = EXCLUDED.salario_anual,
                    moeda = EXCLUDED.moeda,
                    contrato_ate = EXCLUDED.contrato_ate,
                    atualizado_em = CURRENT_TIMESTAMP
                """,
                (
                    jogador_id, linha["ano"], temporada, linha["clube"], linha["liga"],
                    linha["semanal"], linha["anual"], linha["simbolo"],
                    linha["fim_contrato"],
                ),
            )
    conn.commit()


def main():
    client = Client(emulation=EMULATION)
    conn = psycopg.connect(**ast.DB_CONFIG)
    progresso = carregar_progresso()

    try:
        log(f"A carregar jogadores da BD (época {ast.TEMPORADA}) para correspondência global...")
        jogadores_bd = ast.obter_jogadores_bd(conn)
        log(f"  {len(jogadores_bd)} nomes distintos carregados.")

        ligas_a_processar = [l for l in LIGAS if LIGA_A_CORRER is None or l["nome"] == LIGA_A_CORRER]

        for liga in ligas_a_processar:
            processar_liga(client, conn, progresso, jogadores_bd, liga)

        log("Concluído.")
    finally:
        conn.close()


def processar_liga(client, conn, progresso, jogadores_bd, liga):
    liga_slug, nome_liga = liga["slug"], liga["nome"]
    log(f"=== {nome_liga} ===")

    clubes_site = obter_clubes_liga(client, liga_slug)
    log(f"Clubes encontrados na página da liga: {len(clubes_site)}")
    if DEBUG:
        for slug, nome, url in clubes_site:
            log(f"  - {slug} ({nome}) -> {url}")
        clubes_site = clubes_site[:1]
        log("[debug] DEBUG=1: só vou processar o primeiro clube.")

    for indice, (slug, nome_clube_site, url_clube) in enumerate(clubes_site, start=1):
        chave_progresso = f"{liga_slug}:{slug}"
        if chave_progresso in progresso["clubes_concluidos"]:
            log(f"[{indice}/{len(clubes_site)}] {nome_clube_site} -- já processado, a saltar.")
            continue

        log(f"[{indice}/{len(clubes_site)}] {nome_clube_site} ...")

        try:
            plantel = obter_plantel_clube(client, url_clube)
        except RuntimeError as erro:
            log(f"  [BLOQUEADO] {erro}")
            log("  A parar -- o checkpoint já guardado continua válido, retoma mais tarde.")
            return

        log(f"  Jogadores no plantel: {len(plantel)}")

        for jogador in plantel:
            if not jogador["url_jogador"]:
                log(f"  [aviso] sem link de perfil para '{jogador['nome']}', a saltar histórico.")
                continue

            try:
                historico = obter_historico_jogador(client, jogador["url_jogador"])
            except RuntimeError as erro:
                log(f"  [BLOQUEADO] {erro}")
                log("  A parar -- o checkpoint já guardado continua válido, retoma mais tarde.")
                return

            linha_alvo = next((l for l in historico if l["ano"] == ANO_ALVO), None)
            if linha_alvo is None:
                log(f"  [aviso] '{jogador['nome']}' sem linha de {ast.TEMPORADA} no histórico, a saltar.")
                continue

            # A nossa BD guarda o ano de INÍCIO da época (ast.EPOCA
            # = 2024), mas o salarysport identifica a mesma época
            # pelo ano em que ela TERMINA (ANO_ALVO = 2025) --
            # troca-se aqui para não gravar "2025/26" em vez de
            # "2024/25" (ver guardar_salarios(), que calcula a
            # 'temporada' a partir deste campo).
            linha_alvo = dict(linha_alvo)
            linha_alvo["ano"] = ast.EPOCA

            # Desempate por clube feito com o clube da PRÓPRIA linha
            # de 24/25 (linha_alvo["clube"]), não com o clube atual
            # da página do salarysport -- ver encontrar_jogador_bd().
            jogador_id = encontrar_jogador_bd(jogadores_bd, jogador["nome"], linha_alvo["clube"])
            if jogador_id is None:
                log(f"  [aviso] '{jogador['nome']}' ({linha_alvo['clube']}) sem correspondência local, a saltar.")
                continue

            guardar_salarios(conn, jogador_id, [linha_alvo])
            log(f"  {jogador['nome']}: época {ast.TEMPORADA} guardada (clube: {linha_alvo['clube']}).")

            if DEBUG:
                log(f"  [debug] linha guardada: {linha_alvo}")

        progresso["clubes_concluidos"].append(chave_progresso)
        guardar_progresso(progresso)


if __name__ == "__main__":
    main()