import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    import uvicorn.loops.asyncio as _uvloop
    _orig = _uvloop.asyncio_loop_factory
    def _proactor_factory(use_subprocess: bool = False):
        return asyncio.ProactorEventLoop
    _uvloop.asyncio_loop_factory = _proactor_factory

import uvicorn

if __name__ == "__main__":
    port = 8001 if sys.platform == "win32" else 8000
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True, loop="none")
