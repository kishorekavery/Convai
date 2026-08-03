"""
The blocking-call thread pool must be explicit and shared.

Using asyncio's default executor made the in-flight request ceiling a function
of the host's core count (min(32, cpu+4)) rather than a chosen value.
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import BEDROCK_EXECUTOR_THREADS  # noqa: E402
from models import executor as executor_module  # noqa: E402


class TestSharedExecutor:
    def teardown_method(self):
        executor_module.shutdown_bedrock_executor(wait=False)

    def test_returns_a_thread_pool(self):
        assert isinstance(
            executor_module.get_bedrock_executor(), ThreadPoolExecutor
        )

    def test_same_instance_every_call(self):
        assert (
            executor_module.get_bedrock_executor()
            is executor_module.get_bedrock_executor()
        )

    def test_sized_from_configuration_not_cpu_count(self):
        pool = executor_module.get_bedrock_executor()
        assert pool._max_workers == BEDROCK_EXECUTOR_THREADS

    def test_threads_are_named_for_debuggability(self):
        pool = executor_module.get_bedrock_executor()
        assert pool._thread_name_prefix == "bedrock"

    def test_import_does_not_create_a_pool(self):
        executor_module.shutdown_bedrock_executor(wait=False)
        assert executor_module._executor is None

    def test_shutdown_then_reuse_creates_a_fresh_pool(self):
        first = executor_module.get_bedrock_executor()
        executor_module.shutdown_bedrock_executor(wait=True)
        assert executor_module.get_bedrock_executor() is not first

    def test_shutdown_is_idempotent(self):
        executor_module.get_bedrock_executor()
        executor_module.shutdown_bedrock_executor(wait=True)
        executor_module.shutdown_bedrock_executor(wait=True)  # must not raise

    def test_actually_runs_work(self):
        pool = executor_module.get_bedrock_executor()
        assert pool.submit(lambda: 6 * 7).result(timeout=5) == 42
