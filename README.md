# Football Scouting HTML UI

Interface HTML/CSS/JavaScript com backend FastAPI para consultar PostgreSQL.

## Instalar

```bash
pip install -r requirements.txt
```

## Executar

Com o PostgreSQL ligado via Docker:

```bash
docker compose up -d
uvicorn server:app --reload
```

Abrir:

http://127.0.0.1:8000

## Variáveis opcionais

- POSTGRES_HOST=localhost
- POSTGRES_PORT=5432
- POSTGRES_DB=football
- POSTGRES_USER=scouting
- POSTGRES_PASSWORD=scouting
