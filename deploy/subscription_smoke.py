"""Open real subscription streams and verify acknowledgments without reading message bodies."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

import mail_gateway as server

ROOT = Path(__file__).resolve().parents[1]


async def verify(url, token, account):
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport, mode="2026-07-28") as client:
            uri = server._inbox_uri(account)
            async with client.listen(resource_subscriptions=[uri]) as subscription:
                assert subscription.honored.resource_subscriptions == [uri]
                return {"subscription_ack": "verified", "resource_filter": "verified"}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", help="Full public connector URL")
    args = parser.parse_args()
    token = dotenv_values(ROOT / ".env")["MAIL_MCP_TOKEN"]
    accounts = list(server._config())
    assert accounts, "No accounts configured"
    results = {"backend": await verify("http://127.0.0.1:3110/mcp", token, accounts[0])}
    if args.public:
        public_token = os.environ.get("MAIL_PUBLIC_TOKEN")
        assert public_token, "Set MAIL_PUBLIC_TOKEN to a valid public OAuth access token"
        results["public"] = await verify(args.public, public_token, accounts[0])
    results.update(message_bodies_read=False, mail_sent=False)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
