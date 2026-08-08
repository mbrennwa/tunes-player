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

    def _adjust_thread_count(self) -> None:  # noqa: N802 — stdlib override
        # Keep in sync with CPython ThreadPoolExecutor._adjust_thread_count,
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
                args=(
                    weakref.ref(self, weakref_cb),
                    self._create_worker_context(),
                    self._work_queue,
                ),
            )
            t.daemon = True
            t.start()
            self._threads.add(t)
            _cft._threads_queues[t] = self._work_queue
