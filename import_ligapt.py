import re
import requests
from bs4 import BeautifulSoup
import psycopg
import time

from datetime import datetime


def agora():
    """
    Devolve a hora atual formatada, para prefixar logs.
    """
    return datetime.now().strftime("%H:%M:%S")


def log(mensagem):
    """
    Print com timestamp, para acompanhar o progresso ao longo
    de uma execução longa.
    """
    print(f"[{agora()}] {mensagem}")


# ============================================================
# 1. CONFIGURAÇÃO
# ============================================================

BASE_URL = "https://www.transfermarkt.pt"

# Códigos de competição do Transfermarkt:
# Primeira Liga -> PO1
# Liga Portugal 2 (Segunda Liga) -> PO2
# La Liga -> ES1
# Ligue 1 -> FR1
# Eliteserien -> NO1
LIGAS = {
    "Primeira Liga": {
        "codigo": "PO1",
        "url": "https://www.transfermarkt.pt/liga-portugal/startseite/wettbewerb/PO1",
    },
    "Liga Portugal 2": {
        "codigo": "PO2",
        "url": "https://www.transfermarkt.pt/liga-portugal-2/startseite/wettbewerb/PO2",
    },
    "La Liga": {
        "codigo": "ES1",
        "url": "https://www.transfermarkt.pt/laliga/startseite/wettbewerb/ES1",
    },
    "Ligue 1": {
        "codigo": "FR1",
        "url": "https://www.transfermarkt.pt/ligue-1/startseite/wettbewerb/FR1",
    },
    "Jupiler Pro League": {
        "codigo": "BE1",
        "url": "https://www.transfermarkt.pt/jupiler-pro-league/startseite/wettbewerb/BE1",
    },
    # ATENÇÃO: a Noruega joga por ano civil, não por época cruzada
    # (24/25) como as outras -- e o "saison_id" do Transfermarkt para
    # esta liga está desfasado um ano do que mostra no site
    # (saison_id=2024 == "Eliteserien 2025", confirmado em 2026-09-28).
    # Por isso NÃO é processada pelo main()/EPOCAS partilhado deste
    # ficheiro -- ver importar_noruega.py, que usa o seu próprio
    # mapa de época (epoca "2025" -> saison_id 2024).
    "Eliteserien": {
        "codigo": "NO1",
        "url": "https://www.transfermarkt.pt/eliteserien/startseite/wettbewerb/NO1",
    },
}

