#!/usr/bin/env python3
"""Install the bundled skill into a DSH skill root. Standard library only."""
import argparse
import os
import shutil
import sys
from pathlib import Path


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="安装 Zotero skill 到 DeepSeek Harness")
    parser.add_argument("--skills-dir", type=Path, help="指定 skill 根目录，例如 项目/.dsh/skills")
    parser.add_argument("--dry-run", action="store_true", help="只显示目标，不写入")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / "zotero"
    root = args.skills_dir or Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))) / "skills"
    target = root.expanduser().resolve() / "zotero"
    if not (source / "SKILL.md").is_file():
        parser.error("安装包缺少 zotero/SKILL.md，请完整解压后运行")
    if target.exists() or target.is_symlink():
        parser.error(f"目标已存在，不覆盖现有 skill：{target}")
    if target == source or source in target.parents:
        parser.error("安装目标不能位于源 skill 内部")
    print(f"Zotero skill -> {target}")
    if not args.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print("安装完成。请在 Harness 中加载 zotero 技能并运行 status --json。")


if __name__ == "__main__":
    main()
