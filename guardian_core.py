from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, asdict
from decimal import Decimal, getcontext
from typing import Any

from web3 import Web3
from web3.exceptions import ContractLogicError

getcontext().prec = 50
LOG = logging.getLogger("bold_guardian")

# Official Liquity Ethereum mainnet deployment addresses.
# Source: https://docs.liquity.org/v2-documentation/technical-docs-and-audits
COLLATERAL_REGISTRY = Web3.to_checksum_address("0xf949982b91c8c61e952b3ba942cbbfaef5386684")

BRANCHES = {
    "WETH": {
        "index": 0,
        "trove_manager": "0x7bcb64b2c9206a5b699ed43363f6f98d4776cf5a",
        "trove_nft": "0x1a0fc0b843afd9140267d25d4e575cb37a838013",
        "borrower_operations": "0x372abd1810eaf23cb9d941bbe7596dfb2c46bc65",
        "price_feed": "0xcc5f8102eb670c89a4a3c567c13851260303c24f",
    },
    "wstETH": {
        "index": 1,
        "trove_manager": "0xa2895d6a3bf110561dfe4b71ca539d84e1928b22",
        "trove_nft": "0x857aecebf75f1012dc18e15020c97096aea31b04",
        "borrower_operations": "0xa741a32f9dcfe6adba088fd0f97e90742d7d5da3",
        "price_feed": "0xe7aa2ba9e086a379d3beb224098bc634a46e314e",
    },
    "rETH": {
        "index": 2,
        "trove_manager": "0xb2b2abeb5c357a234363ff5d180912d319e3e19e",
        "trove_nft": "0x7ae430e25b67f19b431e1d1dc048a5bcf24c0873",
        "borrower_operations": "0xe8119fc02953b27a1b48d2573855738485a17329",
        "price_feed": "0x34f1e9c7dcc279ec70d3c4488eb2d80fba8b7b2b",
    },
}

for _branch in BRANCHES.values():
    for _key in ("trove_manager", "trove_nft", "borrower_operations", "price_feed"):
        _branch[_key] = Web3.to_checksum_address(_branch[_key])

