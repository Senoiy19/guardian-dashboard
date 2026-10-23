from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import requests
from web3 import Web3

from guardian_core import GuardianError, LiquityReader

LOG = logging.getLogger("bold_guardian.telegram")
TOKEN = os.getenv("GUARDIAN_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("GUARDIAN_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
LIQUITY_YIELDS = "https://api.liquity.org/v2/website/bold-venues.json"
MAX_MESSAGE = 4096

START_TEXT = (
    "🛡 <b>BOLD GUARDIAN</b>\n\n"
    "Monitor Liquity V2 loans, liquidation risk and BOLD yield opportunities from Telegram.\n\n"
    "<b>Commands</b>\n"
    "<code>/get 0xWallet</code> - inspect all Liquity V2 Troves owned by a wallet\n"
    "<code>/yield</code> - current BOLD yield venues\n"
    "<code>/eth</code> - Liquity's current WETH collateral price\n"
    "<code>/opportunities 0xWallet</code> - compare a wallet's borrowing rate with current BOLD yield venues\n"
    "<code>/help</code> - show this menu\n\n"
    "Guardian is read-only. Never send a private key or seed phrase."
)


def fmt_num(value, places=2):
    return "Data unavailable" if value is None else f"{value:,.{places}f}"


def fmt_pct(value):
    return "Data unavailable" if value is None else f"{value:,.2f}%"


def risk_label(cr: Decimal | None, mcr: Decimal) -> str:
    # Guardian product labels, not Liquity protocol states.
    if cr is None:
        return "UNKNOWN"
    if cr < mcr:
        return "LIQUIDATABLE"
    relative = cr / mcr
    if relative < Decimal("1.10"):
        return "RISKY"
    if relative < Decimal("1.25"):
        return "SICKLY"
    if relative < Decimal("1.50"):
        return "MILDLY HEALTHY"
    return "HEALTHY"


def telegram(method, payload):
    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(f"{API}/{method}", json=payload, timeout=40)
            data = response.json()
            if response.ok and data.get("ok"):
                return data
            last_error = RuntimeError(f"Telegram HTTP {response.status_code}: {data}")
        except Exception as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"Telegram request failed: {last_error}") from last_error


