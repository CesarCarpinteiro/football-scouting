
import requests
from bs4 import BeautifulSoup
import psycopg
import time

from datetime import datetime


# ============================================================
# 1. CONFIGURAÇÃO
# ============================================================

TRANSFERMARKT_CLUBE_ID = 720

URL_BASE = (
    "https://www.transfermarkt.pt/"
    "fc-porto/kader/verein/720/saison_id/{}"
)

# Podes começar apenas com uma época:
# EPOCAS = {"2023/24": 2023}

EPOCAS = {
    "2023/24": 2023,
    "2026/27": 2026,
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


# ============================================================
# 2. FUNÇÕES AUXILIARES
# ============================================================

def converter_data(data_texto):
    """
    Converte uma data no formato DD/MM/YYYY
    para um objeto date do Python.
    """

    if not data_texto:
        return None

    data_texto = data_texto.strip()

    formatos = [
        "%d/%m/%Y",
        "%d/%m/%y",
    ]

    for formato in formatos:
        try:
            return datetime.strptime(
                data_texto,
                formato
            ).date()

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

    try:
        texto_limpo = (
            altura_texto
            .replace("m", "")
            .replace("\xa0", "")
            .strip()
            .replace(",", ".")
        )

        metros = float(texto_limpo)

        return round(metros * 100)

    except (ValueError, TypeError):
        return None
    
def converter_numero(numero_texto):
    """
    Converte o número da camisola (texto) para inteiro.
    Devolve None se vier vazio ou "-" (sem número atribuído).
    """
    if not numero_texto:
        return None

    try:
        return int(numero_texto.strip())
    except (ValueError, TypeError):
        return None


def extrair_transfermarkt_id(url):
    """
    Extrai o ID do jogador a partir do URL
    do Transfermarkt.

    Exemplo:
    /.../spieler/123456
    """

    if not url:
        return None

    try:
        partes = url.strip("/").split("/")

        if "spieler" in partes:
            indice = partes.index("spieler")

            return int(partes[indice + 1])

    except (ValueError, IndexError, TypeError):
        pass

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

    if celula is None:
        return None

    link_tag = celula.find("a", href=True)

    if not link_tag:
        return None

    href = link_tag["href"]

    if href.startswith("http"):
        return href

    return "https://www.transfermarkt.pt" + href


# ============================================================
# 3. EXTRAIR JOGADORES DE UMA ÉPOCA
# ============================================================

def extrair_jogadores_epoca(epoca, saison_id):
    """
    Extrai os jogadores do plantel do FC Porto
    para uma época específica.
    """

    url = URL_BASE.format(saison_id)

    print()
    print("=" * 70)
    print(f"A extrair plantel da época {epoca}")
    print(f"URL: {url}")
    print("=" * 70)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        print("Status HTTP:", response.status_code)

        response.raise_for_status()

    except requests.RequestException as erro:
        print(f"Erro ao obter a página: {erro}")
        return []

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    tabelas = soup.find_all("table")

    print("Tabelas encontradas:", len(tabelas))

    if len(tabelas) < 2:
        print("Não foi encontrada a tabela esperada dos jogadores.")
        return []

    tabela_jogadores = tabelas[1]

    linhas = tabela_jogadores.find_all("tr")

    print("Linhas encontradas:", len(linhas))

    jogadores = []

    for linha in linhas[1:]:
        celulas = linha.find_all("td")

        if len(celulas) < 4:
            continue

        # Número da camisola
        numero = obter_texto_seguro(celulas[0])

        # Nome e link do perfil
        nome = obter_texto_seguro(celulas[3])

        link_perfil = extrair_link_perfil(celulas[3])

        if not nome or not link_perfil:
            continue

        # Nacionalidade da tabela do plantel
        nacionalidade = None

        if len(celulas) > 6:
            imagem = celulas[6].find("img")

            if imagem:
                nacionalidade = (
                    imagem.get("title")
                    or imagem.get("alt")
                )

        jogador = {
            "nome": nome,
            "numero": numero,
            "posicao_principal": None,

            # IMPORTANTE:
            # TEXT[] -> lista Python
            "posicoes_secundarias": [],

            "pe_preferencial": None,
            "altura": None,
            "data_nascimento": None,

            "nacionalidade": nacionalidade,

            # IMPORTANTE:
            # TEXT[] -> lista Python
            "outras_nacionalidades": [],

            "contrato": None,
            "transfermarkt_id": extrair_transfermarkt_id(
                link_perfil
            ),
            "url_transfermarkt": link_perfil,

            "no_clube_desde": None,
            "temporada": epoca,
        }

        jogadores.append(jogador)

    print("Jogadores encontrados na tabela:", len(jogadores))

    return jogadores


# ============================================================
# 4. COMPLETAR DADOS ATRAVÉS DO PERFIL DO JOGADOR
# ============================================================

def completar_dados_jogador(jogador):
    """
    Visita o perfil individual do jogador
    e extrai os dados adicionais.
    """

    url = jogador.get("url_transfermarkt")

    if not url:
        return jogador

    print(
        f"A obter perfil: {jogador['nome']}"
    )

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        response.raise_for_status()

    except requests.RequestException as erro:
        print(
            f"Erro ao obter perfil de "
            f"{jogador['nome']}: {erro}"
        )

        return jogador

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # --------------------------------------------------------
    # Dados gerais do perfil
    # --------------------------------------------------------

    labels = soup.select(
        ".info-table__content--regular"
    )

    valores = soup.select(
        ".info-table__content--bold"
    )

    for label, valor in zip(labels, valores):
        chave = obter_texto_seguro(label)

        texto = obter_texto_seguro(valor)

        if not chave:
            continue

        chave = chave.lower()

        # Pé preferencial
        if chave.startswith("pé"):
            jogador["pe_preferencial"] = texto

        # Data de entrada no clube
        elif "equipa desde" in chave:
            jogador["no_clube_desde"] = texto

        # Altura
        elif chave.startswith("altura"):
            jogador["altura"] = converter_altura(texto)

        # Data de nascimento
        elif (
            "data de nascimento" in chave
            or "nascido" in chave
        ):
            jogador["data_nascimento"] = converter_data(
                texto
            )

        # Nacionalidades
        elif chave.startswith("nacionalidade"):
            imagens = valor.find_all("img")

            nacionalidades = []

            for imagem in imagens:
                nacionalidade = (
                    imagem.get("title")
                    or imagem.get("alt")
                )

                if nacionalidade:
                    nacionalidades.append(
                        nacionalidade.strip()
                    )

            if nacionalidades:
                jogador["nacionalidade"] = (
                    nacionalidades[0]
                )

                # Continua a ser uma LISTA
                jogador["outras_nacionalidades"] = (
                    nacionalidades[1:]
                )

        # Contrato
        elif "contrato termina" in chave:
            jogador["contrato"] = texto

        elif chave == "contrato":
            jogador["contrato"] = texto

    # --------------------------------------------------------
    # Posição principal e posições secundárias
    # --------------------------------------------------------

    bloco_posicao = soup.find(
        "div",
        class_="detail-position"
    )

    posicao_principal = None
    posicoes_secundarias = []

    if bloco_posicao:
        titulos = bloco_posicao.find_all(
            class_="detail-position__title"
        )

        for titulo in titulos:
            titulo_texto = obter_texto_seguro(
                titulo
            )

            if not titulo_texto:
                continue

            titulo_texto = titulo_texto.lower()

            valores_posicao = titulo.find_next_siblings(
                class_="detail-position__position"
            )

            if (
                not valores_posicao
                and titulo.parent
            ):
                valores_posicao = (
                    titulo.parent.find_all(
                        class_="detail-position__position"
                    )
                )

            valores_texto = []

            for valor in valores_posicao:
                texto_posicao = obter_texto_seguro(
                    valor
                )

                if texto_posicao:
                    valores_texto.append(
                        texto_posicao
                    )

            if "principal" in titulo_texto:
                if valores_texto:
                    posicao_principal = (
                        valores_texto[0]
                    )

            elif "secundária" in titulo_texto:
                posicoes_secundarias.extend(
                    valores_texto
                )

            elif "secundaria" in titulo_texto:
                posicoes_secundarias.extend(
                    valores_texto
                )

    jogador["posicao_principal"] = posicao_principal

    # Garante que é sempre uma lista
    jogador["posicoes_secundarias"] = (
        posicoes_secundarias
    )

    # Garante que é sempre uma lista
    jogador["outras_nacionalidades"] = (
        jogador.get("outras_nacionalidades") or []
    )

    time.sleep(TEMPO_ESPERA)

    return jogador


# ============================================================
# 5. LIGAÇÃO À BASE DE DADOS
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
        password="scouting"
    )


