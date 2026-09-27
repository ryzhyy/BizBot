"""Per-user cap on messages that cost an OpenAI call.

Every free-text message a client sends triggers one or two paid model
calls; without a cap a single user (or script) flooding the bot would
burn the whole budget. A person chatting normally never gets near it.
"""

import time
from collections import deque

AI_MESSAGES_PER_WINDOW = 15
WINDOW_SECONDS = 60
# Upper bound on tracked users so memory can't grow without limit.
MAX_TRACKED_USERS = 20_000


class SlidingWindowLimiter:
    def __init__(self, limit, window_seconds, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits = {}

    def allow(self, key):
        now = self.clock()
        hits = self._hits.get(key)

        if hits is None:
            if len(self._hits) >= MAX_TRACKED_USERS:
                self._forget_idle(now)
            hits = self._hits[key] = deque()

        while hits and now - hits[0] >= self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            return False

        hits.append(now)
        return True

    def _forget_idle(self, now):
        for key in [
            key for key, hits in self._hits.items()
            if not hits or now - hits[-1] >= self.window
        ]:
            del self._hits[key]


ai_limiter = SlidingWindowLimiter(AI_MESSAGES_PER_WINDOW, WINDOW_SECONDS)
