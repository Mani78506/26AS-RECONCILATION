# TDS Compliance E2E UAT environment

Run an isolated backend with a dedicated database and only the browser origin required by the UAT runner:

```powershell
$env:DB_NAME = "26as_reconciliation_tds_e2e"
$env:CORS_ORIGINS = "http://127.0.0.1:3001"
python -m uvicorn server:app --host 127.0.0.1 --port 8002
```

Run the frontend with `REACT_APP_BACKEND_URL=http://127.0.0.1:8002` and `PORT=3001` through process environment variables. Public UAT supplies its public HTTPS backend origin through `REACT_APP_BACKEND_URL` and its frontend origin through `CORS_ORIGINS` or `PUBLIC_FRONTEND_ORIGIN`.

No tunnel or localhost endpoint is committed as a production default.