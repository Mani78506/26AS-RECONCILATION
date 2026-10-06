"""Persisted background source-inspection jobs for uploaded workbooks."""
import logging
import threading
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

log = logging.getLogger("26as")


class SourceProcessingJobs:
    def __init__(self, collection, inspect, workers=2):
        self.collection, self.inspect = collection, inspect
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.workers = max(1, min(int(workers), 2))
        self.worker_threads = []

    def start(self):
        if not self.worker_threads:
            self.stopping.clear()
            self.worker_threads = [threading.Thread(target=self._loop, daemon=True, name=f"source-processing-{index + 1}") for index in range(self.workers)]
            for worker in self.worker_threads:
                worker.start()

    def stop(self):
        """Stop all pollers before the application releases its Mongo client."""
        self.stopping.set()
        self.wake.set()
        for worker in self.worker_threads:
            worker.join()
        self.worker_threads = []

    def enqueue(self):
        self.wake.set()

    def execute_next(self):
        now = datetime.now(timezone.utc)
        doc = self.collection.find_one_and_update(
            {"$or": [
                {"source_processing_status": "QUEUED"},
                {"source_processing_status": "RUNNING", "source_processing_lease_until": {"$lt": now}},
            ]},
            {"$set": {"source_processing_status": "RUNNING", "source_processing_stage": "QUEUED_FOR_WORKER", "source_processing_started_at": now.isoformat(),
                      "source_processing_lease_until": now + timedelta(minutes=5)}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return False
        query = {"upload_id": doc["upload_id"], "source_processing_status": "RUNNING",
                 "source_processing_started_at": doc["source_processing_started_at"]}
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(30):
                self.collection.update_one(query, {"$set": {"source_processing_lease_until": datetime.now(timezone.utc) + timedelta(minutes=5)}})

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            def progress(stage, processed_rows=0, total_rows=None):
                self.collection.update_one(query, {"$set": {"source_processing_stage": stage, "source_processing_processed_rows": processed_rows, "source_processing_total_rows": total_rows}})
            values = self.inspect(doc, progress)
            completed = datetime.now(timezone.utc)
            values.update({"source_processing_status": "COMPLETED", "source_processing_stage": "COMPLETED", "source_processing_processed_rows": values.get("row_count", 0), "source_processing_total_rows": values.get("row_count", 0), "source_processing_completed_at": completed.isoformat(), "source_processing_elapsed_seconds": round((completed - now).total_seconds(), 3)})
        except Exception as exc:
            log.exception("Source inspection failed for %s", doc["upload_id"])
            values = {"source_processing_status": "FAILED", "source_processing_stage": "FAILED", "source_processing_error": str(exc),
                      "source_processing_completed_at": datetime.now(timezone.utc).isoformat()}
        finally:
            stopped.set()
            heartbeat_thread.join()
        self.collection.update_one(query, {"$set": values, "$unset": {"source_processing_lease_until": ""}})
        return True

    def _loop(self):
        while not self.stopping.is_set():
            try:
                if self.execute_next():
                    continue
            except Exception:
                log.exception("Source-processing worker could not access its queue")
            self.wake.wait(2)
            self.wake.clear()
