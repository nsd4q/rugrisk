"""Minimal Solana JSON-RPC client. Works with any RPC; Helius also enables DAS (metadata)."""
import base64
import os
import time

import requests


class RpcError(Exception):
    pass


class Rpc:
    def __init__(self, url: str | None = None):
        key = os.getenv("HELIUS_API_KEY")
        self.url = (
            url
            or os.getenv("SOLANA_RPC_URL")
            or (f"https://mainnet.helius-rpc.com/?api-key={key}" if key else "https://api.mainnet-beta.solana.com")
        )
        self.is_helius = "helius" in self.url
        self.http = requests.Session()

    def call(self, method: str, params, retries: int = 2):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(retries + 1):
            r = self.http.post(self.url, json=payload, timeout=15)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                err = body["error"]
                raise RpcError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
            return body["result"]
        raise RpcError("RPC rate limited")

    # ---- helpers -------------------------------------------------------
    def get_mint(self, mint: str):
        """Returns (parsed_info, program) or raises RpcError if not a token mint."""
        value = self.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])["value"]
        if value is None:
            raise RpcError("Account not found on-chain")
        data = value.get("data")
        parsed = data.get("parsed") if isinstance(data, dict) else None
        if not parsed or parsed.get("type") != "mint":
            raise RpcError("Address is not a token mint")
        return parsed["info"], data.get("program", "spl-token")

    def largest_accounts(self, mint: str):
        return self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])["value"]

    def multiple_accounts(self, addresses: list[str]):
        if not addresses:
            return []
        return self.call("getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}])["value"]

    def get_asset(self, mint: str):
        """Helius DAS only."""
        return self.call("getAsset", {"id": mint})

    def get_raw_account(self, address: str):
        """(owner_program, data_bytes) or None."""
        value = self.call("getAccountInfo", [address, {"encoding": "base64"}])["value"]
        if value is None:
            return None
        return value["owner"], base64.b64decode(value["data"][0])

    def get_balance(self, address: str) -> int:
        return self.call("getBalance", [address])["value"]

    def simulate(self, tx_base64: str) -> dict:
        """Simulate an unsigned/partially-signed transaction against current chain state."""
        return self.call("simulateTransaction", [tx_base64, {
            "encoding": "base64", "replaceRecentBlockhash": True, "commitment": "processed",
        }])["value"]
