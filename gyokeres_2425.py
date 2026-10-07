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

print("=== 1. Resolver liga (Portugal / Primeira Liga) ===")
d = chamar("leagues", {"country": "Portugal"})
liga = next((x["league"] for x in d["response"] if x["league"]["name"].strip().lower() == "primeira liga"), None)
liga_id = liga["id"]
print("Liga id:", liga_id)

print("\n=== 2. Resolver o Sporting CP na época 2024 ===")
d = chamar("teams", {"league": liga_id, "season": 2024, "search": "Sporting CP"})
if not d["response"]:
    # fallback: procurar em todas as equipas da liga por nome parcial
    d = chamar("teams", {"league": liga_id, "season": 2024})
    equipa = next((e for e in d["response"] if "sporting" in e["team"]["name"].lower()), None)
else:
    equipa = d["response"][0]
team_id = equipa["team"]["id"]
print("Equipa:", equipa["team"]["name"], team_id)

print("\n=== 3. Procurar o Gyokeres dentro do plantel do Sporting ===")
d = chamar("players", {"team": team_id, "search": "Gyokeres", "season": 2024})
if not d["response"]:
    d = chamar("players", {"team": team_id, "search": "Gyökeres", "season": 2024})

if not d["response"]:
    print("\n[AVISO] Não encontrado por search -- a listar o plantel todo para procurar à mão:")
    d = chamar("players", {"team": team_id, "season": 2024, "page": 1})
    for p in d["response"]:
        print(" -", p["player"]["id"], p["player"]["name"])
    raise SystemExit()

bloco = d["response"][0]
info = bloco["player"]
print(f"\n--- {info['name']} (id={info['id']}) ---")
for stats in bloco["statistics"]:
    equipa_nome = stats["team"]["name"]
    liga_nome = stats["league"]["name"]
    jogos = stats["games"]
    golos = stats["goals"]
    print(f"  {equipa_nome} / {liga_nome}: jogos={jogos.get('appearences')} minutos={jogos.get('minutes')} "
          f"rating={jogos.get('rating')} golos={golos.get('total')} assist={golos.get('assists')}")
