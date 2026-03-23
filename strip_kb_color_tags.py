"""
一次性/可重复运行：从 data/knowledge_base.json 中移除 Godot/BBCode 颜色标签，
保留 [energy:N]、[InCombat]、{...} 等游戏语义内容。

仅处理成对标签: [gold]..[/gold]、[red]..[/red] 等，循环剥离以处理异色嵌套。
"""

import json
import os
import re
from typing import Any

# Spire Codex / 游戏内用于着色的标签名（与 docs 中 Rich Text 表一致）
_COLOR_NAMES = "gold|red|blue|green|purple|orange|pink|aqua"
_COLOR_PAIR = re.compile(
    rf"\[({_COLOR_NAMES})\]([\s\S]*?)\[/\1\]",
    re.IGNORECASE,
)


def strip_color_tags(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    prev = None
    s = text
    while prev != s:
        prev = s
        s = _COLOR_PAIR.sub(lambda m: m.group(2), s)
    return s


def walk(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(x) for x in obj]
    if isinstance(obj, str):
        return strip_color_tags(obj)
    return obj


def main() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, "data", "knowledge_base.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cleaned = walk(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print(f"已更新（已移除颜色标签）: {path}")


if __name__ == "__main__":
    main()
