# BOLD Guardian V1: Trove Inspector

This is the read-only foundation for BOLD Guardian.

## What it does now

- Connects to Ethereum mainnet through your own RPC URL.
- Validates Liquity's branch TroveManagers against the official CollateralRegistry before calculating anything.
- Scans all currently active/zombie Troves in WETH, wstETH and rETH branches.
- Checks the Trove NFT owner, so multiple Troves and transferred Troves are handled.
- Reads Liquity's `getLatestTroveData`, which includes accrued interest and redistribution gains.
- Reads the branch's actual MCR.
- Uses Liquity's own price-feed calculation through `eth_call`, with `lastGoodPrice` as a clearly marked fallback.
- Calculates collateral ratio, estimated liquidation price and distance to liquidation.
- Never signs or sends a transaction.

## Setup on Windows

1. Put these files in one folder.
2. Open PowerShell in that folder.
3. Install the dependency:

   py -m pip install -r requirements.txt

4. Get an Ethereum MAINNET RPC URL from a provider you trust.
5. Save it in Windows:

   setx ETH_RPC_URL "YOUR_RPC_URL"

6. Close PowerShell and open a new PowerShell window.
7. Test the offline logic:

   python test_guardian.py

8. Inspect a wallet:

   python inspect_wallet.py 0xYOUR_WALLET

For machine-readable output:

   python inspect_wallet.py 0xYOUR_WALLET --json

## Important

The health labels are Guardian presentation labels, not Liquity protocol states. The actual protocol facts (debt, collateral, MCR, price, CR) are kept separate.

V1 intentionally does NOT:
- sign transactions
- move funds
- recommend interest rates
- estimate redemption debt-in-front
- claim a Trove is safe based only on a label

Those come only after their data and formulas are separately verified.