EPOCAS = {
    "2024/25": 2024,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

TEMPO_ESPERA = 1.5
TEMPO_ESPERA_ENTRE_CLUBES = 2.0


# ============================================================
# 2. FUNÇÕES AUXILIARES
# ============================================================

def converter_data(data_texto):
    """
    Converte uma data no formato DD/MM/YYYY
    para um objeto date do Python.

    O campo "Nasc./Idade" do Transfermarkt vem como
    "16/11/1991 (34)" -- a idade entre parêntesis é descartada,
    ficamos só com a data.
    """

    if not data_texto:
        return None

    data_texto = data_texto.strip()

    correspondencia = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", data_texto)
    if correspondencia:
        data_texto = correspondencia.group(0)

    formatos = [
        "%d/%m/%Y",
        "%d/%m/%y",
    ]

    for formato in formatos:
        try:
            return datetime.strptime(data_texto, formato).date()
        except ValueError:
            continue

    return None


def converter_altura(altura_texto):
    """
    Converte uma altura como:
    1,85m
    1.85 m

    Para centímetros:
    185
    """

    if not altura_texto:
        return None

    altura_texto = altura_texto.strip()
    altura_texto = altura_texto.replace("m", "")
    altura_texto = altura_texto.replace("\xa0", "")
    altura_texto = altura_texto.replace(",", ".")

    try:
        metros = float(altura_texto)
    except ValueError:
        return None

    return round(metros * 100)


def converter_numero(numero_texto):
    """
    Converte o número da camisola (texto) para inteiro.
    Devolve None se vier vazio ou "-" (sem número atribuído).
    """

    if not numero_texto:
        return None

    numero_texto = numero_texto.strip()

    if numero_texto in ("-", ""):
        return None

    try:
        return int(numero_texto)
    except ValueError:
        return None


def extrair_transfermarkt_id(url):
    """
    Extrai o ID do jogador (ou do clube) a partir do URL
    do Transfermarkt.

    Exemplo:
    /.../spieler/123456
    /.../verein/720
    """

    if not url:
        return None

    try:
        partes = url.strip("/").split("/")
        for chave in ("spieler", "verein"):
            if chave in partes:
                indice = partes.index(chave)
                return int(partes[indice + 1])
        return None
    except (ValueError, IndexError, TypeError):
        return None


def converter_valor_mercado(valor):
    """
    Converte um valor de mercado no formato do Transfermarkt
    (ex: "€5,00M", "€800K") para um inteiro em euros.
    """

    if not valor:
        return None

    valor = valor.strip()
    valor = valor.replace("€", "")
    valor = valor.replace(" ", "")
    valor = valor.replace(",", ".")

    multiplicador = 1
    if "M" in valor:
        multiplicador = 1_000_000
        valor = valor.replace("M", "")
    elif "K" in valor:
        multiplicador = 1_000
        valor = valor.replace("K", "")

    try:
        return round(float(valor) * multiplicador)
    except ValueError:
        return None


def obter_texto_seguro(elemento):
    """
    Obtém o texto de um elemento BeautifulSoup
    de forma segura.
    """

    if elemento is None:
        return None

    return elemento.get_text(" ", strip=True)


def extrair_link_perfil(celula):
    """
    Extrai o link completo do perfil do jogador.
    """

    link = celula.find("a")
    if not link:
        return None

    href = link.get("href")
    if not href:
        return None

    if href.startswith("http"):
        return href

    return BASE_URL + href


def url_plantel_para_epoca(url_plantel, saison_id):
    """
    Garante que um URL de plantel aponta para a época (saison_id)
    pretendida, substituindo ou acrescentando o segmento
    "/saison_id/<ano>".
    """

    url_sem_epoca = re.sub(r"/saison_id/\d+", "", url_plantel.rstrip("/"))

    return f"{url_sem_epoca}/saison_id/{saison_id}"


# ============================================================
# 3. EXTRAÇÃO -- LIGA / CLUBES / PLANTÉIS
# ============================================================

def extrair_clubes_liga(nome_liga, url_liga, saison_id):
    """
    Vai à página principal da liga e extrai todos os clubes
    (nome, URL do plantel, transfermarkt_id).

    IMPORTANTE: tem de se passar sempre o saison_id da época
    pretendida. Sem ele, o Transfermarkt devolve a lista de clubes
    da época "atual" do site ao vivo -- que, passado tempo
    suficiente, deixa de ser a época que queremos processar.
    """

    url_liga_epoca = f"{url_liga}?saison_id={saison_id}"

    print()
    print("=" * 70)
    print(f"A extrair clubes de: {nome_liga}")
    print(f"URL: {url_liga_epoca}")
    print("=" * 70)

    try:
        response = requests.get(url_liga_epoca, headers=HEADERS, timeout=30)
        print("Status HTTP:", response.status_code)
        response.raise_for_status()
    except requests.RequestException as erro:
        # IMPORTANTE: nunca disfarçar uma falha de rede/DNS como "0
        # clubes" -- ver import_historico_mercado.py/importar_lesoes.py
        # para o histórico deste tipo de bug. Propagar o erro para o
        # caller não marcar nada como concluído.
        raise RuntimeError(f"Erro ao obter a página da liga: {erro}") from erro

    soup = BeautifulSoup(response.text, "html.parser")

    clubes = []
    vistos = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]

        if "/kader/verein/" not in href:
            continue

        nome_clube = link.get("title")

        if href.startswith("http"):
            url_plantel = href
        else:
            url_plantel = BASE_URL + href

        transfermarkt_id = extrair_transfermarkt_id(url_plantel)

        if not (nome_clube and transfermarkt_id is not None):
            continue

        if transfermarkt_id in vistos:
            continue

        vistos.add(transfermarkt_id)

        clubes.append({
            "nome": nome_clube,
            "url_plantel": url_plantel,
            "transfermarkt_id": transfermarkt_id,
        })

    print(f"Clubes encontrados em {nome_liga}: {len(clubes)}")

    return clubes