def send(chat_id, text):
    telegram("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def authorized(chat_id):
    return not ALLOWED_CHAT_ID or str(chat_id) == str(ALLOWED_CHAT_ID)


def position_block(position):
    label = risk_label(position.collateral_ratio_pct, position.mcr_pct)
    lines = [
        f"<b>{html.escape(position.branch)} Trove | {label}</b>",
        f"Trove ID: <code>{position.trove_id}</code>",
        f"Status: {html.escape(position.status)}",
        f"Collateral: {fmt_num(position.collateral, 6)} {html.escape(position.branch)}",
        f"Debt: {fmt_num(position.debt_bold)} BOLD",
        f"Interest rate: {fmt_pct(position.annual_interest_rate_pct)}",
        f"Collateral price: ${fmt_num(position.collateral_price_usd)}",
        f"Collateral ratio: {fmt_pct(position.collateral_ratio_pct)}",
        f"Liquidation threshold (MCR): {fmt_pct(position.mcr_pct)}",
        f"Estimated liquidation price: ${fmt_num(position.liquidation_price_usd)}",
        f"Price buffer to liquidation: {fmt_pct(position.distance_to_liquidation_pct)}",
    ]
    if position.batch_manager:
        lines.append(f"Interest batch manager: <code>{html.escape(position.batch_manager)}</code>")
    if position.price_is_fallback:
        lines.append("⚠️ <b>Price warning:</b> Liquity fallback price is being used.")
    lines.append(f"Ethereum block: {position.block_number}")
    return "\n".join(lines)


def reports(wallet, positions):
    header = (
        "🛡 <b>BOLD GUARDIAN</b>\n"
        f"Wallet: <code>{html.escape(wallet)}</code>\n"
        f"Liquity Troves found: {len(positions)}"
    )
    if not positions:
        return [header + "\n\nNo active or zombie Liquity V2 Troves found for this wallet."]
    output, current = [], header
    for position in positions:
        block = position_block(position)
        candidate = current + "\n\n" + block
        if len(candidate) > MAX_MESSAGE:
            output.append(current)
            current = header + "\n\n" + block
        else:
            current = candidate
    output.append(current)
    return output


def fetch_yields():
    r = requests.get(LIQUITY_YIELDS, timeout=25)
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Liquity returned no yield venues")
    return rows, datetime.now(timezone.utc)


def yield_messages(limit=None):
    rows, checked = fetch_yields()
    def apr(row):
        value = row.get("weekly_apr")
        try:
            return Decimal(str(value)) if value is not None else Decimal("0")
        except Exception:
            return Decimal("0")
    rows = sorted(rows, key=apr, reverse=True)
    if limit:
        rows = rows[:limit]
    blocks = []
    for i, row in enumerate(rows, 1):
        provider = html.escape(str(row.get("protocol") or "Unknown"))
        venue = html.escape(str(row.get("asset") or "Unknown"))
        link = str(row.get("link") or "")
        title = f'<a href="{html.escape(link, quote=True)}">{provider} | {venue}</a>' if link.startswith(("http://", "https://")) else f"{provider} | {venue}"
        a = apr(row) * 100
        tvl = row.get("tvl")
        try:
            tvl_text = f"${Decimal(str(tvl)):,.0f}" if tvl is not None else "Data unavailable"
        except Exception:
            tvl_text = "Data unavailable"
        blocks.append(f"{i}. {title}\n   APR: {a:.2f}%\n   TVL: {tvl_text}")
    header = "📊 <b>BOLD YIELD VENUES</b>\n"
    footer = f"\nLast successful check: {checked.strftime('%Y-%m-%d %H:%M UTC')}\nSource: Liquity API"
    messages, current = [], header
    for block in blocks:
        candidate = current + "\n" + block + "\n"
        if len(candidate + footer) > MAX_MESSAGE and current != header:
            messages.append(current.rstrip())
            current = "📊 <b>BOLD YIELD VENUES (continued)</b>\n\n" + block + "\n"
        else:
            current = candidate
    messages.append(current.rstrip() + footer)
    return messages, rows


def parse_wallet_command(text):
    parts = text.split()
    if len(parts) != 2 or not Web3.is_address(parts[1]):
        return None
    return Web3.to_checksum_address(parts[1])


def handle(reader, message):
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None:
        return
    if not authorized(chat_id):
        LOG.warning("Rejected unauthorized chat %s", chat_id)
        return

    command = text.split()[0].split("@")[0].lower() if text else ""

    if command in ("/start", "/help"):
        send(chat_id, START_TEXT)
        return

    if command == "/get":
        wallet = parse_wallet_command(text)
        if wallet is None:
            send(chat_id, "Usage: <code>/get 0xYourEthereumWallet</code>")
            return
        try:
            send(chat_id, f"🔎 Checking <code>{wallet[:8]}…{wallet[-6:]}</code>…")
            started = time.perf_counter()
            positions = reader.inspect_wallet(wallet)
            elapsed = time.perf_counter() - started
            for report in reports(wallet, positions):
                send(chat_id, report)
            LOG.info("/get completed in %.2fs for %s", elapsed, wallet)
        except GuardianError:
            LOG.exception("Guardian read failed")
            send(chat_id, "⚠️ Guardian couldn't complete the Liquity check. Try again shortly.")
        except Exception:
            LOG.exception("Unexpected /get failure")
            send(chat_id, "⚠️ Guardian hit an unexpected error. No position data was returned.")
        return

    if command == "/yield":
        try:
            messages, _ = yield_messages()
            for msg in messages:
                send(chat_id, msg)
        except Exception:
            LOG.exception("Yield fetch failed")
            send(chat_id, "⚠️ Couldn't fetch Liquity BOLD yield venues right now.")
        return

    if command == "/eth":
        try:
            _, _, _, pf = reader._contracts("WETH")
            price_raw, fallback = reader._branch_price(pf)
            price = Decimal(price_raw) / (Decimal(10) ** 18)
            block = reader.w3.eth.block_number
            warning = "\n⚠️ Liquity fallback price is being used." if fallback else ""
            send(chat_id, f"Ξ <b>LIQUITY WETH PRICE</b>\n\nPrice: <b>${price:,.2f}</b>\nEthereum block: {block}{warning}\n\nSource: Liquity WETH branch price feed")
        except Exception:
            LOG.exception("ETH price read failed")
            send(chat_id, "⚠️ Couldn't read Liquity's WETH collateral price right now.")
        return

    if command == "/opportunities":
        wallet = parse_wallet_command(text)
        if wallet is None:
            send(chat_id, "Usage: <code>/opportunities 0xYourEthereumWallet</code>")
            return
        try:
            send(chat_id, f"🔎 Comparing <code>{wallet[:8]}…{wallet[-6:]}</code> with current BOLD yields…")
            positions = reader.inspect_wallet(wallet)
            _, rows = yield_messages(limit=5)
            if not positions:
                send(chat_id, "No active or zombie Liquity V2 Troves found for that wallet.")
                return
            top = []
            for row in rows:
                try:
                    a = Decimal(str(row.get("weekly_apr"))) * 100 if row.get("weekly_apr") is not None else Decimal("0")
                except Exception:
                    a = Decimal("0")
                top.append(f"• {html.escape(str(row.get('protocol') or 'Unknown'))} | {html.escape(str(row.get('asset') or 'Unknown'))}: {a:.2f}%")
            loans = []
            for p in positions:
                loans.append(f"• {html.escape(p.branch)} Trove: {p.annual_interest_rate_pct:.2f}% borrowing rate | CR {fmt_pct(p.collateral_ratio_pct)}")
            send(chat_id,
                 "💡 <b>GUARDIAN OPPORTUNITY CHECK</b>\n\n"
                 "<b>Your Liquity borrowing rates</b>\n" + "\n".join(loans) +
                 "\n\n<b>Current top BOLD yield observations</b>\n" + "\n".join(top) +
                 "\n\nThese are observations, not a recommendation to borrow, move funds, or increase leverage. Yield is variable and position risk must be evaluated separately.")
        except Exception:
            LOG.exception("Opportunity check failed")
            send(chat_id, "⚠️ Couldn't complete the opportunity check right now.")
        return

    send(chat_id, "Unknown command. Send /start to see Guardian commands.")


def main():
    if not TOKEN:
        raise SystemExit("ERROR: GUARDIAN_TELEGRAM_BOT_TOKEN is not set.")
    if not os.getenv("ETH_RPC_URL"):
        raise SystemExit("ERROR: ETH_RPC_URL is not set.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    reader = LiquityReader()
    reader.validate_deployments()
    me = telegram("getMe", {})
    telegram("setMyCommands", {"commands": [
        {"command": "get", "description": "Inspect a Liquity V2 wallet"},
        {"command": "yield", "description": "Show current BOLD yield venues"},
        {"command": "eth", "description": "Show Liquity WETH collateral price"},
        {"command": "opportunities", "description": "Compare loan rates with BOLD yields"},
        {"command": "help", "description": "Show Guardian commands"},
    ]})
    LOG.info("Connected to Telegram as @%s", me["result"].get("username", "unknown"))
    LOG.info("BOLD Guardian is listening. Ctrl+C stops it.")

    offset = None
    try:
        while True:
            payload = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            try:
                result = telegram("getUpdates", payload)
                for update in result.get("result", []):
                    offset = int(update["update_id"]) + 1
                    if update.get("message"):
                        handle(reader, update["message"])
            except Exception as exc:
                LOG.error("Polling error: %s", exc)
                time.sleep(3)
    except KeyboardInterrupt:
        LOG.info("Stopped.")


if __name__ == "__main__":
    main()
