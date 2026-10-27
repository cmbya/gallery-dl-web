"""Loopback API trigger: acceptance is not a download-completion claim."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["trigger"])
    parser.add_argument("mode", choices=["refresh", "history"])
    parser.add_argument("--subscription-id")
    args = parser.parse_args()
    payload = {"mode": args.mode}
    if args.subscription_id:
        payload["subscription_id"] = args.subscription_id
    request = Request(
        "http://127.0.0.1:8000/api/automation/trigger",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
        print(json.dumps(result))
        return 2 if result.get("paused") else 0
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"automation trigger failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
