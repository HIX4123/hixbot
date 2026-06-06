from __future__ import annotations

import unittest

from hixbot.models import ChatResult, ChatTurn, HealthStatus
from hixbot.providers import FallbackChatProvider


class FailingProvider:
    name = "failing"

    async def complete(self, messages, *, temperature=0.8, max_tokens=512):
        raise RuntimeError("boom")

    async def health(self):
        return HealthStatus(self.name, False, "boom")


class WorkingProvider:
    name = "working"

    async def complete(self, messages, *, temperature=0.8, max_tokens=512):
        return ChatResult(provider=self.name, content="ok")

    async def health(self):
        return HealthStatus(self.name, True, "ok")


class FallbackChatProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_completes_when_primary_fails(self) -> None:
        provider = FallbackChatProvider(FailingProvider(), WorkingProvider())
        result = await provider.complete([ChatTurn("user", "hello")])
        self.assertEqual(result.provider, "working fallback")
        self.assertEqual(result.content, "ok")

    async def test_health_uses_fallback_when_primary_is_down(self) -> None:
        provider = FallbackChatProvider(FailingProvider(), WorkingProvider())
        status = await provider.health()
        self.assertTrue(status.ok)
        self.assertIn("fallback=ok", status.detail)


if __name__ == "__main__":
    unittest.main()
