import time

from engine.reconciliation.source_processing_jobs import SourceProcessingJobs
from engine.reconciliation.validation_jobs import ValidationJobs


class EmptyQueue:
    def __init__(self):
        self.calls = 0

    def create_index(self, *_args, **_kwargs):
        return None

    def find_one_and_update(self, *_args, **_kwargs):
        self.calls += 1
        return None


def _assert_stop_prevents_further_queue_access(worker, queue):
    worker.start()
    deadline = time.monotonic() + 1
    while not queue.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert queue.calls
    worker.stop()
    calls_after_stop = queue.calls
    time.sleep(0.05)
    assert queue.calls == calls_after_stop


def test_books_validation_worker_stops_before_database_client_shutdown():
    queue = EmptyQueue()
    _assert_stop_prevents_further_queue_access(ValidationJobs(queue, lambda *_args: None), queue)


def test_source_processing_workers_stop_before_database_client_shutdown():
    queue = EmptyQueue()
    _assert_stop_prevents_further_queue_access(SourceProcessingJobs(queue, lambda *_args: None), queue)
