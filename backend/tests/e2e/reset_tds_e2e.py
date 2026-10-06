"""Safe deterministic setup for the isolated TDS E2E database."""
import os
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env.e2e")
EXPECTED_DB = "26as_reconciliation_tds_e2e"

def client_and_db():
    name = os.environ.get("DB_NAME")
    if name != EXPECTED_DB:
        raise RuntimeError("E2E RESET BLOCKED: active MongoDB database is not the isolated E2E database.")
    uri = os.environ.get("MONGO_URL")
    if not uri:
        raise RuntimeError("E2E RESET BLOCKED: MONGO_URL is required in backend/.env.e2e or the process environment.")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client[name]
    db.command("ping")
    if db.name != EXPECTED_DB:
        raise RuntimeError("E2E RESET BLOCKED: active MongoDB database is not the isolated E2E database.")
    return client, db

def reset():
    client, db = client_and_db()
    try:
        # Dedicated database guard above makes this safe; do not reuse in shared DBs.
        for collection in db.list_collection_names():
            db[collection].delete_many({})
        now = datetime.now(timezone.utc).isoformat()
        common = {"workflow":"TDS_COMPLIANCE","organization_id":"E2E_TDS_ORG","client_id":"E2E_DEMO_TDS_SERVICES","assessee_pan":"ZZZZZ9999Z","tan":"ZZZZ99999Z","financial_year":"2026-27","quarter":"Q1","status":"DRAFT","created_at":now,"updated_at":now,"version":1}
        docs = [{**common,"assignment_id":"E2E_GOLDEN_PATH","assessee_legal_name":"E2E Golden Path Services"},{**common,"assignment_id":"E2E_EXCEPTION_PATH","assessee_legal_name":"E2E Exception Path Services"}]
        db.tds_compliance_assignments.insert_many(docs)
        return {"database":db.name,"assignments":[d["assignment_id"] for d in docs]}
    finally: client.close()

if __name__ == "__main__": print(reset())