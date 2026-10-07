import json, requests

def load_env():
    env = {}
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

env = load_env()
API_KEY = env.get("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

def chamar(endpoint, params):
    r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    restantes = r.headers.get("x-ratelimit-requests-remaining")
    print(f"  [{endpoint} {params}] -> results={data.get('results')} restantes_hoje={restantes} errors={data.get('errors')}")
    return data

# já confirmados no teste anterior
TEAM_ID = 228       # Sporting CP
PLAYER_ID = 18979   # V. Gyokeres
LIGA_ID = 94         # Primeira Liga (normalmente é este -- confirmamos no passo 1 na mesma)
EPOCA = 2024

print("=== 1. Confirmar id da Primeira Liga ===")
d = chamar("leagues", {"country": "Portugal"})
liga = next((x["league"] for x in d["response"] if x["league"]["name"].strip().lower() == "primeira liga"), None)
LIGA_ID = liga["id"]
print("Liga id:", LIGA_ID)

print(f"\n=== 2. Últimos 5 jogos do Sporting NA LIGA, época {EPOCA}/{EPOCA+1} ===")
d = chamar("fixtures", {"team": TEAM_ID, "league": LIGA_ID, "season": EPOCA, "last": 5})
fixtures = d["response"]
for f in fixtures:
    casa = f["teams"]["home"]["name"]
    fora = f["teams"]["away"]["name"]
    golos = f["goals"]
    print(f"  - {f['fixture']['id']}  {f['fixture']['date'][:10]}  {casa} {golos['home']}-{golos['away']} {fora}")

print(f"\n=== 3. Stats do Gyökeres em cada um desses jogos ===")
resumo = []
for f in fixtures:
    fixture_id = f["fixture"]["id"]
    d = chamar("fixtures/players", {"fixture": fixture_id})
    for team_block in d["response"]:
        for p in team_block["players"]:
            if p["player"]["id"] == PLAYER_ID:
                s = p["statistics"][0]
                jogos = s["games"]
                golos = s["goals"]
                linha = {
                    "fixture_id": fixture_id,
                    "data": f["fixture"]["date"][:10],
                    "minutos": jogos.get("minutes"),
                    "titular": not jogos.get("substitute"),
                    "rating": jogos.get("rating"),
                    "golos": golos.get("total") or 0,
                    "assistencias": golos.get("assists") or 0,
                }
                resumo.append(linha)
                print(f"  {linha['data']}: min={linha['minutos']} titular={linha['titular']} "
                      f"rating={linha['rating']} golos={linha['golos']} assist={linha['assistencias']}")

print(f"\n=== 4. Eventos (substituições) desses jogos -- minuto exato entrada/saída ===")
for f in fixtures:
    fixture_id = f["fixture"]["id"]
    d = chamar("fixtures/events", {"fixture": fixture_id})
    subs_gyokeres = [
        e for e in d["response"]
        if e["type"] == "subst" and (e["player"]["id"] == PLAYER_ID or e["assist"]["id"] == PLAYER_ID)
    ]
    if subs_gyokeres:
        for e in subs_gyokeres:
            saiu = e["player"]["id"] == PLAYER_ID
            print(f"  jogo {fixture_id}: min {e['time']['elapsed']} -- "
                  f"{'SAIU (' + e['player']['name'] + ')' if saiu else 'ENTROU (' + e['assist']['name'] + ')'}")
    else:
        print(f"  jogo {fixture_id}: sem substituição envolvendo o Gyokeres")

print("\n=== RESUMO FINAL ===")
print(json.dumps(resumo, indent=2, ensure_ascii=False))
