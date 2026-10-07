import requests
from bs4 import BeautifulSoup
import psycopg
import time
from datetime import datetime

def converter_data(data_texto):
    if not data_texto:
        return None

    try:
        return datetime.strptime(
            data_texto,
            "%d/%m/%Y"
        ).date()

    except ValueError:
        return None

def converter_altura(altura_texto):
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

    except ValueError:
        return None

def converter_valor_mercado(valor):
    if not valor:
        return None

    try:
        valor = valor.strip()
        valor = valor.replace("€", "").replace(" ", "")
        valor = valor.replace(",", ".")

        if "M" in valor:
            valor = valor.replace("M", "")
            return int(float(valor) * 1_000_000)

        if "K" in valor:
            valor = valor.replace("K", "")
            return int(float(valor) * 1_000)

        return int(float(valor))

    except (ValueError, TypeError):
        return None
    
# =========================
# 1. CONFIGURAÇÃO
# =========================

url = "https://www.transfermarkt.pt/fc-porto/kader/verein/720/saison_id/2026"

headers = {
    "User-Agent": "Mozilla/5.0"
}


# =========================
# 2. PEDIR PÁGINA AO TRANSFERMARKT
# =========================

response = requests.get(url, headers=headers)

print("Status:", response.status_code)

response.raise_for_status()
# =========================
# 3. LER HTML
# =========================

soup = BeautifulSoup(response.text, "html.parser")

tabelas = soup.find_all("table")

print("Tabelas encontradas:", len(tabelas))


# =========================
# 4. ENCONTRAR TABELA DOS JOGADORES
# =========================

tabela_jogadores = tabelas[1]

linhas = tabela_jogadores.find_all("tr")

print("Linhas encontradas:", len(linhas))


# =========================
# 5. EXTRAIR JOGADORES
# =========================

jogadores = []

for linha in linhas[1:]:

    celulas = linha.find_all("td")

    if len(celulas) >= 9:

        # Nacionalidade
        imagem = celulas[6].find("img")

        #if imagem:
        #    nacionalidade = imagem.get("title")
        #else:
        #    nacionalidade = None

        # Link do perfil do jogador
        link_tag = celulas[3].find("a", href=True)

        if link_tag:
            link_perfil = "https://www.transfermarkt.pt" + link_tag["href"]
        else:
            link_perfil = None

        jogador = {
            "nome": celulas[3].get_text(strip=True),
            "numero": celulas[0].get_text(strip=True),
            "posicao_principal": None,   # já tinhas — vem da tabela do plantel
            "posicoes_secundarias": None,
            "idade": celulas[5].get_text(strip=True),
            #"nacionalidade": nacionalidade,
            "contrato": celulas[7].get_text(strip=True),
            "valor_mercado": converter_valor_mercado(
                celulas[7].get_text(strip=True)
            ),
            "link_perfil": link_perfil,
            "pe_preferencial": None,
            "no_clube_desde": None,      # NOVO — lista, pode ter várias
            "nacionalidade": None,        # já tinhas — vem da tabela do plantel, serve de reserva
            "outras_nacionalidades": [],           # NOVO — lista, pode ficar vazia
            "altura": None,                        # NOVO

        }

        jogadores.append(jogador)


# =========================
# 6. ENTRAR NO PERFIL DE CADA JOGADOR
# =========================

for jogador in jogadores:

    if not jogador["link_perfil"]:
        continue

    response_perfil = requests.get(jogador["link_perfil"], headers=headers)

    soup_perfil = BeautifulSoup(response_perfil.text, "html.parser")

    labels = soup_perfil.select(".info-table__content--regular")
    valores = soup_perfil.select(".info-table__content--bold")

    for label, valor in zip(labels, valores):

        chave = label.get_text(strip=True).lower()
        texto = valor.get_text(strip=True)

        if chave.startswith("pé"):
            jogador["pe_preferencial"] = texto

        if "equipa desde" in chave:
            jogador["no_clube_desde"] = texto
        
        if chave.startswith("altura"):
            jogador["altura"] = converter_altura(texto)

        if chave.startswith("nacionalidade"):
            imagens = valor.find_all("img")
            nacionalidades = [img.get("title") for img in imagens if img.get("title")]

            if nacionalidades:
                jogador["nacionalidade"] = nacionalidades[0]
                jogador["outras_nacionalidades"] = nacionalidades[1:]

        # Posição principal e posições secundárias
    bloco_posicao = soup_perfil.find("div", class_="detail-position")

    posicao_principal = None
    posicoes_secundarias = []

    if bloco_posicao:
        titulos = bloco_posicao.find_all(class_="detail-position__title")

        for titulo in titulos:
            titulo_texto = titulo.get_text(strip=True).lower()

            # valores logo a seguir ao título, na mesma zona
            valores = titulo.find_next_siblings(class_="detail-position__position")

            if not valores and titulo.parent:
                valores = titulo.parent.find_all(class_="detail-position__position")

            valores_texto = [v.get_text(strip=True) for v in valores]

            if "principal" in titulo_texto:
                posicao_principal = valores_texto[0] if valores_texto else None
            elif "secundária" in titulo_texto:
                posicoes_secundarias = valores_texto

    jogador["posicao_principal"] = posicao_principal
    jogador["posicoes_secundarias"] = posicoes_secundarias

    time.sleep(1.5)


