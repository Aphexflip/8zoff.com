#!/usr/bin/env python3
"""Build a validated videos.json with a last-known-good fallback."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable


DEFAULT_FEED_URL = (
    "https://www.youtube.com/feeds/videos.xml?channel_id="
    "UCfa5-QhuRmRa7eMIx9fsepQ"
)
DEFAULT_FALLBACK_URL = "https://8zoff.com/videos.json"
USER_AGENT = "8zoff-site-builder/2.0 (+https://8zoff.com)"
MAX_RESPONSE_BYTES = 2_000_000
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def validate_videos(value: object) -> list[dict[str, str]]:
    """Return a sanitized, non-empty, de-duplicated video list."""
    if not isinstance(value, list):
        raise ValueError("video payload must be a JSON array")

    videos: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in value:
        if not isinstance(item, dict):
            continue

        video_id = str(item.get("id", "")).strip()
        if not VIDEO_ID_PATTERN.fullmatch(video_id) or video_id in seen:
            continue

        seen.add(video_id)
        videos.append(
            {
                "id": video_id,
                "title": str(item.get("title", "Zoff Video")).strip()[:500]
                or "Zoff Video",
                "published": str(item.get("published", "")).strip()[:80],
            }
        )

    if not videos:
        raise ValueError("video payload contains no valid videos")

    return videos[:50]


def parse_feed(xml_data: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_data)
    videos = []

    for entry in root.findall("atom:entry", NAMESPACES):
        videos.append(
            {
                "id": entry.findtext(
                    "yt:videoId", default="", namespaces=NAMESPACES
                ),
                "title": entry.findtext(
                    "atom:title", default="", namespaces=NAMESPACES
                ),
                "published": entry.findtext(
                    "atom:published", default="", namespaces=NAMESPACES
                ),
            }
        )

    return validate_videos(videos)


def request_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/atom+xml, application/json;q=0.9, */*;q=0.1",
            "User-Agent": USER_AGENT,
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)

    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("remote response exceeded the size limit")

    return data


def fetch_feed(
    url: str,
    *,
    attempts: int,
    timeout: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[dict[str, str]]:
    errors: list[str] = []

    for attempt in range(1, attempts + 1):
        try:
            return parse_feed(request_bytes(url, timeout))
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}")
            if attempt < attempts:
                sleeper(min(2 ** attempt, 10))

    raise RuntimeError("feed fetch failed (" + ", ".join(errors) + ")")


def load_cached_bytes(data: bytes) -> list[dict[str, str]]:
    return validate_videos(json.loads(data.decode("utf-8")))


def load_cached_url(url: str, timeout: float) -> list[dict[str, str]]:
    return load_cached_bytes(request_bytes(url, timeout))


def load_cached_file(path: Path) -> list[dict[str, str]]:
    return load_cached_bytes(path.read_bytes())


def write_output(path: Path, videos: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(videos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_output(
    *,
    output: Path,
    feed_url: str,
    fallback_url: str | None,
    fallback_file: Path | None,
    attempts: int,
    timeout: float,
) -> tuple[str, int]:
    try:
        videos = fetch_feed(
            feed_url,
            attempts=attempts,
            timeout=timeout,
        )
        source = "youtube-feed"
    except RuntimeError as exc:
        print(f"::warning::{exc}; trying last-known-good data", file=sys.stderr)
        videos = []
        source = ""

        if fallback_url:
            try:
                videos = load_cached_url(fallback_url, timeout)
                source = "deployed-cache"
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as fallback_exc:
                print(
                    "::warning::deployed cache unavailable "
                    f"({type(fallback_exc).__name__})",
                    file=sys.stderr,
                )

        if not videos and fallback_file and fallback_file.is_file():
            try:
                videos = load_cached_file(fallback_file)
                source = "repository-cache"
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as fallback_exc:
                print(
                    "::warning::repository cache unavailable "
                    f"({type(fallback_exc).__name__})",
                    file=sys.stderr,
                )

        if not videos:
            raise RuntimeError(
                "no valid remote feed or last-known-good video cache is available"
            ) from exc

    write_output(output, videos)
    return source, len(videos)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/videos.json"))
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL)
    parser.add_argument("--fallback-url", default=DEFAULT_FALLBACK_URL)
    parser.add_argument("--fallback-file", type=Path, default=Path("videos.json"))
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.attempts < 1 or args.attempts > 5:
        raise SystemExit("--attempts must be between 1 and 5")
    if args.timeout <= 0 or args.timeout > 60:
        raise SystemExit("--timeout must be greater than 0 and no more than 60")

    source, count = build_output(
        output=args.output,
        feed_url=args.feed_url,
        fallback_url=args.fallback_url,
        fallback_file=args.fallback_file,
        attempts=args.attempts,
        timeout=args.timeout,
    )
    print(f"Built {count} videos from {source}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
