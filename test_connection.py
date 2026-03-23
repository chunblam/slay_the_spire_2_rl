"""
scripts/test_connection.py

测试与 STS2MCP Mod 的连接
运行前请确保游戏已启动并加载 Mod
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json


def test_connection(host="localhost", port=15526, api_mode="singleplayer"):
    """
    Raw API: GET /api/v1/singleplayer 或 /api/v1/multiplayer
    若 Mod 使用非默认端口，修改 port（官方文档默认 15526）。
    """
    base_url = f"http://{host}:{port}"
    path = f"/api/v1/{api_mode}"
    print(f"🔌 测试连接: {base_url}{path}")

    try:
        resp = requests.get(f"{base_url}{path}", timeout=5)
        resp.raise_for_status()
        state = resp.json()
        if isinstance(state, dict) and state.get("status") == "error":
            print(f"❌ API 错误: {state.get('error', state)}")
            return False

        st = state.get("state_type", state.get("screen_type", "?"))
        print("✅ 连接成功!")
        print(f"   state_type: {st}")
        floor = state.get("floor", "?")
        gold = state.get("gold", "?")
        pl = state.get("player")
        if isinstance(pl, dict):
            if floor == "?":
                floor = pl.get("floor", "?")
            if gold == "?":
                gold = pl.get("gold", "?")
        print(f"   楼层: {floor}")
        print(f"   金币: {gold}")

        combat = state.get("battle") or state.get("combat", {})
        if combat:
            player = combat.get("player", {})
            print(f"   玩家 HP: {player.get('hp', '?')}/{player.get('max_hp', '?')}")
            hand = combat.get("hand", [])
            print(f"   手牌: {[c.get('name', '?') for c in hand]}")

        print("\n📋 完整状态 (前500字符):")
        print(json.dumps(state, ensure_ascii=False)[:500])
        return True

    except requests.ConnectionError:
        print("❌ 连接失败! 请确认:")
        print("   1. 杀戮尖塔2 已运行")
        print("   2. STS2MCP Mod 已在游戏中启用")
        print("   3. 游戏设置中开启了 Mod")
        print(f"   4. 端口与路径正确（Raw API: GET {path}，默认端口见 Mod 说明，常见为 15526）")
        return False
    except Exception as e:
        print(f"❌ 错误: {e}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="测试 STS2MCP Raw API 连接")
    parser.add_argument("--host", default="localhost", help="主机名，建议用 localhost（避免 Invalid Hostname）")
    parser.add_argument("--port", type=int, default=15526, help="Mod HTTP 端口，默认 15526")
    parser.add_argument(
        "--api-mode",
        default="singleplayer",
        choices=["singleplayer", "multiplayer"],
        help="Raw API 路径 /api/v1/singleplayer 或 multiplayer",
    )
    args = parser.parse_args()
    success = test_connection(host=args.host, port=args.port, api_mode=args.api_mode)
    sys.exit(0 if success else 1)