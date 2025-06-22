#!/usr/bin/env python3
import argparse
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def main():
    parser = argparse.ArgumentParser(
        description="Simple MCP Search Client (JSON‐RPC → HTTP)")
    parser.add_argument(
        "-q", "--query",
        required=True,
        help="Search query string"
    )
    parser.add_argument(
        "-n", "--num-results",
        type=int,
        default=5,
        help="Number of search results (default: 5)"
    )
    parser.add_argument(
        "--id",
        type=int,
        default=1,
        help="JSON‐RPC request ID (default: 1)"
    )

    args = parser.parse_args()

    url = f"http://127.0.0.1:12845/mcp"
    payload = {
        "jsonrpc": "2.0",
        "method": "search",
        "params": {
            "query": args.query,
            "num_results": args.num_results
        },
        "id": args.id
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = Request(url, data=data, headers=headers, method="POST")

    try:
        with urlopen(req) as resp:
            resp_data = resp.read().decode("utf-8")
            parsed = json.loads(resp_data)
            # Pretty‐print to stdout
            print(json.dumps(parsed, indent=2))
    except HTTPError as e:
        print(f"HTTP Error: {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"URL Error: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

