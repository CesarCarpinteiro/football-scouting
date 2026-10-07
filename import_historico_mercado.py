"""
Recolhe, para cada jogador já importado (com url_transfermarkt),
o histórico de valor de mercado (gráfico) e o histórico de
transferências/empréstimos, e guarda em:

    valores_mercado (jogador_id, data, valor, clube, idade)
    transferencias_jogador (jogador_id, clube_origem, clube_destino,
                             data_transferencia, tipo, valor, epoca)

O histórico de valor de mercado só existe como um gráfico SVG
renderizado em JavaScript (sem dados em texto simples na página),
por isso usamos o Playwright para abrir um browser real, passar o
rato por cima de cada ponto do gráfico e ler o tooltip que aparece.

Uso:
    python3 import_historico_mercado.py
"""

import re
import sys
import json
import time
import psycopg
from datetime import datetime
from playwright.sync_api import sync_playwright


def agora():
    return datetime.now().strftime("%H:%M:%S")


def log(mensagem):
    print(f"[{agora()}] {mensagem}")


DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "football",
    "user": "scouting",
    "password": "scouting",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

PROGRESSO_FICHEIRO = "progresso_historico_mercado.json"

TEMPO_ENTRE_JOGADORES = 0.8
TEMPO_ENTRE_HOVERS = 0.25


# ============================================================
# PROGRESSO (retomável)
# ============================================================

def carregar_progresso():
    try:
        with open(PROGRESSO_FICHEIRO, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"jogadores_concluidos": []}


def guardar_progresso(progresso):
    with open(PROGRESSO_FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, indent=2, ensure_ascii=False)


def marcar_concluido(progresso, jogador_id):
    if jogador_id not in progresso["jogadores_concluidos"]:
        progresso["jogadores_concluidos"].append(jogador_id)
    guardar_progresso(progresso)


def ja_concluido(progresso, jogador_id):
    return jogador_id in progresso["jogadores_concluidos"]


# ============================================================
# CONVERSÕES
# ============================================================

def converter_valor_texto(texto):
    """
    Converte textos como "600 mil €", "9,91 M. €", "20,00 M. €"
    para um inteiro em euros. Devolve None para "-", "?" ou vazio.
    """
    if not texto:
        return None

    texto = texto.strip()

    if texto in ("-", "?", ""):
        return None

    try:
        limpo = texto.replace("€", "").replace(".", "").strip()

        if "mil" in limpo:
            numero = limpo.replace("mil", "").strip().replace(",", ".")
            return int(float(numero) * 1_000)

        if "M" in limpo:
            numero = limpo.replace("M", "").strip().replace(",", ".")
            return int(float(numero) * 1_000_000)

        return int(float(limpo.replace(",", ".")))

    except (ValueError, TypeError):
        return None


def converter_data_ddmmyyyy(texto):
    if not texto:
        return None
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def normalizar_epoca(texto):
    """
    "24/25" -> "2024/25". Assume-se sempre época 20xx.
    """
    if not texto or "/" not in texto:
        return texto

    inicio, fim = texto.split("/")

    try:
        return f"20{inicio}/{fim}"
    except ValueError:
        return texto


def url_para_slug_e_id(url_perfil):
    """
    A partir de .../<slug>/profil/spieler/<id>, devolve (slug, id).
    """
    m = re.search(r"transfermarkt\.pt/([^/]+)/profil/spieler/(\d+)", url_perfil)
    if not m:
        return None, None
    return m.group(1), m.group(2)


# ============================================================
# HISTÓRICO DE VALOR DE MERCADO (gráfico, via hover)
# ============================================================

def extrair_historico_valor_mercado(page, slug, spieler_id):
    url = f"https://www.transfermarkt.pt/{slug}/marktwertverlauf/spieler/{spieler_id}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as erro:
        # Falha a ABRIR a página (rede em baixo, DNS, etc.) não é o
        # mesmo que "jogador sem histórico" -- já aconteceu um corte
        # de internet fazer isto disfarçar-se de dado genuíno. Propaga
        # para o caller (per-jogador, ver main()) não marcar como
        # concluído.
        raise RuntimeError(f"falha ao abrir página de valor de mercado: {erro}") from erro

    try:
        page.wait_for_selector("path.voronoi-cell", timeout=8000)
    except Exception:
        # jogador sem gráfico de valor de mercado (sem histórico)
        return []

    cells = page.query_selector_all("path.voronoi-cell")

    registos = []

    for cell in cells:
        box = cell.bounding_box()
        if not box:
            continue

        page.mouse.move(
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
            steps=3,
        )
        time.sleep(TEMPO_ENTRE_HOVERS)

        texto = page.evaluate(
            """
            () => {
                const els = Array.from(document.querySelectorAll('div'));
                const alvo = els.find(el =>
                    /\\d{2}\\/\\d{2}\\/\\d{4}/.test(el.textContent) &&
                    /Valor de mercado/.test(el.textContent) &&
                    el.textContent.length < 200
                );
                return alvo ? alvo.textContent.trim() : null;
            }
            """
        )

        if not texto:
            continue

        m = re.search(
            r"(\d{2}/\d{2}/\d{4})\s*Valor de mercado:\s*([^\n]*?)\s*Clube:\s*(.*?)\s*Idade\s*:\s*(\d+)",
            texto,
        )

        if not m:
            continue

        data_texto, valor_texto, clube, idade_texto = m.groups()

        registos.append({
            "data": converter_data_ddmmyyyy(data_texto),
            "valor": converter_valor_texto(valor_texto),
            "clube": clube.strip(),
            "idade": int(idade_texto),
        })

    # remover duplicados por data (o hover pode repetir o mesmo ponto)
    vistos = set()
    unicos = []
    for r in registos:
        if r["data"] in vistos:
            continue
        vistos.add(r["data"])
        unicos.append(r)

    return unicos


