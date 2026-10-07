"""
Recolhe o heatmap (pontos de toque na bola) de cada jogador que já
tem estatísticas Sofascore guardadas (estatisticas_sofascore), e
guarda em heatmap_sofascore.

Reaproveita o sofascore_id/liga_id/epoca_id já encontrados por
importar_sofascore.py -- não precisa de procurar o jogador outra
vez, só faz mais um pedido (o do heatmap) por combinação já bem
sucedida.

Corre migrar_sofascore.py primeiro (cria a tabela, se ainda não
tiver corrido nesta BD).

PEDIDOS HTTP -- usa wreq (fingerprint TLS/JA3/JA4 de Chrome real,
emulation=Chrome149) em vez de Playwright, tal como
importar_sofascore.py -- confirmado que passa a Cloudflare da
Sofascore sem precisar de abrir um browser nem visitar a homepage
primeiro.

Uso:
    python3 importar_heatmap_sofascore.py
"""

import asyncio
import json
import time
from datetime import datetime

import psycopg
from wreq import Client, Emulation

BASE_URL = "https://www.sofascore.com/api/v1"

# Perfil de emulação TLS/JA3/JA4 do wreq -- o mesmo que confirmámos que
# passa a Cloudflare da Sofascore em importar_sofascore.py.
EMULATION = Emulation.Chrome149

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "football",
    "user": "scouting",
    "password": "scouting",
}

PROGRESSO_FICHEIRO = "progresso_heatmap.json"
SLEEP_ENTRE_PEDIDOS = 1.0


def agora():
    return datetime.now().strftime("%H:%M:%S")


def log(mensagem):
    print(f"[{agora()}] {mensagem}")


def carregar_progresso():
    try:
        with open(PROGRESSO_FICHEIRO) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def guardar_progresso(feitos):
    with open(PROGRESSO_FICHEIRO, "w") as f:
        json.dump(sorted(feitos), f)


def obter_combinacoes(conn):
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT jogador_id, sofascore_id, liga_id, epoca_id
            FROM estatisticas_sofascore
            ORDER BY jogador_id
        """)
        return cursor.fetchall()


def _pedir(client, url):
    """
    Faz um único GET com o client wreq (async) a partir de código
    síncrono. Cria e fecha um event loop por pedido -- simples e
    suficiente dado o SLEEP_ENTRE_PEDIDOS entre pedidos.
    """
    async def _fazer():
        resp = await client.get(url)
        texto = await resp.text()
        return resp.status, texto

    return asyncio.run(_fazer())


def obter_heatmap(client, sofascore_id, liga_id, epoca_id):
    url = f"{BASE_URL}/player/{sofascore_id}/unique-tournament/{liga_id}/season/{epoca_id}/heatmap/overall"
    status, texto = _pedir(client, url)

    if status == 404:
        return None  # jogador sem heatmap nesta competição/época -- genuinamente vazio

    if status != 200:
        # NUNCA disfarçar um bloqueio (403 da Cloudflare, 429, 5xx) como
        # "sem dados" -- já aconteceu perder centenas de jogadores de
        # uma vez por causa disto (ver importar_sofascore.py). Propaga
        # para o chamador parar e não marcar como concluído.
        raise RuntimeError(f"HTTP {status} em {url}: {texto[:200]}")

    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        return None
    pontos = dados.get("points")
    return pontos if pontos else None


def guardar_heatmap(conn, jogador_id, liga_id, epoca_id, pontos):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO heatmap_sofascore (jogador_id, liga_id, epoca_id, pontos)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (jogador_id, liga_id, epoca_id)
            DO UPDATE SET pontos = EXCLUDED.pontos, atualizado_em = CURRENT_TIMESTAMP
            """,
            (jogador_id, liga_id, epoca_id, json.dumps(pontos)),
        )
    conn.commit()


def main():
    feitos = carregar_progresso()
    conn = psycopg.connect(**DB_CONFIG)

    combinacoes = obter_combinacoes(conn)
    total = len(combinacoes)
    log(f"Total de combinações a processar: {total}")

    guardados = 0
    sem_dados = 0

    client = Client(emulation=EMULATION)

    for indice, (jogador_id, sofascore_id, liga_id, epoca_id) in enumerate(combinacoes, start=1):
        chave = f"{jogador_id}:{liga_id}:{epoca_id}"
        if chave in feitos:
            continue

        try:
            pontos = obter_heatmap(client, sofascore_id, liga_id, epoca_id)
        except RuntimeError as erro:
            log(f"  [BLOQUEADO] jogador_id={jogador_id}: {erro}")
            log("  A parar a corrida -- o checkpoint já guardado continua válido, "
                "retoma mais tarde sem repetir trabalho.")
            break

        if pontos:
            guardar_heatmap(conn, jogador_id, liga_id, epoca_id, pontos)
            guardados += 1
            if indice % 25 == 0:
                log(f"[{indice}/{total}] jogador_id={jogador_id}: {len(pontos)} pontos")
        else:
            sem_dados += 1

        feitos.add(chave)
        guardar_progresso(feitos)
        time.sleep(SLEEP_ENTRE_PEDIDOS)

    conn.close()
    log(f"Concluído. Guardados: {guardados}, sem dados: {sem_dados}")


if __name__ == "__main__":
    main()