# ============================================================
# 6. OBTER ID DO CLUBE
# ============================================================

def obter_clube_id(cursor):
    """
    Obtém o ID do FC Porto na tabela clubes.
    """

    cursor.execute(
        """
        SELECT id
        FROM clubes
        WHERE transfermarkt_id = %s
        """,
        (TRANSFERMARKT_CLUBE_ID,)
    )

    resultado = cursor.fetchone()

    if resultado is None:
        raise ValueError(
            "FC Porto não foi encontrado na tabela clubes."
        )

    return resultado[0]


# ============================================================
# 7. INSERIR OU ATUALIZAR JOGADOR
# ============================================================

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

    transfermarkt_id = jogador.get(
        "transfermarkt_id"
    )

    # Garantir arrays válidos
    posicoes_secundarias = (
        jogador.get("posicoes_secundarias") or []
    )

    outras_nacionalidades = (
        jogador.get("outras_nacionalidades") or []
    )

    # Garantir que os valores são listas
    if not isinstance(posicoes_secundarias, list):
        posicoes_secundarias = [
            posicoes_secundarias
        ]

    if not isinstance(outras_nacionalidades, list):
        outras_nacionalidades = [
            outras_nacionalidades
        ]

    # --------------------------------------------------------
    # Procurar jogador pelo transfermarkt_id
    # --------------------------------------------------------

    resultado = None

    if transfermarkt_id is not None:
        cursor.execute(
            """
            SELECT id
            FROM jogadores
            WHERE transfermarkt_id = %s
            """,
            (transfermarkt_id,)
        )

        resultado = cursor.fetchone()

    # --------------------------------------------------------
    # Fallback: procurar pelo nome
    # --------------------------------------------------------

    if resultado is None:
        cursor.execute(
            """
            SELECT id
            FROM jogadores
            WHERE nome = %s
            """,
            (jogador["nome"],)
        )

        resultado = cursor.fetchone()

    # --------------------------------------------------------
    # Jogador existente
    # --------------------------------------------------------

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
                transfermarkt_id = %s,
                url_transfermarkt = %s
            WHERE id = %s
            """,
            (
                jogador["nome"],
                converter_numero(jogador["numero"]),
                jogador["posicao_principal"],
                posicoes_secundarias,
                jogador["pe_preferencial"],
                jogador["altura"],
                jogador["data_nascimento"],
                jogador["nacionalidade"],
                outras_nacionalidades,
                converter_data(
                    jogador["contrato"]
                ),
                jogador["transfermarkt_id"],
                jogador["url_transfermarkt"],
                jogador_id,
            )
        )

        print(
            "Jogador atualizado:",
            jogador["nome"],
            "| ID:",
            jogador_id
        )

        return jogador_id

    # --------------------------------------------------------
    # Jogador novo
    # --------------------------------------------------------

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
            transfermarkt_id,
            url_transfermarkt
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            jogador["nome"],
            converter_numero(jogador["numero"]),
            jogador["posicao_principal"],
            posicoes_secundarias,
            jogador["pe_preferencial"],
            jogador["altura"],
            jogador["data_nascimento"],
            jogador["nacionalidade"],
            outras_nacionalidades,
            converter_data(
                jogador["contrato"]
            ),
            jogador["transfermarkt_id"],
            jogador["url_transfermarkt"],
        )
    )

    jogador_id = cursor.fetchone()[0]

    print(
        "Jogador inserido:",
        jogador["nome"],
        "| ID:",
        jogador_id
    )

    return jogador_id


# ============================================================
# 8. ASSOCIAR JOGADOR AO FC PORTO
# ============================================================

def associar_jogador_clube(
    cursor,
    jogador_id,
    clube_id,
    jogador
):
    """
    Cria ou atualiza a associação entre:
    jogador, clube e temporada.
    """

    data_entrada = converter_data(
        jogador.get("no_clube_desde")
    )

    data_fim = converter_data(
        jogador.get("contrato")
    )

    temporada = jogador["temporada"]

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
        (
            jogador_id,
            clube_id,
            temporada,
            data_entrada,
            data_fim,
        )
    )

    print(
        "Associação criada/atualizada:",
        jogador["nome"],
        "| Época:",
        temporada
    )


# ============================================================
# 9. MOSTRAR RESUMO DO JOGADOR
# ============================================================

def mostrar_jogador(jogador):
    """
    Mostra os dados extraídos para validação.
    """

    print()
    print("-" * 60)
    print("Nome:", jogador["nome"])
    print("Número:", jogador["numero"])
    print(
        "Posição principal:",
        jogador["posicao_principal"]
    )
    print(
        "Posições secundárias:",
        jogador["posicoes_secundarias"]
    )
    print(
        "Pé preferencial:",
        jogador["pe_preferencial"]
    )
    print("Altura:", jogador["altura"])
    print(
        "Data de nascimento:",
        jogador["data_nascimento"]
    )
    print(
        "Nacionalidade:",
        jogador["nacionalidade"]
    )
    print(
        "Outras nacionalidades:",
        jogador["outras_nacionalidades"]
    )
    print("Contrato:", jogador["contrato"])
    print(
        "Transfermarkt ID:",
        jogador["transfermarkt_id"]
    )
    print(
        "Época:",
        jogador["temporada"]
    )
    print("-" * 60)


# ============================================================
# 10. PROCESSAR UMA ÉPOCA
# ============================================================

def processar_epoca(cursor, clube_id, epoca, saison_id):
    """
    Extrai, completa e guarda todos os jogadores
    de uma época.
    """

    jogadores = extrair_jogadores_epoca(
        epoca,
        saison_id
    )

    if not jogadores:
        print(
            f"Nenhum jogador encontrado para {epoca}."
        )

        return

    jogadores_completos = []

    for jogador in jogadores:
        jogador_completo = completar_dados_jogador(
            jogador
        )

        jogadores_completos.append(
            jogador_completo
        )

    print()
    print(
        f"Jogadores completos da época {epoca}:",
        len(jogadores_completos)
    )

    for jogador in jogadores_completos:
        mostrar_jogador(jogador)

        jogador_id = obter_ou_inserir_jogador(
            cursor,
            jogador
        )

        associar_jogador_clube(
            cursor,
            jogador_id,
            clube_id,
            jogador
        )

    print()
    print(
        f"Época {epoca} processada com sucesso."
    )


# ============================================================
# 11. FUNÇÃO PRINCIPAL
# ============================================================

def main():
    conn = None
    cursor = None

    try:
        conn = ligar_base_dados()

        cursor = conn.cursor()

        print()
        print("Ligação à BD estabelecida!")

        clube_id = obter_clube_id(cursor)

        print(
            "FC Porto encontrado na BD.",
            "| ID:",
            clube_id
        )

        # Processar cada época configurada
        for epoca, saison_id in EPOCAS.items():
            try:
                processar_epoca(
                    cursor,
                    clube_id,
                    epoca,
                    saison_id
                )

                # Guardar alterações da época
                conn.commit()

                print(
                    f"Alterações da época {epoca} guardadas."
                )

            except Exception as erro_epoca:
                conn.rollback()

                print()
                print(
                    f"Erro ao processar a época {epoca}:"
                )
                print(erro_epoca)

                # Interromper para não continuar
                # com dados potencialmente inconsistentes
                raise

        print()
        print("=" * 70)
        print("Todos os jogadores foram processados!")
        print("=" * 70)

    except Exception as erro:
        if conn:
            conn.rollback()

        print()
        print("ERRO GERAL:")
        print(erro)

        raise

    finally:
        if cursor:
            cursor.close()

        if conn:
            conn.close()

        print()
        print("Ligação à base de dados fechada.")
        print("Processo terminado!")


# ============================================================
# 12. EXECUTAR
# ============================================================

if __name__ == "__main__":
    main()