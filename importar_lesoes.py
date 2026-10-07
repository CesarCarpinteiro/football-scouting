"""
Recolhe o histórico de lesões de cada jogador já importado (com
url_transfermarkt) a partir da página "Histórico de Lesões" do
Transfermarkt (/verletzungen/spieler/<id>) e guarda em
lesoes_jogador (tabela já existente no schema, criada por
create_db.py, apenas sem dados até agora).

Ao contrário das páginas de valor de mercado/transferências, esta
página é servida com HTML já pronto (sem JavaScript), por isso usa
`requests` simples em vez de Playwright -- muito mais rápido.

Uso:
    python3 importar_lesoes.py
"""

import re
import sys
import json
import time
import psycopg
import requests
from datetime import datetime
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "football",
    "user": "scouting",
    "password": "scouting",
}

PROGRESSO_FICHEIRO = "progresso_lesoes.json"
TEMPO_ENTRE_JOGADORES = 1.0


def agora():
    return datetime.now().strftime("%H:%M:%S")


def log(mensagem):
    print(f"[{agora()}] {mensagem}")


# ============================================================
# PROGRESSO (retomável)
# ============================================================

def carregar_progresso():
    try:
        with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def guardar_progresso(feitos):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(sorted(feitos), f)


# ============================================================
# CONVERSÕES
# ============================================================

def converter_data(texto):
    if not texto:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", texto.strip())
    if not m:
        return None
    dia, mes, ano = m.groups()
    if len(ano) == 2:
        ano = "20" + ano
    try:
        return datetime(int(ano), int(mes), int(dia)).date()
    except ValueError:
        return None


def converter_dias(texto):
    if not texto:
        return None
    texto = texto.strip().lower()
    if texto in ("-", ""):
        return None
    m = re.search(r"(\d+)", texto)
    if not m:
        return None
    numero = int(m.group(1))
    if "mes" in texto or "mês" in texto:
        return numero * 30
    return numero


def converter_jogos(texto):
    if not texto:
        return None
    texto = texto.strip()
    if texto in ("-", "?", ""):
        return None
    m = re.search(r"(\d+)", texto)
    return int(m.group(1)) if m else None


def url_para_slug_e_id(url_perfil):
    m = re.search(r"transfermarkt\.pt/([^/]+)/profil/spieler/(\d+)", url_perfil)
    if not m:
        return None, None
    return m.group(1), m.group(2)


# ============================================================
# EXTRAÇÃO
# ============================================================

def extrair_lesoes(slug, spieler_id):
    url = f"https://www.transfermarkt.pt/{slug}/verletzungen/spieler/{spieler_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as erro:
        # IMPORTANTE: nunca disfarçar uma falha de rede/DNS como "0
        # lesões" -- já aconteceu um corte de internet a meio de uma
        # corrida marcar ~350 jogadores da Ligue 1 como "sem lesões"
        # quando na verdade nunca conseguimos sequer pedir a página.
        # Ao propagar o erro, o caller (main()) não marca o jogador
        # como concluído, por isso é retomado numa próxima corrida.
        raise RuntimeError(f"falha ao obter página de lesões: {erro}") from erro

    soup = BeautifulSoup(r.text, "html.parser")

    tabela_alvo = None
    for tabela in soup.find_all("table"):
        cabecalho = tabela.find("tr")
        if not cabecalho:
            continue
        texto_cabecalho = cabecalho.get_text(" ", strip=True).lower()
        if "época" in texto_cabecalho and "lesão" in texto_cabecalho:
            tabela_alvo = tabela
            break

    if tabela_alvo is None:
        return []

    registos = []
    for linha in tabela_alvo.find_all("tr")[1:]:
        celulas = linha.find_all("td")
        if len(celulas) < 5:
            continue

        epoca = celulas[0].get_text(" ", strip=True)
        tipo_lesao = celulas[1].get_text(" ", strip=True)
        data_inicio = converter_data(celulas[2].get_text(" ", strip=True))
        data_fim = converter_data(celulas[3].get_text(" ", strip=True))
        dias = converter_dias(celulas[4].get_text(" ", strip=True))
        jogos = converter_jogos(celulas[5].get_text(" ", strip=True)) if len(celulas) > 5 else None

        if not tipo_lesao or not data_inicio:
            continue

        registos.append({
            "epoca": epoca,
            "tipo_lesao": tipo_lesao,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "dias_indisponivel": dias,
            "jogos_falhados": jogos,
        })

    return registos


# ============================================================
# BASE DE DADOS
# ============================================================

def obter_jogadores(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, url_transfermarkt FROM jogadores "
            "WHERE url_transfermarkt IS NOT NULL ORDER BY id"
        )
        return cursor.fetchall()


def guardar_lesoes(conn, jogador_id, registos):
    if not registos:
        return
    with conn.cursor() as cursor:
        for r in registos:
            cursor.execute(
                """
                INSERT INTO lesoes_jogador (
                    jogador_id, tipo_lesao, data_inicio, data_fim,
                    dias_indisponivel, jogos_falhados, epoca
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (jogador_id, tipo_lesao, data_inicio)
                DO UPDATE SET
                    data_fim = EXCLUDED.data_fim,
                    dias_indisponivel = EXCLUDED.dias_indisponivel,
                    jogos_falhados = EXCLUDED.jogos_falhados,
                    epoca = EXCLUDED.epoca
                """,
                (
                    jogador_id, r["tipo_lesao"], r["data_inicio"], r["data_fim"],
                    r["dias_indisponivel"], r["jogos_falhados"], r["epoca"],
                ),
            )
    conn.commit()


# ============================================================
# PRINCIPAL
# ============================================================

def main():
    feitos = carregar_progresso()
    conn = psycopg.connect(**DB_CONFIG)

    jogadores = obter_jogadores(conn)
    total = len(jogadores)
    log(f"Total de jogadores a processar: {total}")

    processados = 0
    com_lesoes = 0

    for indice, (jogador_id, url_perfil) in enumerate(jogadores, start=1):
        if jogador_id in feitos:
            continue

        slug, spieler_id = url_para_slug_e_id(url_perfil)
        if not slug:
            feitos.add(jogador_id)
            guardar_progresso(feitos)
            continue

        try:
            lesoes = extrair_lesoes(slug, spieler_id)
            guardar_lesoes(conn, jogador_id, lesoes)
            if lesoes:
                com_lesoes += 1
            if indice % 50 == 0 or lesoes:
                log(f"[{indice}/{total}] jogador_id={jogador_id} ({slug}): {len(lesoes)} lesões")
            feitos.add(jogador_id)
            guardar_progresso(feitos)
            processados += 1
        except Exception as erro:
            log(f"  [ERRO] jogador_id={jogador_id}: {erro}")
            conn.rollback()

        time.sleep(TEMPO_ENTRE_JOGADORES)

    conn.close()
    log(f"Concluído. Processados: {processados}, com lesões registadas: {com_lesoes}")


if __name__ == "__main__":
    main()
