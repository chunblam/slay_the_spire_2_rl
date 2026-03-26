import argparse
import json
import requests


def test_connection(host: str = "127.0.0.1", port: int = 15526) -> bool:
    base_url = f"http://{host}:{port}"
    endpoint = f"{base_url}/api/v1/singleplayer"
    print(f"Testing: {endpoint}")

    try:
        resp = requests.get(endpoint, timeout=5)
        resp.raise_for_status()
        state = resp.json()

        state_type = state.get("state_type", "?")
        floor = state.get("floor", (state.get("run") or {}).get("floor", "?"))
        gold = state.get("gold", (state.get("run") or {}).get("gold", "?"))
        available = state.get("available_actions", [])

        print("Connection OK")
        print(f"  state_type: {state_type}")
        print(f"  floor: {floor}")
        print(f"  gold: {gold}")
        print(f"  available_actions: {available}")
        print("  state preview:")
        print(json.dumps(state, ensure_ascii=False)[:600])
        return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test STS2MCP connection")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15526)
    args = parser.parse_args()
    ok = test_connection(host=args.host, port=args.port)
    raise SystemExit(0 if ok else 1)
