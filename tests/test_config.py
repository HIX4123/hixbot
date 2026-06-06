from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from hixbot.config import Settings


class SettingsTests(unittest.TestCase):
    def test_bot_owner_ids_are_parsed(self) -> None:
        env = {
            "DISCORD_TOKEN": "token",
            "BOT_OWNER_IDS": "10, 20",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.bot_owner_ids, (10, 20))
        self.assertTrue(settings.is_bot_owner(10))
        self.assertFalse(settings.is_bot_owner(30))


if __name__ == "__main__":
    unittest.main()
