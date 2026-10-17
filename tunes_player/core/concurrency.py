"""Concurrency helpers.

``concurrent.futures.ThreadPoolExecutor`` workers are non-daemon by default, so
in-flight network work (catalog enrich, discover fetches) can keep the process
alive after ``Adw.Application.quit`` / ``do_shutdown`` finishes. Use
``DaemonThreadPoolExecutor`` for background pools that must not block exit.
"""

from __future__ import annotations

import concurrent.futures
import concurrent.futures.thread as _cft
import threading
import weakref


class DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """Like ``ThreadPoolExecutor``, but worker threads are daemons."""

    def _worker_args(self, executor_reference: weakref.ReferenceType) -> tuple:
        """Arguments for ``concurrent.futures.thread._worker``.

        3.14 passes a worker context. 3.11–3.13 pass the queue, initializer,
        and initargs. ``_create_worker_context`` exists only on 3.14+.
        """
        if hasattr(self, "_create_worker_context"):
            return (
                executor_reference,
                self._create_worker_context(),
                self._work_queue,
            )
        return (
            executor_reference,
            self._work_queue,
            self._initializer,
            self._initargs,
        )

    def _adjust_thread_count(self) -> None:  # noqa: N802 — stdlib override
        # Same shape as CPython ThreadPoolExecutor._adjust_thread_count,
        # with ``t.daemon = True`` before start.
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
            t = threading.Thread(
                name=thread_name,
                target=_cft._worker,
                args=self._worker_args(weakref.ref(self, weakref_cb)),
            )
            t.daemon = True
            t.start()
            self._threads.add(t)
            _cft._threads_queues[t] = self._work_queue
