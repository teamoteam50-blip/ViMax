"""CLI: python -m mandarin_factory run <project_dir> [--keep-work]

exit code: 0 = 全部质检门通过并已交付；2 = blocked（成片未获批准，原因见输出）。
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mandarin_factory", description="全自动电影级普通话视频工厂（渲染+质检+返工+交付）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="对项目目录执行完整交付管线")
    p_run.add_argument("project_dir")
    p_run.add_argument("--keep-work", action="store_true", help="保留 .render_work 中间段")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        from . import run
        from .factory import FactoryBlocked
        try:
            summary = run(args.project_dir, keep_work=args.keep_work)
        except FactoryBlocked as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["status"] == "done" else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
