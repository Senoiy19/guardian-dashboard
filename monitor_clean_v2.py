"""Formatting helpers for the Liquity V2 BOLD yield monitor.

This module contains presentation only. It does not fetch data, schedule jobs,
or send Telegram messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Mapping, Optional
from urllib.parse import urlparse

TELEGRAM_MESSAGE_LIMIT = 4096


class VenueDataError(ValueError):
    pass


def _number(value, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise VenueDataError(f"{field} is missing or invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise VenueDataError(f"{field} is invalid: {value!r}") from exc
    if not result.is_finite():
        raise VenueDataError(f"{field} is not finite")
    return result


def _safe_url(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return escape(url, quote=True)


def _format_timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return "data unavailable"
    if value.tzinfo is None or value.utcoffset() is None:
        raise VenueDataError("last_successful_check must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _venue_block(index: int, entry: Mapping[str, object]) -> str:
    provider = str(entry.get("provider") or "").strip()
    venue = str(entry.get("venue") or "").strip()
    if not provider:
        raise VenueDataError(f"venue #{index} has no normalized provider")
    if not venue:
        raise VenueDataError(f"venue #{index} has no venue name")

    # Source contract retained from the existing bot: `apy` stores the source
    # weekly_apr as a decimal fraction. Rename only after source semantics are
    # independently verified.
    apr_raw = entry.get("apy")
    # Product display rule: unavailable APR is shown as 0.00%, while the
    # normalized record keeps None so monitoring can distinguish it from a
    # source-reported zero.
    apr = Decimal("0") if apr_raw is None else _number(apr_raw, "apy") * Decimal("100")
    apr_text = f"{apr:.2f}%"

    tvl_value = entry.get("tvl_usd")
    if tvl_value is None:
        tvl_text = "Data unavailable"
    else:
        try:
            tvl = _number(tvl_value, "tvl_usd")
            tvl_text = "Data unavailable" if tvl < 0 else f"${tvl:,.0f}"
        except VenueDataError:
            tvl_text = "Data unavailable"

    display = escape(f"{provider} | {venue}")
    url = _safe_url(entry.get("link"))
    title = f'<a href="{url}">{display}</a>' if url else display
    return f"{index}. {title}\n   APR: {apr_text}\n   TVL: {tvl_text}"


def format_get_messages(
    current_venues: Mapping[str, Mapping[str, object]],
    last_successful_check: Optional[datetime],
) -> list[str]:
    """Return one or more Telegram HTML messages containing every venue.

    Messages are split only at venue boundaries so Telegram's 4096-character
    limit never causes silent truncation.
    """
    sorted_entries = sorted(
        current_venues.values(),
        key=lambda entry: Decimal("0") if entry.get("apy") is None else _number(entry.get("apy"), "apy"),
        reverse=True,
    )

    header = f"📊 BOLD YIELD VENUES\nVenues tracked: {len(sorted_entries)}\n"
    footer = (
        f"\nLast successful check: {_format_timestamp(last_successful_check)}"
        "\nSource: Liquity API"
    )

    if not sorted_entries:
        return [header + "\nNo valid venue data available." + footer]

    blocks = [_venue_block(i, entry) for i, entry in enumerate(sorted_entries, 1)]
    messages: list[str] = []
    current = header

    for block in blocks:
        candidate = current + "\n" + block + "\n"
        reserve = footer if len(messages) == 0 else ""
        if len(candidate + reserve) > TELEGRAM_MESSAGE_LIMIT and current != header:
            messages.append(current.rstrip())
            current = "📊 BOLD YIELD VENUES (continued)\n\n" + block + "\n"
        else:
            current = candidate

    current = current.rstrip() + footer
    if len(current) > TELEGRAM_MESSAGE_LIMIT:
        # This should only be possible with an abnormally long single record.
        raise VenueDataError("formatted Telegram message exceeds 4096 characters")
    messages.append(current)
    return messages


def format_get_message(
    current_venues: Mapping[str, Mapping[str, object]],
    last_successful_check: Optional[datetime] = None,
) -> str:
    """Compatibility wrapper for callers that expect one string."""
    messages = format_get_messages(current_venues, last_successful_check)
    if len(messages) != 1:
        raise VenueDataError("output requires multiple Telegram messages; use format_get_messages")
    return messages[0]
