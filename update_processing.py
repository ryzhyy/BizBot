import asyncio

from telegram.ext import BaseUpdateProcessor


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Different users are handled concurrently, but one user's updates
    still run strictly in order.

    By default python-telegram-bot handles every update one after another,
    so while the AI answers one client (1-3 s at OpenAI) everyone else
    waits. Plain concurrent_updates=True would fix that but let two quick
    messages from the same user race over their user_data and
    ConversationHandler state, so updates are serialized per user.
    """

    def __init__(self, max_concurrent_updates=256):
        # Updates queued behind their own user's lock still hold a slot,
        # so the limit is generous enough that one user flooding the bot
        # can't take every slot and stall everybody else.
        super().__init__(max_concurrent_updates)
        self._locks = {}

    @staticmethod
    def _key(update):
        user = getattr(update, "effective_user", None)
        if user is not None:
            return ("user", user.id)

        chat = getattr(update, "effective_chat", None)
        if chat is not None:
            return ("chat", chat.id)

        return None

    async def do_process_update(self, update, coroutine):
        key = self._key(update)

        if key is None:
            await coroutine
            return

        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = [asyncio.Lock(), 0]

        # Reference count, so locks of users who went quiet are dropped
        # instead of piling up for every user the bot has ever seen.
        entry[1] += 1
        try:
            async with entry[0]:
                await coroutine
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self._locks[key]

    async def initialize(self):
        pass

    async def shutdown(self):
        pass
