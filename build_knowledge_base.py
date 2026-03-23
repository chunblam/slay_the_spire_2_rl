"""
scripts/build_knowledge_base.py

构建 LLM 知识库
从 Spire Codex API + 内置攻略知识 构建知识库 JSON
"""

import sys
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
_scripts_parent = os.path.dirname(_ROOT)
if os.path.basename(_ROOT) == "scripts":
    sys.path.insert(0, _scripts_parent)

import argparse
from knowledge_builder import KnowledgeBuilder


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=str, default="data/knowledge_base.json",
        help="知识库输出路径"
    )
    parser.add_argument(
        "--no-codex", action="store_true",
        help="不使用 Spire Codex（当 Spire Codex 未运行时）"
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=KnowledgeBuilder.DEFAULT_CODEX_LANG,
        help="Spire Codex 语言代码，默认 zhs（简体中文）；见 Codex /api 文档 lang 参数",
    )
    parser.add_argument(
        "--codex-url",
        type=str,
        default=None,
        help="Spire Codex 根 URL，默认 localhost:8000 或环境变量 SPIRE_CODEX_URL",
    )
    args = parser.parse_args()

    builder = KnowledgeBuilder(
        output_path=args.output,
        spire_codex_url=args.codex_url,
        codex_lang=args.lang,
    )
    kb = builder.build(use_spire_codex=not args.no_codex)

    print("\n🎮 知识库构建完成!")
    print(f"输出文件: {args.output}")