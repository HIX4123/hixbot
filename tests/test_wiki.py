from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hixbot.models import RetrievedChunk
from hixbot.wiki import WikiManager, format_retrieved_context


class WikiManagerTests(unittest.TestCase):
    def test_append_chunks_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wiki = WikiManager(Path(tmp))
            chunk = wiki.append_summary(
                123,
                "오늘은 발로란트 파티 이야기가 많았다.",
                channel_ids={10, 20},
            )
            chunks = wiki.chunks(123)
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].id, chunk.id)
            deleted = wiki.delete(123, chunk.id)
            self.assertEqual(deleted, 1)
            self.assertEqual(wiki.chunks(123), [])

    def test_format_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wiki = WikiManager(Path(tmp))
            chunk = wiki.append_summary(1, "게임 취향은 협동 게임 쪽이다.", channel_ids={2})
            text = format_retrieved_context([RetrievedChunk(chunk=chunk, score=0.8)])
            self.assertIn("score=0.80", text)
            self.assertIn("협동 게임", text)


if __name__ == "__main__":
    unittest.main()
