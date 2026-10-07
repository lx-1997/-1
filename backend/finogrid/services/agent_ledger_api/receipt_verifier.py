"""Base USDC receipt verification for x402 payments.

The verifier is deliberately synchronous internally because web3.py's HTTP
provider is synchronous. The middleware calls the async wrapper in a worker
thread so a slow RPC cannot block the event loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

USDC_DECIMALS = 6
BASE_MAINNET_CHAIN_ID = 8453
TRANSFER_TOPIC_HEX = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@dataclass(frozen=True)
class ReceiptVerification:
    valid: bool
    reason: str
    tx_hash: str
    block_number: int | None = None
    confirmations: int = 0
    amount_usdc: Decimal | None = None


def _failure(tx_hash: str, reason: str) -> ReceiptVerification:
    return ReceiptVerification(valid=False, reason=reason, tx_hash=tx_hash)


def _topic_address(topic: Any) -> str:
    raw = topic.hex() if hasattr(topic, "hex") else str(topic)
    raw = raw.removeprefix("0x")
    return "0x" + raw[-40:].lower()


def _data_uint256(data: Any) -> int:
    raw = data.hex() if hasattr(data, "hex") else str(data)
    return int(raw.removeprefix("0x") or "0", 16)


def verify_usdc_receipt(
    *,
    tx_hash: str,
    pay_to: str,
    amount_usdc: str | Decimal,
    rpc_url: str,
    usdc_contract: str,
    min_confirmations: int = 12,
    rpc_timeout_seconds: int = 10,
) -> ReceiptVerification:
    """Verify a successful Base mainnet USDC transfer to ``pay_to``."""
    tx_hash = str(tx_hash or "").strip()
    if len(tx_hash) != 66 or not tx_hash.startswith("0x"):
        return _failure(tx_hash, "Invalid transaction hash")
    try:
        requested = Decimal(str(amount_usdc))
        if requested <= 0:
            return _failure(tx_hash, "Payment amount must be positive")
        required_raw = int(requested * (10 ** USDC_DECIMALS))
    except (InvalidOperation, ValueError, TypeError):
        return _failure(tx_hash, "Invalid payment amount")
    if required_raw <= 0:
        return _failure(tx_hash, "Payment amount is below token precision")

    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": rpc_timeout_seconds}))
        if not w3.is_connected():
            return _failure(tx_hash, "RPC is unavailable")
        if int(w3.eth.chain_id) != BASE_MAINNET_CHAIN_ID:
            return _failure(tx_hash, "RPC is not Base mainnet")

        contract = w3.to_checksum_address(usdc_contract)
        recipient = w3.to_checksum_address(pay_to).lower()
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt is None or int(receipt.get("status", 0)) != 1:
            return _failure(tx_hash, "Transaction is not mined successfully")
        block_number = int(receipt.get("blockNumber") or 0)
        latest = int(w3.eth.block_number)
        confirmations = max(0, latest - block_number + 1)
        if confirmations < max(1, int(min_confirmations)):
            return ReceiptVerification(
                valid=False,
                reason=f"Insufficient confirmations ({confirmations}/{min_confirmations})",
                tx_hash=tx_hash,
                block_number=block_number,
                confirmations=confirmations,
            )

        transferred_raw = 0
        for log in receipt.get("logs", []):
            if str(log.get("address", "")).lower() != contract.lower():
                continue
            topics = log.get("topics") or []
            if len(topics) < 3 or topics[0].hex().removeprefix("0x").lower() != TRANSFER_TOPIC_HEX:
                continue
            if _topic_address(topics[2]) != recipient:
                continue
            transferred_raw += _data_uint256(log.get("data", "0x0"))

        if transferred_raw < required_raw:
            return ReceiptVerification(
                valid=False,
                reason="USDC transfer amount or recipient does not match",
                tx_hash=tx_hash,
                block_number=block_number,
                confirmations=confirmations,
                amount_usdc=Decimal(transferred_raw) / Decimal(10 ** USDC_DECIMALS),
            )
        return ReceiptVerification(
            valid=True,
            reason="confirmed",
            tx_hash=tx_hash,
            block_number=block_number,
            confirmations=confirmations,
            amount_usdc=Decimal(transferred_raw) / Decimal(10 ** USDC_DECIMALS),
        )
    except Exception as exc:  # noqa: BLE001
        return _failure(tx_hash, f"Receipt verification failed: {exc}")


async def verify_usdc_receipt_async(**kwargs: Any) -> ReceiptVerification:
    import asyncio

    return await asyncio.to_thread(verify_usdc_receipt, **kwargs)