# ============================================================
# HISTÓRICO DE TRANSFERÊNCIAS
# ============================================================

TIPOS_TEXTO = {"empréstimo", "fim do empréstimo", "-", "?"}


def extrair_transferencias(page, slug, spieler_id):
    url = f"https://www.transfermarkt.pt/{slug}/transfers/spieler/{spieler_id}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as erro:
        # Mesmo motivo que em extrair_historico_valor_mercado: falha
        # de rede não é "sem transferências".
        raise RuntimeError(f"falha ao abrir página de transferências: {erro}") from erro

    try:
        page.wait_for_selector('a[aria-label*=" - "][aria-label*="->"]', timeout=8000)
    except Exception:
        return []

    links = page.query_selector_all('a[aria-label*=" - "][aria-label*="->"]')

    registos = []

    for link in links:
        secao = link.evaluate_handle("el => el.closest('section')")
        texto = secao.evaluate("el => el ? el.innerText : ''")

        linhas = [l.strip() for l in texto.split("\n") if l.strip()]

        if len(linhas) < 6:
            continue

        epoca, data_texto, origem, destino, valor1_texto, valor2_texto = linhas[:6]

        tipo = "Transferência"
        valor = converter_valor_texto(valor2_texto)

        if valor2_texto.strip().lower() in TIPOS_TEXTO:
            tipo = valor2_texto.strip()
            valor = None

        registos.append({
            "epoca": normalizar_epoca(epoca),
            "data": converter_data_ddmmyyyy(data_texto),
            "clube_origem": origem,
            "clube_destino": destino,
            "tipo": tipo,
            "valor": valor,
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


def guardar_valores_mercado(conn, jogador_id, registos):
    if not registos:
        return

    with conn.cursor() as cursor:
        for r in registos:
            if not r["data"]:
                continue
            cursor.execute(
                """
                INSERT INTO valores_mercado (jogador_id, data, valor, clube, idade)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (jogador_id, data)
                DO UPDATE SET
                    valor = EXCLUDED.valor,
                    clube = EXCLUDED.clube,
                    idade = EXCLUDED.idade
                """,
                (jogador_id, r["data"], r["valor"], r["clube"], r["idade"]),
            )
    conn.commit()


def guardar_transferencias(conn, jogador_id, registos):
    if not registos:
        return

    with conn.cursor() as cursor:
        for r in registos:
            if not r["data"]:
                continue
            cursor.execute(
                """
                INSERT INTO transferencias_jogador (
                    jogador_id, clube_origem, clube_destino,
                    data_transferencia, tipo, valor, epoca
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (jogador_id, data_transferencia, clube_destino)
                DO UPDATE SET
                    clube_origem = EXCLUDED.clube_origem,
                    tipo = EXCLUDED.tipo,
                    valor = EXCLUDED.valor,
                    epoca = EXCLUDED.epoca
                """,
                (
                    jogador_id,
                    r["clube_origem"],
                    r["clube_destino"],
                    r["data"],
                    r["tipo"],
                    str(r["valor"]) if r["valor"] is not None else None,
                    r["epoca"],
                ),
            )
    conn.commit()


# ============================================================
# COOKIES (Sourcepoint CMP -- só é preciso dispensar uma vez
# por sessão de browser, fica guardado em cookies)
# ============================================================

def dispensar_cookies(page):
    try:
        page.mouse.click(443, 622)
        time.sleep(0.5)
    except Exception:
        pass


# ============================================================
# PRINCIPAL
# ============================================================

def main():
    progresso = carregar_progresso()

    conn = psycopg.connect(**DB_CONFIG)

    jogadores = obter_jogadores(conn)
    total = len(jogadores)

    log(f"Total de jogadores a processar: {total}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8"},
            viewport={"width": 1300, "height": 1400},
        )
        page = context.new_page()

        # primeira visita só para dispensar o aviso de cookies
        primeiro_slug, primeiro_id = url_para_slug_e_id(jogadores[0][1])
        if primeiro_slug:
            page.goto(
                f"https://www.transfermarkt.pt/{primeiro_slug}/marktwertverlauf/spieler/{primeiro_id}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            time.sleep(1.5)
            dispensar_cookies(page)

        processados = 0
        com_erro = []

        for indice, (jogador_id, url_perfil) in enumerate(jogadores, start=1):
            if ja_concluido(progresso, jogador_id):
                continue

            slug, spieler_id = url_para_slug_e_id(url_perfil)
            if not slug:
                marcar_concluido(progresso, jogador_id)
                continue

            log(f"[{indice}/{total}] jogador_id={jogador_id} ({slug})")

            try:
                historico_valor = extrair_historico_valor_mercado(page, slug, spieler_id)
                guardar_valores_mercado(conn, jogador_id, historico_valor)
                log(f"    valor de mercado: {len(historico_valor)} pontos guardados")

                transferencias = extrair_transferencias(page, slug, spieler_id)
                guardar_transferencias(conn, jogador_id, transferencias)
                log(f"    transferências: {len(transferencias)} guardadas")

                marcar_concluido(progresso, jogador_id)
                processados += 1

            except Exception as erro:
                log(f"    [ERRO] {erro}")
                com_erro.append(jogador_id)
                conn.rollback()

            time.sleep(TEMPO_ENTRE_JOGADORES)

        browser.close()

    conn.close()

    log(f"Concluído. Processados nesta corrida: {processados}")
    if com_erro:
        log(f"Jogadores com erro: {com_erro}")


if __name__ == "__main__":
    main()
