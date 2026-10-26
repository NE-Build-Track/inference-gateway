"""Local administrative CLI. Database access is the administrative trust boundary."""

import argparse
import json
import os
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

from endpoint_gateway.adapters.ledger import SQLiteLedger
from endpoint_gateway.core.accounting import Price


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gateway user, API key and retail accounting administration"
    )
    parser.add_argument("--db", default=os.getenv("GATEWAY_ACCOUNTING_DB"))
    areas = parser.add_subparsers(dest="area", required=True)
    users = areas.add_parser("users").add_subparsers(dest="action", required=True)
    users.add_parser("create").add_argument("name")
    users.add_parser("list")
    users.add_parser("disable").add_argument("user_id")
    keys = areas.add_parser("keys").add_subparsers(dest="action", required=True)
    create = keys.add_parser("create")
    create.add_argument("user_id")
    create.add_argument("--name", default="default")
    create.add_argument("--scopes", default="inference")
    create.add_argument("--expires", type=float, help="Expiry as UTC Unix seconds")
    keys.add_parser("list").add_argument("--user-id")
    keys.add_parser("revoke").add_argument("key_id")
    keys.add_parser("rotate").add_argument("key_id")
    prices = areas.add_parser("prices").add_subparsers(dest="action", required=True)
    prices.add_parser("list")
    price = prices.add_parser("set")
    for field in ["model", "version", "input-per-million", "output-per-million"]:
        price.add_argument("--" + field, required=True)
    price.add_argument("--currency", default="usd")
    price.add_argument("--cached-input-per-million")
    adjust = areas.add_parser("adjust")
    for field in ["request-id", "amount", "reason", "operation-id"]:
        adjust.add_argument("--" + field, required=True)
    accounting = areas.add_parser("accounting").add_subparsers(dest="action", required=True)
    accounting.add_parser("retry").add_argument("request_id")
    args = parser.parse_args()
    if not args.db:
        parser.error("Set GATEWAY_ACCOUNTING_DB or --db")
    try:
        ledger = SQLiteLedger(args.db)
        result: Any = {"ok": True}
        if args.area == "users":
            if args.action == "create":
                result = {"user_id": ledger.create_user(args.name)}
            elif args.action == "list":
                result = ledger.users()
            else:
                ledger.disable_user(args.user_id)
        elif args.area == "keys":
            if args.action == "create":
                kid, secret = ledger.create_key(
                    args.user_id, args.name, frozenset(args.scopes.split(",")), args.expires
                )
                result = {"key_id": kid, "secret": secret}
            elif args.action == "rotate":
                kid, secret = ledger.rotate_key(args.key_id)
                result = {"key_id": kid, "secret": secret}
            elif args.action == "list":
                result = ledger.keys(args.user_id)
            else:
                ledger.revoke_key(args.key_id)
        elif args.area == "prices":
            if args.action == "list":
                result = ledger.prices()
            else:
                ledger.set_price(
                    Price(
                        args.version,
                        args.model,
                        args.currency,
                        Decimal(args.input_per_million),
                        Decimal(args.output_per_million),
                        Decimal(args.cached_input_per_million)
                        if args.cached_input_per_million is not None
                        else None,
                    )
                )
        elif args.area == "accounting":
            ledger.retry_reconciliation(args.request_id)
        else:
            ledger.adjustment(args.request_id, Decimal(args.amount), args.reason, args.operation_id)
        print(json.dumps(result))
    except (ValueError, sqlite3.Error, InvalidOperation):
        parser.exit(
            2,
            "Invalid operation; check IDs, scopes, prices and database permissions.\n",
        )


if __name__ == "__main__":
    main()
