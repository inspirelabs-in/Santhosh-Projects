"""Temporal worker entry.

Run:  python -m grabon_intel.worker
Or:   grabon-intel worker start
"""
from __future__ import annotations

import asyncio
import signal

from temporalio.client import Client, TLSConfig
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from .config import get_settings
from .logging import configure as configure_logging
from .logging import get_logger
from .workflows import ACTIVITIES, WORKFLOWS


def build_runner() -> SandboxedWorkflowRunner:
    """Workflow sandbox tuned to pass our own pkg + its deps through.

    Our workflow modules import activity *types* (dataclasses) from
    `grabon_intel.workflows.activities`. That module transitively imports
    config/db/llm code which uses `pathlib.Path.resolve()` at module load —
    restricted inside the default sandbox. Passing the whole `grabon_intel`
    tree through is safe because the workflow files themselves stay free
    of non-deterministic calls; the activities (which DO do I/O) only ever
    run on the activity worker, never inside the sandbox.
    """
    restrictions = SandboxRestrictions.default.with_passthrough_modules(
        "grabon_intel",
        "sklearn",
        "numpy",
        "litellm",
        "httpx",
        "tenacity",
        "structlog",
        "orjson",
        "sqlalchemy",
        "pgvector",
        "pydantic",
        "pydantic_settings",
        "dotenv",
    )
    return SandboxedWorkflowRunner(restrictions=restrictions)

log = get_logger(__name__)


async def _client() -> Client:
    s = get_settings()
    return await Client.connect(
        s.temporal_address,
        namespace=s.temporal_namespace,
        tls=TLSConfig() if s.temporal_tls else False,
    )


async def serve() -> None:
    configure_logging()
    s = get_settings()
    client = await _client()
    worker = Worker(
        client,
        task_queue=s.temporal_task_queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        workflow_runner=build_runner(),
        max_concurrent_activities=8,
        max_concurrent_workflow_tasks=20,
    )
    log.info(
        "worker.start",
        address=s.temporal_address,
        namespace=s.temporal_namespace,
        task_queue=s.temporal_task_queue,
        workflows=[w.__name__ for w in WORKFLOWS],
        activities=[a.__name__ for a in ACTIVITIES],
    )

    stop = asyncio.Event()

    def _stop(*_: object) -> None:
        log.info("worker.shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # Windows ProactorEventLoop lacks add_signal_handler — fall back.
            signal.signal(sig, _stop)

    async with worker:
        await stop.wait()
    log.info("worker.stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