REGISTRY_ABI = [
    {"name":"totalCollaterals","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
    {"name":"getTroveManager","type":"function","stateMutability":"view","inputs":[{"name":"_index","type":"uint256"}],"outputs":[{"type":"address"}]},
]
TM_ABI = [
    {"name":"getTroveIdsCount","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
    {"name":"getTroveFromTroveIdsArray","type":"function","stateMutability":"view","inputs":[{"name":"_index","type":"uint256"}],"outputs":[{"type":"uint256"}]},
    {"name":"getLatestTroveData","type":"function","stateMutability":"view","inputs":[{"name":"_troveId","type":"uint256"}],
     "outputs":[{"name":"trove","type":"tuple","components":[
         {"name":"entireDebt","type":"uint256"},
         {"name":"entireColl","type":"uint256"},
         {"name":"redistBoldDebtGain","type":"uint256"},
         {"name":"redistCollGain","type":"uint256"},
         {"name":"accruedInterest","type":"uint256"},
         {"name":"recordedDebt","type":"uint256"},
         {"name":"annualInterestRate","type":"uint256"},
         {"name":"weightedRecordedDebt","type":"uint256"},
         {"name":"accruedBatchManagementFee","type":"uint256"},
         {"name":"lastInterestRateAdjTime","type":"uint256"},
     ]}]},
    {"name":"Troves","type":"function","stateMutability":"view","inputs":[{"name":"_id","type":"uint256"}],
     "outputs":[
         {"name":"debt","type":"uint256"},{"name":"coll","type":"uint256"},{"name":"stake","type":"uint256"},
         {"name":"status","type":"uint8"},{"name":"arrayIndex","type":"uint64"},
         {"name":"lastDebtUpdateTime","type":"uint64"},{"name":"lastInterestRateAdjTime","type":"uint64"},
         {"name":"annualInterestRate","type":"uint256"},{"name":"interestBatchManager","type":"address"},
         {"name":"batchDebtShares","type":"uint256"},
     ]},
]
NFT_ABI = [
    {"name":"ownerOf","type":"function","stateMutability":"view","inputs":[{"name":"tokenId","type":"uint256"}],"outputs":[{"type":"address"}]},
    {"name":"balanceOf","type":"function","stateMutability":"view","inputs":[{"name":"owner","type":"address"}],"outputs":[{"type":"uint256"}]},
]
BO_ABI = [
    {"name":"MCR","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
]
PRICE_ABI = [
    {"name":"fetchPrice","type":"function","stateMutability":"nonpayable","inputs":[],"outputs":[{"type":"uint256"},{"type":"bool"}]},
    {"name":"lastGoodPrice","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
]

STATUS = {0: "nonExistent", 1: "active", 2: "closedByOwner", 3: "closedByLiquidation", 4: "zombie"}
WAD = Decimal(10) ** 18


class GuardianError(RuntimeError):
    pass


class RpcError(GuardianError):
    pass


class DeploymentMismatch(GuardianError):
    pass


@dataclass(frozen=True)
class TrovePosition:
    owner: str
    branch: str
    trove_id: int
    status: str
    collateral: Decimal
    debt_bold: Decimal
    annual_interest_rate_pct: Decimal
    collateral_price_usd: Decimal
    collateral_ratio_pct: Decimal | None
    mcr_pct: Decimal
    liquidation_price_usd: Decimal | None
    distance_to_liquidation_pct: Decimal | None
    batch_manager: str | None
    price_is_fallback: bool
    block_number: int

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d


def _wad(value: int) -> Decimal:
    return Decimal(value) / WAD


def _pct_wad(value: int) -> Decimal:
    return Decimal(value) * Decimal(100) / WAD


class LiquityReader:
    """Read-only Liquity V2 Ethereum mainnet reader.

    It discovers currently active/zombie Troves from each TroveManager's active
    Trove ID array and verifies ownership against the branch TroveNFT. This
    correctly handles multiple Troves and NFT transfers.
    """

    def __init__(self, rpc_url: str | None = None, retries: int = 3):
        self.rpc_url = rpc_url or os.getenv("ETH_RPC_URL")
        if not self.rpc_url:
            raise GuardianError("ETH_RPC_URL is not set. Add an Ethereum mainnet RPC URL first.")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 30}))
        self.retries = max(1, retries)
        if not self._retry(lambda: self.w3.is_connected()):
            raise RpcError("Could not connect to the Ethereum RPC.")
        chain_id = self._retry(lambda: self.w3.eth.chain_id)
        if chain_id != 1:
            raise GuardianError(f"Wrong network: expected Ethereum mainnet chain ID 1, got {chain_id}.")
        self.registry = self.w3.eth.contract(address=COLLATERAL_REGISTRY, abi=REGISTRY_ABI)

    def _retry(self, fn):
        last = None
        for attempt in range(self.retries):
            try:
                return fn()
            except Exception as exc:
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.6 * (2 ** attempt))
        raise RpcError(f"RPC call failed after {self.retries} attempts: {last}") from last

    def validate_deployments(self) -> None:
        """Fail closed if the official registry no longer matches our verified branch map."""
        count = self._retry(lambda: self.registry.functions.totalCollaterals().call())
        if count < 3:
            raise DeploymentMismatch(f"CollateralRegistry reports only {count} collateral branches.")
        for name, cfg in BRANCHES.items():
            actual = self._retry(lambda c=cfg: self.registry.functions.getTroveManager(c["index"]).call())
            if Web3.to_checksum_address(actual) != cfg["trove_manager"]:
                raise DeploymentMismatch(
                    f"{name} TroveManager mismatch. Expected {cfg['trove_manager']}, registry returned {actual}. "
                    "Refusing to calculate risk from an unverified deployment."
                )

    def _contracts(self, branch: str):
        cfg = BRANCHES[branch]
        tm = self.w3.eth.contract(address=cfg["trove_manager"], abi=TM_ABI)
        nft = self.w3.eth.contract(address=cfg["trove_nft"], abi=NFT_ABI)
        bo = self.w3.eth.contract(address=cfg["borrower_operations"], abi=BO_ABI)
        pf = self.w3.eth.contract(address=cfg["price_feed"], abi=PRICE_ABI)
        return tm, nft, bo, pf

    def _branch_price(self, pf) -> tuple[int, bool]:
        """Try Liquity's current oracle calculation via eth_call; fall back to lastGoodPrice.

        fetchPrice is non-view on-chain, but .call() executes it locally with eth_call and
        does not send a transaction or change chain state.
        """
        try:
            price, oracle_failure = self._retry(lambda: pf.functions.fetchPrice().call())
            if int(price) <= 0:
                raise ValueError("zero price")
            return int(price), bool(oracle_failure)
        except Exception as exc:
            LOG.warning("fetchPrice eth_call failed; using lastGoodPrice: %s", exc)
            price = self._retry(lambda: pf.functions.lastGoodPrice().call())
            if int(price) <= 0:
                raise GuardianError("Liquity price feed returned no usable price.")
            return int(price), True

    def _alchemy_incoming_nft_ids(self, wallet: str, nft_address: str) -> set[int]:
        """Use Alchemy's indexed transfer API to find NFT IDs ever received by wallet.

        Current ownership is still verified with ownerOf(), so transfers out, burns,
        stale index data, and historical ownership cannot create false positives.
        """
        if "alchemy.com" not in self.rpc_url.lower():
            raise GuardianError("Alchemy indexed lookup unavailable on this RPC provider.")

        ids: set[int] = set()
        page_key = None
        while True:
            query = {
                "fromBlock": "0x0",
                "toBlock": "latest",
                "toAddress": wallet,
                "contractAddresses": [nft_address],
                "category": ["erc721"],
                "withMetadata": False,
                "excludeZeroValue": False,
                "maxCount": "0x3e8",
                "order": "asc",
            }
            if page_key:
                query["pageKey"] = page_key
            response = self._retry(
                lambda q=query: self.w3.provider.make_request("alchemy_getAssetTransfers", [q])
            )
            if not isinstance(response, dict) or response.get("error"):
                raise GuardianError(f"Alchemy transfer lookup failed: {response.get('error') if isinstance(response, dict) else response}")
            result = response.get("result") or {}
            for transfer in result.get("transfers") or []:
                raw = transfer.get("rawContract") or {}
                token = transfer.get("tokenId") or transfer.get("erc721TokenId") or raw.get("value")
                if token is None:
                    continue
                try:
                    ids.add(int(str(token), 0))
                except (TypeError, ValueError):
                    try:
                        ids.add(int(str(token), 16))
                    except (TypeError, ValueError):
                        LOG.warning("Could not parse NFT token id from Alchemy transfer: %r", token)
            page_key = result.get("pageKey")
            if not page_key:
                break
        return ids

    def _scan_branch_troves(self, wallet: str, branch: str) -> list[int]:
        """Slow provider-independent fallback used only when indexed lookup is unavailable."""
        tm, nft, _, _ = self._contracts(branch)
        count = int(self._retry(lambda: tm.functions.getTroveIdsCount().call()))
        LOG.info("%s: fallback scanning %s active/zombie Troves", branch, count)
        found: list[int] = []
        for i in range(count):
            trove_id = int(self._retry(lambda idx=i: tm.functions.getTroveFromTroveIdsArray(idx).call()))
            try:
                owner = self._retry(lambda tid=trove_id: nft.functions.ownerOf(tid).call())
            except RpcError:
                continue
            if Web3.to_checksum_address(owner) == wallet:
                found.append(trove_id)
        return found

    def find_wallet_trove_ids(self, wallet: str) -> list[tuple[str, int]]:
        """Find currently owned Troves quickly on Alchemy, with safe on-chain verification.

        First asks each TroveNFT for balanceOf(wallet). Zero balance means zero work.
        On Alchemy, indexed ERC-721 transfers give candidate token IDs in a handful of
        RPC calls. Every candidate is then verified using ownerOf(). If the indexed
        result cannot account for the wallet's on-chain NFT balance, Guardian falls
        back to the original complete branch scan rather than returning incomplete data.
        """
        wallet = Web3.to_checksum_address(wallet)
        found: list[tuple[str, int]] = []

        for branch, cfg in BRANCHES.items():
            _, nft, _, _ = self._contracts(branch)
            balance = int(self._retry(lambda n=nft: n.functions.balanceOf(wallet).call()))
            if balance == 0:
                LOG.info("%s: wallet owns no Trove NFTs", branch)
                continue

            verified: list[int] = []
            try:
                candidates = self._alchemy_incoming_nft_ids(wallet, cfg["trove_nft"])
                LOG.info("%s: indexed lookup returned %s candidate Trove NFTs", branch, len(candidates))
                for trove_id in candidates:
                    try:
                        owner = self._retry(lambda tid=trove_id, n=nft: n.functions.ownerOf(tid).call())
                    except RpcError:
                        continue
                    if Web3.to_checksum_address(owner) == wallet:
                        verified.append(trove_id)
            except GuardianError as exc:
                LOG.warning("%s: fast indexed lookup unavailable: %s", branch, exc)

            # balanceOf is the on-chain source of truth. Never silently return a
            # partial list if the index missed a transfer or token ID.
            if len(verified) != balance:
                LOG.warning("%s: expected %s owned Troves, fast lookup verified %s; using complete fallback scan", branch, balance, len(verified))
                verified = self._scan_branch_troves(wallet, branch)

            found.extend((branch, tid) for tid in verified)

        return found

    def read_position(self, wallet: str, branch: str, trove_id: int) -> TrovePosition:
        wallet = Web3.to_checksum_address(wallet)
        tm, nft, bo, pf = self._contracts(branch)

        owner = Web3.to_checksum_address(self._retry(lambda: nft.functions.ownerOf(trove_id).call()))
        if owner != wallet:
            raise GuardianError(f"Trove {trove_id} is no longer owned by {wallet}.")

        latest = self._retry(lambda: tm.functions.getLatestTroveData(trove_id).call())
        raw = self._retry(lambda: tm.functions.Troves(trove_id).call())
        mcr_raw = int(self._retry(lambda: bo.functions.MCR().call()))
        price_raw, fallback = self._branch_price(pf)
        block_number = int(self._retry(lambda: self.w3.eth.block_number))

        debt = _wad(int(latest[0]))
        coll = _wad(int(latest[1]))
        rate_pct = _pct_wad(int(latest[6]))
        status = STATUS.get(int(raw[3]), f"unknown({raw[3]})")
        batch = Web3.to_checksum_address(raw[8])
        if int(batch, 16) == 0:
            batch = None

        price = _wad(price_raw)
        mcr = _pct_wad(mcr_raw)

        cr = None
        liq_price = None
        distance = None
        if debt > 0 and coll > 0:
            cr = (coll * price / debt) * Decimal(100)
            liq_price = (debt * (Decimal(mcr_raw) / WAD)) / coll
            if price > 0:
                distance = ((price - liq_price) / price) * Decimal(100)

        return TrovePosition(
            owner=owner, branch=branch, trove_id=trove_id, status=status,
            collateral=coll, debt_bold=debt, annual_interest_rate_pct=rate_pct,
            collateral_price_usd=price, collateral_ratio_pct=cr, mcr_pct=mcr,
            liquidation_price_usd=liq_price, distance_to_liquidation_pct=distance,
            batch_manager=batch, price_is_fallback=fallback, block_number=block_number,
        )

    def inspect_wallet(self, wallet: str) -> list[TrovePosition]:
        if not Web3.is_address(wallet):
            raise GuardianError("That is not a valid Ethereum address.")
        wallet = Web3.to_checksum_address(wallet)
        self.validate_deployments()
        ids = self.find_wallet_trove_ids(wallet)
        positions = []
        for branch, trove_id in ids:
            try:
                positions.append(self.read_position(wallet, branch, trove_id))
            except (ContractLogicError, GuardianError, RpcError) as exc:
                LOG.warning("Skipping Trove %s/%s because state changed during inspection: %s", branch, trove_id, exc)
        return positions


def classify_health(position: TrovePosition) -> str:
    """Presentation-only first-pass labels. Not financial advice.

    We deliberately keep this separate from protocol facts so thresholds can be
    changed later without changing the underlying position calculations.
    """
    if position.collateral_ratio_pct is None:
        return "UNKNOWN"
    cr = position.collateral_ratio_pct
    mcr = position.mcr_pct
    if cr < mcr:
        return "LIQUIDATABLE"
    # Relative buffers above the branch's actual MCR, not hard-coded CRs.
    buffer_ratio = cr / mcr
    if buffer_ratio < Decimal("1.10"):
        return "RISKY"
    if buffer_ratio < Decimal("1.25"):
        return "SICKLY"
    if buffer_ratio < Decimal("1.50"):
        return "MILDLY HEALTHY"
    return "HEALTHY"
