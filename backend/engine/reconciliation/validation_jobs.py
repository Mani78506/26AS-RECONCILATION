"""Persisted Books validation jobs, using the application's MongoDB and threads."""
import logging
import threading
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

log = logging.getLogger("26as")


class ValidationJobs:
    def __init__(self, collection, validate):
        self.collection, self.validate = collection, validate
        collection.create_index("job_id", unique=True)
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.worker = None

    def start(self):
        if self.worker is None:
            self.stopping.clear()
            self.worker = threading.Thread(target=self._loop, daemon=True)
            self.worker.start()

    def stop(self):
        """Stop polling before the application releases its Mongo client."""
        self.stopping.set()
        self.wake.set()
        if self.worker is not None:
            self.worker.join()
            self.worker = None

    def create(self, job_id, source, total_rows):
        now = datetime.now(timezone.utc)
        self.collection.update_one({"job_id": job_id}, {"$setOnInsert": {
            "job_id": job_id, "source": source, "status": "QUEUED",
            "created_at": now.isoformat(), "started_at": None, "completed_at": None,
            "processed_rows": 0, "total_rows": total_rows, "error": None, "result": None,
        }}, upsert=True)
        job = self.get(job_id)
        if job["source"] != source:
            raise ValueError("This validation ID belongs to different sources.")
        self.wake.set()
        return job

    def get(self, job_id):
        return self.collection.find_one({"job_id": job_id}, {"_id": 0, "lease_until": 0})

    def execute_next(self):
        now = datetime.now(timezone.utc)
        job = self.collection.find_one_and_update(
            {"$or": [{"status": "QUEUED"}, {"status": "RUNNING", "lease_until": {"$lt": now}}]},
            {"$set": {"status": "RUNNING", "started_at": now.isoformat(), "processed_rows": 0,
                      "lease_until": now + timedelta(seconds=60)}},
            return_document=ReturnDocument.AFTER,
        )
        if not job:
            return False
        query = {"job_id": job["job_id"], "started_at": job["started_at"], "status": "RUNNING"}
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(10):
                try:
                    self.collection.update_one(query, {"$set": {"lease_until": datetime.now(timezone.utc) + timedelta(seconds=60)}})
                except Exception:
                    log.exception("Validation lease renewal failed for %s", job["job_id"])

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            def progress(rows):
                self.collection.update_one(query, {"$set": {"processed_rows": rows}})
            result = self.validate(job["source"], progress)
            values = {"status": "COMPLETED", "result": result, "processed_rows": sum(f["row_count"] for f in result["files"])}
        except Exception as exc:
            log.exception("Books validation failed for %s", job["job_id"])
            values = {"status": "FAILED", "error": str(getattr(exc, "detail", None) or exc)}
        finally:
            stopped.set()
            heartbeat_thread.join()
        values["completed_at"] = datetime.now(timezone.utc).isoformat()
        self.collection.update_one(query, {"$set": values, "$unset": {"lease_until": ""}})
        return True

    def _loop(self):
        while not self.stopping.is_set():
            try:
                if self.execute_next():
                    continue
            except Exception:
                log.exception("Books validation worker could not access its queue")
            self.wake.wait(2)
            self.wake.clear()
