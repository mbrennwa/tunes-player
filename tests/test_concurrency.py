"""Tests for daemon thread-pool helpers."""

from __future__ import annotations

import threading
import time
import unittest

from tunes_player.core.concurrency import DaemonThreadPoolExecutor


class DaemonThreadPoolExecutorTests(unittest.TestCase):
    def test_workers_are_daemon_threads(self) -> None:
        seen: list[bool] = []

        def work() -> None:
            seen.append(threading.current_thread().daemon)
            time.sleep(0.05)

        with DaemonThreadPoolExecutor(max_workers=1, thread_name_prefix="t") as pool:
            pool.submit(work).result(timeout=2.0)
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
