"""Authorized report projections for gateway, OpenAI and Anthropic HTTP shapes."""

import base64
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from endpoint_gateway.adapters.ledger import SQLiteLedger


def epoch(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required") from None
        result = parsed.timestamp()
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid timestamp")
    return result


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def attach_reports(app: FastAPI, ledger: SQLiteLedger) -> None:
    async def report(request: Request) -> JSONResponse:
        anthropic = "/organizations/" in request.url.path
        gateway = "/gateway/" in request.url.path
        costs = request.url.path.endswith(("costs", "cost_report"))
        wholesale = request.url.path.endswith("wholesale")
        key = request.headers.get("x-api-key") or request.headers.get(
            "authorization", ""
        ).removeprefix("Bearer ")
        principal = ledger.authenticate(key)

        def error(status: int, message: str) -> JSONResponse:
            kind = (
                "authentication_error"
                if status == 401
                else "permission_error"
                if status == 403
                else "invalid_request_error"
            )
            body: dict[str, Any] = {"error": {"type": kind, "message": message}}
            if anthropic:
                body["type"] = "error"
            return JSONResponse(body, status_code=status, headers={"x-should-retry": "false"})

        if principal is None:
            return error(401, "Invalid API key")
        if not principal.permits("reports") or (wholesale and not principal.permits("admin")):
            return error(403, "Insufficient reporting permission")
        if "request_id" in request.path_params:
            row = ledger.get_request(principal, request.path_params["request_id"])
            return (
                JSONResponse(row, headers={"Cache-Control": "no-store"})
                if row
                else error(404, "Request not found")
            )
        q = request.query_params
        try:
            allowed = {
                "start_time",
                "end_time",
                "starting_at",
                "ending_at",
                "bucket_width",
                "limit",
                "page",
                "group_by",
                "group_by[]",
                "api_key_ids",
                "api_key_ids[]",
                "user_ids",
                "user_ids[]",
                "models",
                "models[]",
                "project_ids",
                "project_ids[]",
                "workspace_ids",
                "workspace_ids[]",
                "outcomes",
                "outcomes[]",
            }
            if set(q) - allowed:
                raise ValueError("Unsupported report parameter")
            start_raw = q.get("start_time") or q.get("starting_at")
            if not start_raw:
                raise ValueError("Report start time is required")
            start = epoch(start_raw)
            end_raw = q.get("end_time") or q.get("ending_at")
            end = epoch(end_raw) if end_raw else float(int(time.time()) + 1)
            offset = 0
            fingerprint = hashlib.sha256(
                json.dumps(sorted((k, v) for k, v in q.multi_items() if k != "page")).encode()
            ).hexdigest()
            if q.get("page"):
                cursor = json.loads(base64.urlsafe_b64decode(q["page"]))
                if cursor["query"] != fingerprint:
                    raise ValueError("Cursor does not match query")
                end, offset = float(cursor["end"]), int(cursor["offset"])
            if not math.isfinite(end) or end <= start or end - start > 31 * 86400 or offset < 0:
                raise ValueError("Use a positive window of at most 31 days")
            limit = int(q.get("limit", "100"))
            if not 1 <= limit <= 1000:
                raise ValueError("Limit must be between 1 and 1000")
            rows = ledger.rows(principal, start, end)
            mapping = {
                "api_key_ids": "key_id",
                "user_ids": "user_id",
                "models": "model",
                "project_ids": "user_id",
                "workspace_ids": "user_id",
                "outcomes": "outcome",
            }
            for query, field in mapping.items():
                filters = q.getlist(query) + q.getlist(query + "[]")
                if filters:
                    rows = [r for r in rows if r[field] in filters]
            group = q.getlist("group_by") + q.getlist("group_by[]")
            dimensions = {
                "api_key_id": "key_id",
                "user_id": "user_id",
                "project_id": "user_id",
                "workspace_id": "user_id",
                "model": "model",
                "outcome": "outcome",
                "description": "model",
                "line_item": "model",
            }
            supported = (
                set(dimensions)
                if gateway
                else (
                    {"workspace_id", "description"}
                    if anthropic and costs
                    else {"api_key_id", "workspace_id", "model"}
                    if anthropic
                    else {"api_key_id", "user_id", "project_id", "line_item"}
                    if costs
                    else {"api_key_id", "user_id", "project_id", "model"}
                )
            )
            if set(group) - supported or len(group) != len(set(group)):
                raise ValueError("Unsupported grouping")
            if request.url.path.endswith("/requests"):
                items = rows
            else:
                width = q.get("bucket_width", "1d")
                sizes = {"1m": 60, "1h": 3600, "1d": 86400}
                if width not in sizes or (costs and width != "1d"):
                    raise ValueError("Unsupported bucket width")
                seconds = sizes[width]
                if math.ceil((end - start) / seconds) > 10000:
                    raise ValueError("Too many buckets; use a larger bucket width")
                buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for row in rows:
                    buckets[int((row["created"] - start) // seconds)].append(row)
                items = []
                for index in range(math.ceil((end - start) / seconds)):
                    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
                    for row in buckets[index]:
                        currency = (
                            row.get("wholesale_currency")
                            if wholesale
                            else row["currency"]
                            if costs
                            else None
                        )
                        groups[(*(row[dimensions[g]] for g in group), currency)].append(row)
                    results: list[dict[str, Any]] = []
                    for values, members in sorted(groups.items(), key=lambda item: str(item[0])):
                        result: dict[str, Any] = dict(zip(group, values, strict=False))
                        if costs or wholesale:
                            field = "wholesale_amount" if wholesale else "retail_amount"
                            known = [Decimal(r[field]) for r in members if r.get(field) is not None]
                            amount = sum(known, Decimal(0))
                            if not known and not gateway:
                                # Provider monetary results cannot represent an unknown charge.
                                continue
                            if gateway:
                                result.update(
                                    amount=str(amount) if known else None, currency=values[-1]
                                )
                            elif anthropic:
                                if values[-1] != "usd":
                                    raise ValueError("Use gateway reports for non-USD costs")
                                result.update(amount=str(amount * 100), currency="USD")
                            else:
                                result.update(
                                    object="organization.costs.result",
                                    amount={"value": float(amount), "currency": values[-1]},
                                )
                            result["gateway_unresolved_requests"] = sum(
                                (
                                    r["billing_status"] == "pending"
                                    if costs
                                    else r.get(field) is None
                                )
                                for r in members
                            )
                            if wholesale:
                                result["exact_amount"] = str(amount) if known else None
                                comparable = [
                                    r
                                    for r in members
                                    if r["retail_amount"] is not None
                                    and r["wholesale_amount"] is not None
                                    and r["currency"] == r["wholesale_currency"]
                                ]
                                result["known_margin"] = (
                                    str(
                                        sum(
                                            (
                                                Decimal(r["retail_amount"])
                                                - Decimal(r["wholesale_amount"])
                                                for r in comparable
                                            ),
                                            start=Decimal(0),
                                        )
                                    )
                                    if comparable
                                    else None
                                )
                                result["margin_unresolved_requests"] = len(members) - len(
                                    comparable
                                )
                                result["observations"] = [
                                    {
                                        "request_id": r["id"],
                                        "status": r["wholesale_status"],
                                        "basis": r["wholesale_basis"],
                                        "observed_at": r["reconciled"],
                                        "amount": r["wholesale_amount"],
                                        "attempts": r["attempts"],
                                    }
                                    for r in members
                                ]
                                if not known:
                                    result["amount"] = None
                        else:
                            inp = sum(r["input_tokens"] or 0 for r in members)
                            out = sum(r["output_tokens"] or 0 for r in members)
                            cached = sum(r["cached_input_tokens"] or 0 for r in members)
                            if anthropic:
                                result.update(
                                    uncached_input_tokens=inp - cached,
                                    output_tokens=out,
                                    cache_read_input_tokens=cached,
                                    cache_creation={
                                        "ephemeral_5m_input_tokens": 0,
                                        "ephemeral_1h_input_tokens": 0,
                                    },
                                )
                            else:
                                result.update(
                                    object="organization.usage.completions.result",
                                    input_tokens=inp,
                                    input_cached_tokens=cached,
                                    output_tokens=out,
                                    num_model_requests=len(members),
                                )
                            result["gateway_unresolved_requests"] = sum(
                                r["input_tokens"] is None for r in members
                            )
                        results.append(result)
                    unresolved = sum(
                        r["billing_status"] == "pending"
                        if costs
                        else r.get("wholesale_amount") is None
                        if wholesale
                        else r["input_tokens"] is None
                        for r in buckets[index]
                    )
                    low, high = start + index * seconds, min(end, start + (index + 1) * seconds)
                    items.append(
                        {
                            "starting_at": iso(low),
                            "ending_at": iso(high),
                            "results": results,
                            "gateway_unresolved_requests": unresolved,
                        }
                        if anthropic
                        else {
                            "object": "bucket",
                            "start_time": int(low),
                            "end_time": int(high),
                            "results": results,
                            "gateway_unresolved_requests": unresolved,
                        }
                    )
            selected = items[offset : offset + limit]
            more = offset + limit < len(items)
            token = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {"query": fingerprint, "end": end, "offset": offset + limit}
                    ).encode()
                ).decode()
                if more
                else None
            )
            body = {"data": selected, "has_more": more, "next_page": token}
            if not anthropic:
                body["object"] = "page"
            return JSONResponse(
                body,
                headers={
                    "Cache-Control": "no-store",
                    "X-Gateway-Accounting-As-Of": iso(time.time()),
                },
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            return error(
                400,
                "Invalid report query; use supported filters, cursor and a window up to 31 days",
            )

    for path in [
        "/v1/organization/usage/completions",
        "/v1/organization/costs",
        "/v1/organizations/usage_report/messages",
        "/v1/organizations/cost_report",
        "/v1/gateway/requests",
        "/v1/gateway/requests/{request_id}",
        "/v1/gateway/usage",
        "/v1/gateway/costs",
        "/v1/gateway/wholesale",
    ]:
        app.add_api_route(path, report, methods=["GET"])
