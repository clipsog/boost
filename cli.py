#!/usr/bin/env python3
"""CLI for Zefame API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from zefame_client import ZefameClient

load_dotenv(Path(__file__).resolve().parent / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description="Zefame API v2 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("balance", help="Show account balance")
    sub.add_parser("services", help="List available services")

    add = sub.add_parser("order", help="Place an order")
    add.add_argument("service", type=int)
    add.add_argument("link")
    add.add_argument("quantity", type=int, nargs="?", default=None)
    add.add_argument("--runs", type=int)
    add.add_argument("--interval", type=int)
    add.add_argument("--comments")
    add.add_argument("--username")

    status = sub.add_parser("status", help="Order status")
    status.add_argument("order_id")

    args = parser.parse_args()
    client = ZefameClient()

    try:
        if args.command == "balance":
            out = client.balance()
        elif args.command == "services":
            out = client.services()
        elif args.command == "order":
            out = client.add_order(
                args.service,
                args.link,
                quantity=args.quantity,
                runs=args.runs,
                interval=args.interval,
                comments=args.comments,
                username=args.username,
            )
        elif args.command == "status":
            out = client.order_status(args.order_id)
        else:
            parser.error(f"unknown command: {args.command}")
            return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
