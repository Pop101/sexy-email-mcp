"""Safe live MCP and optional OAuth smoke check. Does not read or send email."""
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:3110"
PUBLIC = os.getenv("MAIL_PUBLIC_URL", "").rstrip("/")
TOOLS = {"list_accounts", "list_templates", "list_signatures", "check_mail", "list_folders", "search_messages",
         "list_messages", "read_message", "get_attachment", "prepare_email", "prepare_reply", "send_draft"}


def rpc(method, token, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    req = urllib.request.Request(BASE + "/mcp", data=json.dumps(message).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


def public(path, payload=None, headers=False):
    parsed = urllib.parse.urlsplit(PUBLIC)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    args = ["curl", "--silent", "--show-error", "--max-time", "10", "--resolve", f"{parsed.netloc}:443:127.0.0.1"]
    if headers:
        args += ["--dump-header", "-", "--output", "/dev/null"]
    if payload is not None:
        args += ["--header", "Content-Type: application/json", "--header",
                 "Accept: application/json, text/event-stream", "--data", json.dumps(payload)]
    result = subprocess.run(args + [origin + path], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, f"Public request failed: {path}"
    return result.stdout if headers else json.loads(result.stdout)


def main():
    env = dotenv_values(ROOT / ".env")
    token = env["MAIL_MCP_TOKEN"]
    with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
        assert json.load(response) == {"status": "ok"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "mail-smoke", "version": "1"}}}
    assert rpc("initialize", "incorrect-backend-token", init["params"])[0] == 401
    status, initialized = rpc("initialize", token, init["params"])
    assert status == 200 and initialized["result"]["serverInfo"]["name"] == "runbox-mail"
    status, tools = rpc("tools/list", token, request_id=2)
    actual = {entry["name"] for entry in tools["result"]["tools"]}
    expected = TOOLS - ({"send_draft"} if env.get("MAIL_ENABLE_SEND", "true").lower() in ("false", "0") else set())
    assert status == 200 and actual == expected, f"Tool mismatch: {actual ^ expected}"
    status, prompts = rpc("prompts/list", token, request_id=3)
    assert status == 200 and {x["name"] for x in prompts["result"]["prompts"]} == {"inbox_brief"}
    status, resources = rpc("resources/list", token, request_id=4)
    assert status == 200 and {x["uri"] for x in resources["result"]["resources"]} == {"mail://status"}
    status, account_response = rpc("tools/call", token, {"name": "list_accounts", "arguments": {}}, 5)
    assert status == 200 and not account_response["result"].get("isError")
    result = account_response["result"]
    accounts = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert accounts["accounts"] and all(a["configured"] for a in accounts["accounts"])
    if PUBLIC:
        parsed = urllib.parse.urlsplit(PUBLIC)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        route = parsed.path
        response = public(route, init, headers=True).lower()
        assert "401 unauthorized" in response and "www-authenticate:" in response
        assert "resource_metadata=" in response
        assert "404 not found" in public(route + "/mcp", init, headers=True).lower()
        metadata = public("/.well-known/oauth-protected-resource" + route)
        assert metadata["authorization_servers"] == [origin]
        assert public("/.well-known/oauth-authorization-server")["issuer"] == origin
    print(json.dumps({"status": "passed", "tools": sorted(actual), "prompts": ["inbox_brief"],
                      "resources": ["mail://status"], "configured_accounts": len(accounts["accounts"]),
                      "backend_auth": "verified", "public_oauth": "verified" if PUBLIC else "skipped",
                      "messages_accessed": False, "mail_sent": False}, indent=2))


if __name__ == "__main__":
    main()