def extrair_jogadores_epoca(clube, epoca, saison_id):
    """
    Extrai os jogadores do plantel de um clube
    para uma época específica.
    """

    url = url_plantel_para_epoca(clube["url_plantel"], saison_id)

    print("-" * 70)
    print(f"A extrair plantel de {clube['nome']} - época {epoca}")
    print(f"URL: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        print("Status HTTP:", response.status_code)
        response.raise_for_status()
    except requests.RequestException as erro:
        raise RuntimeError(f"Erro ao obter a página do plantel: {erro}") from erro

    soup = BeautifulSoup(response.text, "html.parser")

    tabelas = soup.find_all("table")
    print("Tabelas encontradas:", len(tabelas))

    tabela_alvo = None
    for tabela in tabelas:
        if tabela.find("img"):
            tabela_alvo = tabela
            break

    if tabela_alvo is None:
        print("Não foi encontrada a tabela esperada dos jogadores.")
        return []

    linhas = tabela_alvo.find_all("tr")
    print("Linhas encontradas:", len(linhas))

    jogadores = []

    for linha in linhas:
        celulas = linha.find_all("td")

        if len(celulas) < 4:
            continue

        imagem = linha.find("img")
        if not imagem:
            continue

        nome = imagem.get("title") or imagem.get("alt")
        if not nome:
            continue

        celula_link = None
        for celula in celulas:
            if celula.find("a", href=re.compile(r"/profil/spieler/")):
                celula_link = celula
                break

        url_perfil = extrair_link_perfil(celula_link) if celula_link else None
        if not url_perfil:
            continue

        transfermarkt_id = extrair_transfermarkt_id(url_perfil)

        numero = None
        celula_numero = linha.find("div", class_="rn_nummer")
        if celula_numero:
            numero = converter_numero(obter_texto_seguro(celula_numero))

        jogadores.append({
            "nome": nome,
            "numero": numero,
            "url_transfermarkt": url_perfil,
            "transfermarkt_id": transfermarkt_id,
            "clube_nome": clube["nome"],
            "temporada": epoca,
        })

    print("Jogadores encontrados na tabela:", len(jogadores))

    return jogadores


def completar_dados_jogador(jogador, indice, total):
    """
    Visita o perfil individual do jogador
    e extrai os dados adicionais.
    """

    url = jogador["url_transfermarkt"]

    print(f"  [{indice}/{total}] A obter perfil: {jogador['nome']}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as erro:
        raise RuntimeError(f"Erro ao obter perfil de {jogador['nome']}: {erro}") from erro

    soup = BeautifulSoup(response.text, "html.parser")

    rotulos = soup.select(".info-table__content--regular")
    valores = soup.select(".info-table__content--bold")

    for rotulo, valor in zip(rotulos, valores):
        texto_rotulo = obter_texto_seguro(rotulo).lower()
        texto_valor = obter_texto_seguro(valor)

        if "pé" in texto_rotulo:
            jogador["pe_preferencial"] = texto_valor
        elif "equipa desde" in texto_rotulo:
            jogador["no_clube_desde"] = texto_valor
        elif "altura" in texto_rotulo:
            jogador["altura"] = converter_altura(texto_valor)
        elif "nasc" in texto_rotulo or "nascido" in texto_rotulo:
            jogador["data_nascimento"] = converter_data(texto_valor)
        elif "nacionalidade" in texto_rotulo:
            bandeiras = valor.find_all("img")
            nomes = [b.get("title") or b.get("alt") for b in bandeiras]
            nomes = [n for n in nomes if n]
            jogador["nacionalidade"] = nomes[0] if nomes else None
            jogador["outras_nacionalidades"] = nomes[1:]
        elif "contrato até" in texto_rotulo or "contrato termina" in texto_rotulo:
            jogador["contrato"] = converter_data(texto_valor)

    if "outras_nacionalidades" not in jogador:
        jogador["outras_nacionalidades"] = []

    jogador["posicao_principal"] = None
    jogador["posicoes_secundarias"] = []

    for bloco in soup.select("div.detail-position"):
        titulo = obter_texto_seguro(bloco.select_one(".detail-position__title"))
        posicao = obter_texto_seguro(bloco.select_one(".detail-position__position"))

        if not (titulo and posicao):
            continue

        if "principal" in titulo.lower():
            jogador["posicao_principal"] = posicao
        elif "secundária" in titulo.lower() or "secundaria" in titulo.lower():
            jogador["posicoes_secundarias"].append(posicao)

    valor_mercado_elemento = soup.select_one(".data-header__market-value-wrapper")
    if valor_mercado_elemento:
        jogador["valor_mercado"] = converter_valor_mercado(obter_texto_seguro(valor_mercado_elemento))

    return jogador


# ============================================================
# 4. BASE DE DADOS
# ============================================================

def ligar_base_dados():
    """
    Cria a ligação à base de dados PostgreSQL.
    """
    return psycopg.connect(
        host="localhost",
        port=5432,
        dbname="football",
        user="scouting",
        password="scouting",
    )


def obter_ou_criar_clube(cursor, clube):
    """
    Procura o clube pelo transfermarkt_id.
    Se não existir, cria-o.
    """

    cursor.execute(
        """
        SELECT id
        FROM clubes
        WHERE transfermarkt_id = %s
        """,
        (clube["transfermarkt_id"],),
    )
    resultado = cursor.fetchone()

    if resultado:
        return resultado[0]

    cursor.execute(
        """
        INSERT INTO clubes (nome, transfermarkt_id)
        VALUES (%s, %s)
        RETURNING id
        """,
        (clube["nome"], clube["transfermarkt_id"]),
    )
    clube_id = cursor.fetchone()[0]

    print(f"Clube inserido: {clube['nome']} | ID: {clube_id}")

    return clube_id


def obter_ou_inserir_jogador(cursor, jogador):
    """
    Procura o jogador através do transfermarkt_id.

    Se existir:
        Atualiza os dados.

    Se não existir:
        Insere um novo jogador.

    IMPORTANTE:
    posicoes_secundarias e outras_nacionalidades
    são listas porque as colunas são TEXT[].
    """

    posicoes_secundarias = jogador.get("posicoes_secundarias") or []
    outras_nacionalidades = jogador.get("outras_nacionalidades") or []

    cursor.execute(
        """
        SELECT id
        FROM jogadores
        WHERE transfermarkt_id = %s
        """,
        (jogador["transfermarkt_id"],),
    )
    resultado = cursor.fetchone()

    if resultado:
        jogador_id = resultado[0]

        cursor.execute(
            """
            UPDATE jogadores
            SET
                nome = %s,
                numero = %s,
                posicao_principal = %s,
                posicoes_secundarias = %s,
                pe_preferencial = %s,
                altura = %s,
                data_nascimento = %s,
                nacionalidade = %s,
                outras_nacionalidades = %s,
                contrato = %s,
                valor_mercado = %s,
                url_transfermarkt = %s
            WHERE id = %s
            """,
            (
                jogador.get("nome"),
                jogador.get("numero"),
                jogador.get("posicao_principal"),
                posicoes_secundarias,
                jogador.get("pe_preferencial"),
                jogador.get("altura"),
                jogador.get("data_nascimento"),
                jogador.get("nacionalidade"),
                outras_nacionalidades,
                jogador.get("contrato"),
                jogador.get("valor_mercado"),
                jogador.get("url_transfermarkt"),
                jogador_id,
            ),
        )

        print(f"Jogador atualizado: {jogador.get('nome')} | ID: {jogador_id}")

        return jogador_id

    cursor.execute(
        """
        INSERT INTO jogadores (
            nome,
            numero,
            posicao_principal,
            posicoes_secundarias,
            pe_preferencial,
            altura,
            data_nascimento,
            nacionalidade,
            outras_nacionalidades,
            contrato,
            valor_mercado,
            url_transfermarkt,
            transfermarkt_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            jogador.get("nome"),
            jogador.get("numero"),
            jogador.get("posicao_principal"),
            posicoes_secundarias,
            jogador.get("pe_preferencial"),
            jogador.get("altura"),
            jogador.get("data_nascimento"),
            jogador.get("nacionalidade"),
            outras_nacionalidades,
            jogador.get("contrato"),
            jogador.get("valor_mercado"),
            jogador.get("url_transfermarkt"),
            jogador["transfermarkt_id"],
        ),
    )
    jogador_id = cursor.fetchone()[0]

    print(f"Jogador inserido: {jogador.get('nome')} | ID: {jogador_id}")

    return jogador_id


def associar_jogador_clube(cursor, jogador_id, clube_id, jogador):
    """
    Cria ou atualiza a associação entre:
    jogador, clube e temporada.
    """

    data_entrada = converter_data(jogador.get("no_clube_desde"))
    data_fim = jogador.get("contrato")
    temporada = jogador.get("temporada")

    cursor.execute(
        """
        INSERT INTO jogador_clube (
            jogador_id,
            clube_id,
            temporada,
            data_entrada,
            data_fim
        )
        VALUES (%s, %s, %s, %s, %s)

        ON CONFLICT (
            jogador_id,
            clube_id,
            temporada
        )
        DO UPDATE SET
            data_entrada = EXCLUDED.data_entrada,
            data_fim = EXCLUDED.data_fim
        """,
        (jogador_id, clube_id, temporada, data_entrada, data_fim),
    )

    print(f"Associação criada/atualizada: {jogador.get('nome')} | Época: {temporada}")


def mostrar_jogador(jogador):
    """
    Mostra os dados extraídos para validação.
    """
    print("-" * 60)
    print("Nome:", jogador.get("nome"))
    print("Número:", jogador.get("numero"))
    print("Posição principal:", jogador.get("posicao_principal"))
    print("Posições secundárias:", jogador.get("posicoes_secundarias"))
    print("Pé preferencial:", jogador.get("pe_preferencial"))
    print("Altura:", jogador.get("altura"))
    print("Data de nascimento:", jogador.get("data_nascimento"))
    print("Nacionalidade:", jogador.get("nacionalidade"))
    print("Outras nacionalidades:", jogador.get("outras_nacionalidades"))
    print("Contrato:", jogador.get("contrato"))
    print("Transfermarkt ID:", jogador.get("transfermarkt_id"))
    print("Época:", jogador.get("temporada"))


# ============================================================
# 5. PROCESSAMENTO POR CLUBE
# ============================================================

def processar_clube(cursor, clube_id, clube, epocas):
    """
    Extrai, completa e guarda todos os jogadores
    de um clube, para todas as épocas configuradas.
    """

    for epoca, saison_id in epocas.items():

        jogadores = extrair_jogadores_epoca(
            clube,
            epoca,
            saison_id
        )

        if not jogadores:
            log(f"Nenhum jogador encontrado para {clube['nome']} em {epoca}.")
            continue

        log(f"A completar perfis de {len(jogadores)} jogadores de {clube['nome']} ({epoca})...")

        for indice, jogador in enumerate(jogadores, start=1):
            jogador = completar_dados_jogador(jogador, indice, len(jogadores))

            jogador_id = obter_ou_inserir_jogador(cursor, jogador)

            associar_jogador_clube(cursor, jogador_id, clube_id, jogador)

            time.sleep(TEMPO_ESPERA)

        log(f"{clube['nome']} ({epoca}) concluído: {len(jogadores)} jogadores processados.")


# ============================================================
# 6. PRINCIPAL
# ============================================================

def main():
    conn = ligar_base_dados()
    cursor = conn.cursor()
    log("Ligação à BD estabelecida!")

    inicio = time.time()
    clubes_ok = 0
    clubes_erro = 0

    try:
        for nome_liga, info in LIGAS.items():
            saison_id_alvo = next(iter(EPOCAS.values()))

            clubes = extrair_clubes_liga(nome_liga, info["url"], saison_id_alvo)
            log(f"{nome_liga}: {len(clubes)} clubes encontrados.")
            log(f"Total de clubes a processar: {len(clubes)}")

            for indice, clube in enumerate(clubes, start=1):
                try:
                    clube_id = obter_ou_criar_clube(cursor, clube)
                    conn.commit()

                    log(f"=== CLUBE {indice}/{len(clubes)} === {clube['nome']} ({nome_liga}) | ID BD: {clube_id}")

                    processar_clube(cursor, clube_id, clube, EPOCAS)
                    conn.commit()

                    clubes_ok += 1
                    log(f"Clube {clube['nome']} processado e guardado. "
                        f"Progresso: {indice}/{len(clubes)} clubes | "
                        f"Tempo decorrido: {time.time() - inicio:.1f}s")

                except Exception as erro:
                    conn.rollback()
                    clubes_erro += 1
                    log(f"ERRO ao processar o clube {clube['nome']}: {erro}")

                time.sleep(TEMPO_ESPERA_ENTRE_CLUBES)

        print("=" * 70)
        log("Todos os clubes foram processados!")
        log(f"Clubes com sucesso: {clubes_ok}")
        log(f"Clubes com erro: {clubes_erro}")

    except Exception as erro:
        conn.rollback()
        log(f"ERRO GERAL: {erro}")
        raise

    finally:
        cursor.close()
        conn.close()
        log("Ligação à base de dados fechada.")
        log("Processo terminado!")


if __name__ == "__main__":
    main()
