# Deployment: Railway + Vercel

## Railway backend

Create a Railway service from this repository. Railway uses `railway.json`.

Set these variables in Railway:

- `MONGO_URL` — MongoDB Atlas connection URI
- `DB_NAME` — production database name
- `CORS_ORIGINS` — the Vercel production URL, for example `https://your-app.vercel.app`
- `APP_ENV=production`
- `TDS_COMPLIANCE_DEV_AUTH=false`

Confirm `https://<railway-domain>/api/health` returns `200`.

## Vercel frontend

Import this repository in Vercel. Vercel uses `vercel.json`.

Set this production environment variable before deploying:

- `REACT_APP_BACKEND_URL` — Railway backend origin, without `/api`

Redeploy after changing a `REACT_APP_*` variable because Create React App embeds it at build time.

Add the final Vercel URL to Railway `CORS_ORIGINS`, then redeploy Railway.
