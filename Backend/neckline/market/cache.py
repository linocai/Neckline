"""相同请求合并，失败单独使用更短的缓存时间。"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import time


class RequestCache:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.values: OrderedDict = OrderedDict()
        self.pending: dict = {}

    async def get(self, key, fetch, ttl, wait_timeout=None, failure_ttl=None):
        cached = self.values.get(key)
        limit = failure_ttl if cached and cached[2] and failure_ttl is not None else ttl
        if cached and time.monotonic() - cached[0] < limit:
            if cached[2]:
                raise cached[1]
            return cached[1], True
        task = self.pending.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.create_task(self._load(key, fetch))
            self.pending[key] = task
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        waiter = asyncio.shield(task)
        result = await waiter if wait_timeout is None else await asyncio.wait_for(waiter, wait_timeout)
        return result, shared

    async def _load(self, key, fetch):
        try:
            try:
                result = await fetch()
            except Exception as error:
                self._store(key, error, failed=True)
                raise
            self._store(key, result, failed=False)
            return result
        finally:
            self.pending.pop(key, None)

    def _store(self, key, value, failed: bool) -> None:
        self.values[key] = (time.monotonic(), value, failed)
        self.values.move_to_end(key)
        while len(self.values) > self.capacity:
            self.values.popitem(last=False)

    async def close(self) -> None:
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