# =========================
# 7. MOSTRAR JOGADORES
# =========================

print()
print("Jogadores encontrados:", len(jogadores))
print()

for jogador in jogadores:
    print(jogador)

# =========================
# 7. LIGAR À BASE DE DADOS
# =========================

conn = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="football",
    user="scouting",
    password="scouting"
)

cursor = conn.cursor()

print()
print("Ligação à BD estabelecida!")


# =========================
# 8. PROCURAR FC PORTO
# =========================

cursor.execute("""
    SELECT id
    FROM clubes
    WHERE transfermarkt_id = %s
""", (720,))

resultado_clube = cursor.fetchone()

print("FC Porto:", resultado_clube)

if resultado_clube is None:
    raise ValueError(
        "FC Porto não foi encontrado na base de dados."
    )

clube_id = resultado_clube[0]


# =========================
# 9. PROCESSAR JOGADORES
# =========================

for jogador in jogadores:
    print(jogador)
    cursor.execute("""
        SELECT id
        FROM jogadores
        WHERE nome = %s
    """, (
        jogador["nome"],
    ))

    resultado = cursor.fetchone()


    # =========================
    # JOGADOR EXISTENTE
    # =========================

    if resultado:

        jogador_id = resultado[0]

        cursor.execute("""
            UPDATE jogadores
            SET
                numero = %s,
                idade = %s,
                altura = %s,
                posicao_principal = %s,
                posicoes_secundarias = %s,
                nacionalidade = %s,
                outras_nacionalidades = %s,
                contrato = %s,
                pe_preferencial = %s,
                valor_mercado = %s
            WHERE id = %s
        """, (
            jogador["numero"],
            jogador["idade"],
            jogador["altura"],
            jogador["posicao_principal"],
            jogador["posicoes_secundarias"],
            jogador["nacionalidade"],
            jogador["outras_nacionalidades"],
            converter_data(
                jogador["contrato"]
            ),
            jogador["pe_preferencial"],
            jogador["valor_mercado"],
            jogador_id
        ))

        print(
            "Jogador atualizado:",
            jogador["nome"],
            "| ID:",
            jogador_id
        )


    # =========================
    # JOGADOR NOVO
    # =========================

    else:

        cursor.execute("""
            INSERT INTO jogadores (
                nome,
                numero,
                idade,
                altura,
                posicao_principal,
                posicoes_secundarias,
                nacionalidade,
                outras_nacionalidades,
                contrato,
                pe_preferencial,
                valor_mercado
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            jogador["nome"],
            jogador["numero"],
            jogador["idade"],
            jogador["altura"],
            jogador["posicao_principal"],
            jogador["posicoes_secundarias"],
            jogador["nacionalidade"],
            jogador["outras_nacionalidades"],
            converter_data(
                jogador["contrato"]
            ),
            jogador["pe_preferencial"],
            jogador["valor_mercado"]
        ))

        jogador_id = cursor.fetchone()[0]

        print(
            "Jogador inserido:",
            jogador["nome"],
            "| ID:",
            jogador_id
        )


    # =========================
    # 10. LIGAR AO FC PORTO
    # =========================

    data_entrada = converter_data(
        jogador["no_clube_desde"]
    )

    data_fim = converter_data(
        jogador["contrato"]
    )

    cursor.execute("""
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
    """, (
        jogador_id,
        clube_id,
        "2026/27",
        data_entrada,
        data_fim
    ))

    print(
        "Ligação ao FC Porto criada/atualizada:",
        jogador["nome"]
    )


# =========================
# 11. GUARDAR ALTERAÇÕES
# =========================

conn.commit()

print("Todos os jogadores foram processados!")

# =========================
# 12. FECHAR LIGAÇÃO
# =========================

cursor.close()
conn.close()

print("Processo terminado!")