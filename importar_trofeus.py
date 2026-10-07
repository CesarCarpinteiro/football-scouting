"""
Recolhe os troféus de cada clube já importado (com transfermarkt_id) a
partir da página "Títulos" do Transfermarkt (/erfolge/verein/<id>) e
guarda em trofeus_clube (tabela criada por migrar_trofeus.py).

Página servida com HTML já pronto (sem JavaScript), tal como
importar_lesoes.py -- usa `requests` simples, sem Playwright.

Uso:
    python3 importar_trofeus.py
"""

import re
import time

import psycopg
import requests
from bs4 import BeautifulSoup


def agora():
    return time.strftime("%H:%M:%S")


def log(mensagem):
    print(f"[{agora()}] {mensagem}")


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

PROGRESSO_FICHEIRO = "progresso_trofeus.json"
TEMPO_ENTRE_CLUBES = 1.5


# ============================================================
# PROGRESSO (retomável)
# ============================================================

def carregar_progresso():
    import json
    try:
        with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def guardar_progresso(feitos):
    import json
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(sorted(feitos), f)


# ============================================================
# EXTRAÇÃO
# ============================================================

def extrair_trofeus(transfermarkt_id):
    """
    Vai à página de títulos do clube e devolve uma lista de dicts
    {"nome_trofeu", "quantidade", "epocas"}.

    IMPORTANTE: nunca disfarçar uma falha de rede como "0 troféus" --
    mesmo padrão de importar_lesoes.py/import_historico_mercado.py.
    """
    url = f"https://www.transfermarkt.pt/x/erfolge/verein/{transfermarkt_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as erro:
        raise RuntimeError(f"falha ao obter página de troféus: {erro}") from erro

    soup = BeautifulSoup(r.text, "html.parser")

    trofeus = []
    for box in soup.select(".box"):
        header = box.select_one(".header h2")
        if not header:
            continue

        titulo = header.get_text(" ", strip=True)
        if not titulo:
            continue

        m = re.match(r"(\d+)x\s+(.+)", titulo)
        if m:
            quantidade, nome_trofeu = int(m.group(1)), m.group(2).strip()
        else:
            quantidade, nome_trofeu = None, titulo

        infotext = box.select_one(".erfolg_infotext_box")
        texto_bruto = infotext.get_text() if infotext else ""
        epocas = [e.strip() for e in texto_bruto.split(",") if e.strip()]

        trofeus.append({
            "nome_trofeu": nome_trofeu,
            "quantidade": quantidade,
            "epocas": epocas,
            "fonte": "transfermarkt",
        })

    return trofeus


# ============================================================
# BASE DE DADOS
# ============================================================

def obter_clubes(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, nome, transfermarkt_id FROM clubes "
            "WHERE transfermarkt_id IS NOT NULL ORDER BY id"
        )
        return cursor.fetchall()


def guardar_trofeus(conn, clube_id, trofeus):
    with conn.cursor() as cursor:
        for t in trofeus:
            cursor.execute(
                """
                INSERT INTO trofeus_clube (clube_id, nome_trofeu, quantidade, epocas, fonte)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (clube_id, nome_trofeu)
                DO UPDATE SET
                    quantidade = EXCLUDED.quantidade,
                    epocas = EXCLUDED.epocas,
                    fonte = EXCLUDED.fonte,
                    atualizado_em = CURRENT_TIMESTAMP
                """,
                (clube_id, t["nome_trofeu"], t["quantidade"], t["epocas"], t["fonte"]),
            )
    conn.commit()


# ============================================================
# PRINCIPAL
# ============================================================

def main():
    feitos = carregar_progresso()
    conn = psycopg.connect(**DB_CONFIG)

    clubes = obter_clubes(conn)
    total = len(clubes)
    log(f"Total de clubes a processar: {total}")

    processados = 0
    com_trofeus = 0

    for indice, (clube_id, nome, transfermarkt_id) in enumerate(clubes, start=1):
        if clube_id in feitos:
            continue

        try:
            trofeus = extrair_trofeus(transfermarkt_id)
            guardar_trofeus(conn, clube_id, trofeus)
            if trofeus:
                com_trofeus += 1
            log(f"[{indice}/{total}] {nome}: {len(trofeus)} troféus")
            feitos.add(clube_id)
            guardar_progresso(feitos)
            processados += 1
        except Exception as erro:
            log(f"  [ERRO] {nome}: {erro}")
            conn.rollback()

        time.sleep(TEMPO_ENTRE_CLUBES)

    conn.close()
    log(f"Concluído. Processados: {processados}, com troféus: {com_trofeus}")


if __name__ == "__main__":
    main()
