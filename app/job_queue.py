import json
from dataclasses import dataclass

import redis.asyncio as aioredis

QUEUE_KEY = "trends:queue"
DEAD_KEY = "trends:dead"


@dataclass
class Job:
    keyword: str          # для compare-джобов — ключи через запятую, отсортированные
    geo: str
    timeframe: str
    category: int = 0
    gprop: str = ""
    attempt: int = 0
    kind: str = "series"  # series | compare | regions | related

    def encode(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def decode(cls, raw: str | bytes) -> "Job":
        return cls(**json.loads(raw))

    @property
    def inflight_key(self) -> str:
        return f"trends:inflight:{self.kind}:{self.keyword}:{self.geo}:{self.timeframe}:{self.category}:{self.gprop}"


class JobQueue:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._redis_url = redis_url
        self._r: aioredis.Redis | None = None

    async def connect(self):
        self._r = aioredis.from_url(self._redis_url, decode_responses=True)

    async def close(self):
        if self._r:
            await self._r.aclose()

    async def push(self, jobs: list[Job]):
        if not jobs:
            return
        pipe = self._r.pipeline()
        for job in jobs:
            pipe.lpush(QUEUE_KEY, job.encode())
        await pipe.execute()

    async def push_unique(self, jobs: list[Job], ttl: int = 300) -> int:
        """Push jobs skipping ones already in-flight. Returns number actually pushed."""
        pushed = 0
        for job in jobs:
            added = await self._r.set(job.inflight_key, 1, ex=ttl, nx=True)
            if added:
                await self._r.lpush(QUEUE_KEY, job.encode())
                pushed += 1
        return pushed

    async def clear_inflight(self, job: Job):
        """Remove the dedup marker so the same job can be enqueued again."""
        await self._r.delete(job.inflight_key)

    async def pop(self, timeout: int = 0) -> Job | None:
        result = await self._r.brpop(QUEUE_KEY, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return Job.decode(raw)

    async def push_dead(self, job: Job, reason: str):
        payload = {"job": job.__dict__, "reason": reason}
        await self._r.lpush(DEAD_KEY, json.dumps(payload))

    async def size(self) -> int:
        return await self._r.llen(QUEUE_KEY)

    async def dead_size(self) -> int:
        return await self._r.llen(DEAD_KEY)