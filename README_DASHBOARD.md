# BOLD Guardian Dashboard MVP

Read-only dashboard built on the existing BOLD Guardian V2 core.

## Features
- Inspect a Liquity V2 wallet by address.
- Shows all discovered Troves with branch, health label, collateral, debt, borrowing rate, CR, liquidation price and price buffer.
- Shows wallet-level debt, collateral value, weighted borrowing rate and worst-position health.
- Shows current BOLD yield venues from Liquity's official BOLD venue API, including APR, TVL and direct venue links.
- Does not hold private keys or sign transactions.

## Run locally
Set `ETH_RPC_URL`, then:

```text
pip install -r requirements.txt
uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

## Railway
Use the same `ETH_RPC_URL` variable already used by Guardian.

Start command:

```text
uvicorn dashboard_api:app --host 0.0.0.0 --port $PORT
```

No Telegram token is required for the dashboard.

## Important MVP notes
The health labels are Guardian presentation labels, not official Liquity classifications. The existing Guardian core's thresholds are reused.

The dashboard uses the same Trove-reading core as the Telegram Guardian, so the two interfaces do not maintain separate risk calculations.

## Rate Intelligence V1

Each discovered Trove now includes read-only loan-rate analysis:
- current annual and monthly-equivalent BOLD interest cost
- branch-local debt estimated to be ahead in Liquity V2 redemption ordering
- Aggressive / Balanced / Defensive Guardian heuristic rates
- estimated annual interest change versus the current rate
- a "What needs your attention" summary

The three Guardian modes are product heuristics, not Liquity protocol recommendations. They target roughly 10% / 25% / 50% of branch debt ahead in rate ordering. Liquity V2 redemptions prioritize lower interest rates; equal-rate ordering uses last-in-first-redeemed. Cross-branch redemption routing also depends on outside debt, so branch debt-ahead is context rather than a guarantee of safety.

The branch market snapshot is cached for 300 seconds by default (`RATE_CACHE_SECONDS`). The first wallet lookup after cache expiry can be slower because Guardian reads the active branch Troves to build the rate market.
