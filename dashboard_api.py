from __future__ import annotations

import html
import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from guardian_core import GuardianError, LiquityReader, classify_health

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="BOLD Guardian", version="0.1.0")

# Browser access is intentionally open for the MVP. For a public deployment,
# set DASHBOARD_ORIGIN to the exact frontend origin instead.
origin = os.getenv("DASHBOARD_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin] if origin != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

_reader: LiquityReader | None = None
_reader_checked_at = 0.0


def reader() -> LiquityReader:
    global _reader, _reader_checked_at
    if _reader is None:
        if not os.getenv("ETH_RPC_URL"):
            raise HTTPException(503, "ETH_RPC_URL is not configured")
        _reader = LiquityReader()
    # Deployment validation is cheap relative to the Trove read and prevents
    # silently calculating against stale/wrong contract addresses.
    if time.monotonic() - _reader_checked_at > 300:
        try:
            _reader.validate_deployments()
        except GuardianError as exc:
            raise HTTPException(503, f"Liquity deployment validation failed: {exc}") from exc
        _reader_checked_at = time.monotonic()
    return _reader


def _json_get(url: str, timeout: int = 15) -> Any:
    req = Request(url, headers={"User-Agent": "BOLD-Guardian/0.1"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _num(v: Any, digits: int = 2) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), digits)
    except (ValueError, TypeError):
        return None


def _yield_data() -> dict[str, Any]:
    url = "https://api.liquity.org/v2/website/bold-venues.json"
    data = _json_get(url)
    if not isinstance(data, list):
        raise ValueError("Liquity BOLD venue API returned an unexpected shape")
    venues: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        apr = row.get("weekly_apr")
        tvl = row.get("tvl")
        try:
            apr_pct = float(apr) * 100 if apr is not None else 0.0
        except (ValueError, TypeError):
            apr_pct = 0.0
        tvl_num = _num(tvl, 2)
        venues.append({
            "provider": str(row.get("protocol") or "Unknown"),
            "venue": str(row.get("asset") or "Unknown"),
            "apr_pct": round(apr_pct, 2),
            "tvl_usd": tvl_num,
            "link": str(row.get("link") or ""),
            "apr_available": apr is not None,
        })
    venues.sort(key=lambda x: x["apr_pct"], reverse=True)
    return {"venues": venues, "count": len(venues), "source": url}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "bold-guardian-dashboard", "version": app.version}


@app.get("/api/wallet/{wallet}")
def wallet_summary(wallet: str) -> dict[str, Any]:
    from web3 import Web3

    if not Web3.is_address(wallet):
        raise HTTPException(400, "Invalid Ethereum address")
    wallet = Web3.to_checksum_address(wallet)
    try:
        positions = reader().inspect_wallet(wallet)
    except GuardianError as exc:
        raise HTTPException(503, f"Guardian could not read Liquity data: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, "Guardian could not complete the Liquity read") from exc

    items = []
    for p in positions:
        d = p.to_dict()
        d["health"] = classify_health(p)
        items.append(d)

    total_debt = sum(Decimal(str(p.debt_bold)) for p in positions)
    total_collateral_usd = sum(
        Decimal(str(p.collateral)) * Decimal(str(p.collateral_price_usd)) for p in positions
    )
    weighted_rate = (
        sum(Decimal(str(p.debt_bold)) * Decimal(str(p.annual_interest_rate_pct)) for p in positions)
        / total_debt
        if total_debt > 0 else Decimal("0")
    )
    overall_cr = (total_collateral_usd / total_debt * Decimal("100")) if total_debt > 0 else None
    health_order = {"LIQUIDATABLE": 0, "RISKY": 1, "SICKLY": 2, "MILDLY HEALTHY": 3, "HEALTHY": 4}
    overall_health = min((classify_health(p) for p in positions), key=lambda x: health_order[x], default="NO TROVES")

    return {
        "wallet": wallet,
        "troves": items,
        "summary": {
            "trove_count": len(items),
            "total_debt_bold": float(total_debt),
            "total_collateral_usd": float(total_collateral_usd),
            "weighted_borrowing_rate_pct": round(float(weighted_rate), 3),
            "overall_collateral_ratio_pct": round(float(overall_cr), 2) if overall_cr is not None else None,
            "overall_health": overall_health,
        },
    }


@app.get("/api/yield")
def yield_data() -> dict[str, Any]:
    try:
        return _yield_data()
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch Liquity BOLD venue data: {exc}") from exc


@app.get("/api/eth")
def eth_price() -> dict[str, Any]:
    # Public read-only price endpoint. The dashboard does not expose any key.
    url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
    try:
        data = _json_get(url)
        price = data.get("ethereum", {}).get("usd")
        if price is None:
            raise ValueError("ETH price missing")
        return {"price_usd": float(price), "source": "CoinGecko"}
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch ETH price: {exc}") from exc
