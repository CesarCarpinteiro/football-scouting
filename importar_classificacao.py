import re
import time
from datetime import datetime

import psycopg
import requests
from bs4 import BeautifulSoup


def agora():
    return datetime.now().strftime("%H:%M:%S")


def log(mensagem):
    print(f"[{agora()}] {mensagem}")


LIGAS = {
    "Primeira Liga": {
        "codigo": "PO1",
        "slug": "liga-portugal",
    },
    "Liga Portugal 2": {
        "codigo": "PO2",
        "slug": "liga-portugal-2",
    },
    "La Liga": {
        "codigo": "ES1",
        "slug": "laliga",
    },
    "Ligue 1": {
        "codigo": "FR1",
        "slug": "ligue-1",
    },
    "Jupiler Pro League": {
        "codigo": "BE1",
        "slug": "jupiler-pro-league",
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

TEMPO_ESPERA = 2.0


def extrair_classificacao(slug, codigo, saison_id):
    url = f"https://www.transfermarkt.pt/{slug}/tabelle/wettbewerb/{codigo}?saison_id={saison_id}"
    resposta = requests.get(url, headers=HEADERS, timeout=20)
    resposta.raise_for_status()

    soup = BeautifulSoup(resposta.text, "html.parser")
    tabela = soup.select_one("table.items")
    if not tabela:
        raise ValueError(f"tabela de classificação não encontrada em {url}")

    linhas = []
    for tr in tabela.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue

        posicao = int(tds[0].get_text(strip=True))

        link_clube = tds[1].find("a") or tds[2].find("a")
        href = link_clube.get("href") if link_clube else None
        match_id = re.search(r"/verein/(\d+)", href or "")
        if not match_id:
            log(f"  [aviso] sem transfermarkt_id de clube na posição {posicao}, a ignorar linha")
            continue
        transfermarkt_id = int(match_id.group(1))

        jogos = int(tds[3].get_text(strip=True))
        vitorias = int(tds[4].get_text(strip=True))
        empates = int(tds[5].get_text(strip=True))
        derrotas = int(tds[6].get_text(strip=True))

        golos_texto = tds[7].get_text(strip=True)
        golos_marcados, golos_sofridos = golos_texto.split(":")

        pontos = int(tds[9].get_text(strip=True))

        linhas.append({
            "posicao": posicao,
            "transfermarkt_id": transfermarkt_id,
            "jogos": jogos,
            "vitorias": vitorias,
            "empates": empates,
            "derrotas": derrotas,
            "golos_marcados": int(golos_marcados),
            "golos_sofridos": int(golos_sofridos),
            "pontos": pontos,
        })

    return linhas


def guardar_classificacao(conn, liga_nome, epoca_nome, linhas):
    cursor = conn.cursor()
    guardados = 0
    for linha in linhas:
        cursor.execute(
            "SELECT id FROM clubes WHERE transfermarkt_id = %s",
            (linha["transfermarkt_id"],),
        )
        resultado = cursor.fetchone()
        if not resultado:
            log(f"  [aviso] clube com transfermarkt_id={linha['transfermarkt_id']} "
                f"não existe na BD, a ignorar (posição {linha['posicao']})")
            continue
        clube_id = resultado[0]

        cursor.execute("""
            INSERT INTO classificacao_liga
                (liga, epoca, posicao, clube_id, jogos, vitorias, empates,
                 derrotas, golos_marcados, golos_sofridos, pontos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (liga, epoca, clube_id) DO UPDATE SET
                posicao = EXCLUDED.posicao,
                jogos = EXCLUDED.jogos,
                vitorias = EXCLUDED.vitorias,
                empates = EXCLUDED.empates,
                derrotas = EXCLUDED.derrotas,
                golos_marcados = EXCLUDED.golos_marcados,
                golos_sofridos = EXCLUDED.golos_sofridos,
                pontos = EXCLUDED.pontos,
                data_recolha = CURRENT_DATE
        """, (
            liga_nome, epoca_nome, linha["posicao"], clube_id, linha["jogos"],
            linha["vitorias"], linha["empates"], linha["derrotas"],
            linha["golos_marcados"], linha["golos_sofridos"], linha["pontos"],
        ))
        guardados += 1

    conn.commit()
    cursor.close()
    return guardados


def main():
    conn = psycopg.connect(
        host="localhost", port=5432, dbname="football",
        user="scouting", password="scouting",
    )

    for epoca_nome, saison_id in EPOCAS.items():
        for liga_nome, info in LIGAS.items():
            log(f"A extrair classificação: {liga_nome} {epoca_nome}...")
            try:
                linhas = extrair_classificacao(info["slug"], info["codigo"], saison_id)
                guardados = guardar_classificacao(conn, liga_nome, epoca_nome, linhas)
                log(f"  {liga_nome} {epoca_nome}: {guardados}/{len(linhas)} clubes guardados")
            except Exception as erro:
                log(f"  [ERRO] {liga_nome} {epoca_nome}: {erro}")
                conn.rollback()
            time.sleep(TEMPO_ESPERA)

    conn.close()
    log("Concluído.")


if __name__ == "__main__":
    main()
