from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_videos  # noqa: E402


VALID_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>AbCdEf12345</yt:videoId>
    <title>Example video</title>
    <published>2026-08-14T00:00:00+00:00</published>
  </entry>
</feed>
"""


class BuildVideosTests(unittest.TestCase):
    def test_parse_feed_returns_sanitized_videos(self) -> None:
        self.assertEqual(
            build_videos.parse_feed(VALID_FEED),
            [
                {
                    "id": "AbCdEf12345",
                    "title": "Example video",
                    "published": "2026-08-14T00:00:00+00:00",
                }
            ],
        )

    def test_parse_feed_rejects_empty_feed(self) -> None:
        with self.assertRaises(ValueError):
            build_videos.parse_feed(
                b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )

    def test_validate_videos_deduplicates_and_drops_invalid_rows(self) -> None:
        videos = build_videos.validate_videos(
            [
                {"id": "AbCdEf12345", "title": "One"},
                {"id": "AbCdEf12345", "title": "Duplicate"},
                {"id": "bad id", "title": "Invalid"},
                "not an object",
            ]
        )
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["title"], "One")

    def test_build_uses_repository_cache_when_feed_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fallback = root / "videos.json"
            output = root / "dist" / "videos.json"
            fallback.write_text(
                json.dumps([{"id": "AbCdEf12345", "title": "Cached"}]),
                encoding="utf-8",
            )

            with mock.patch.object(
                build_videos,
                "fetch_feed",
                side_effect=RuntimeError("offline"),
            ):
                source, count = build_videos.build_output(
                    output=output,
                    feed_url="https://example.invalid/feed",
                    fallback_url=None,
                    fallback_file=fallback,
                    attempts=3,
                    timeout=1,
                )

            self.assertEqual(source, "repository-cache")
            self.assertEqual(count, 1)
            self.assertEqual(json.loads(output.read_text())[0]["title"], "Cached")

    def test_build_refuses_to_replace_site_with_empty_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fallback = root / "videos.json"
            fallback.write_text("[]", encoding="utf-8")

            with mock.patch.object(
                build_videos,
                "fetch_feed",
                side_effect=RuntimeError("offline"),
            ):
                with self.assertRaises(RuntimeError):
                    build_videos.build_output(
                        output=root / "dist" / "videos.json",
                        feed_url="https://example.invalid/feed",
                        fallback_url=None,
                        fallback_file=fallback,
                        attempts=3,
                        timeout=1,
                    )


if __name__ == "__main__":
    unittest.main()
