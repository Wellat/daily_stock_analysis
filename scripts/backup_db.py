# -*- coding: utf-8 -*-
"""SQLite 数据库定时备份脚本。

对项目默认数据库 ``data/stock_analysis.db`` 执行 SQLite 在线备份（标准库
``sqlite3`` 的 ``Connection.backup``），在数据库开启 WAL 且可能正被写入时
也能得到一致快照，不依赖 ``sqlite3`` CLI 可执行文件。

用法：
    python scripts/backup_db.py
    python scripts/backup_db.py --dry-run
    python scripts/backup_db.py --db-path ./data/stock_analysis.db \
        --backup-dir ./data/backups --keep 5

建议通过系统 cron 每 3 天执行一次（``crontab -e``）：

    0 2 */3 * * cd /home/ubuntu/code/daily_stock_analysis && \
        python scripts/backup_db.py >> logs/backup_db.log 2>&1
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DB_PATH = "./data/stock_analysis.db"
DEFAULT_KEEP = 5
BACKUP_PREFIX = "stock_analysis_"


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _backup_filename() -> str:
    return f"{BACKUP_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"


def _existing_backups(backup_dir: Path) -> list[Path]:
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db"), key=lambda p: p.name, reverse=True)


def _prune_plan(backup_dir: Path, new_backup_name: str, keep: int) -> list[Path]:
    """Return existing backup files that would be deleted after adding the new one."""
    names = sorted(
        [p.name for p in _existing_backups(backup_dir)] + [new_backup_name],
        reverse=True,
    )
    return [backup_dir / name for name in names[keep:]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="对项目 SQLite 数据库执行在线备份并轮转保留最近 N 份。",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("DATABASE_PATH", DEFAULT_DB_PATH),
        help=f"数据库路径（默认读取 DATABASE_PATH，缺省 {DEFAULT_DB_PATH}）",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="备份目录（默认数据库同级 backups/ 子目录）",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"保留份数（默认 {DEFAULT_KEEP}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的操作，不实际备份或删除",
    )
    args = parser.parse_args()

    if args.keep < 1:
        print("错误: --keep 必须为正整数", file=sys.stderr)
        return 2

    db_path = _resolve_path(args.db_path)
    backup_dir = _resolve_path(args.backup_dir) if args.backup_dir else db_path.parent / "backups"
    new_name = _backup_filename()
    backup_path = backup_dir / new_name

    if not db_path.is_file():
        print(f"错误: 数据库文件不存在: {db_path}", file=sys.stderr)
        return 2

    to_delete = _prune_plan(backup_dir, new_name, args.keep)

    print(f"数据库: {db_path}")
    print(f"备份目录: {backup_dir}")
    print(f"新备份文件: {backup_path}")

    if args.dry_run:
        if to_delete:
            print("将删除以下超出保留份数的旧备份:")
            for path in to_delete:
                print(f"  - {path}")
        else:
            print("无需删除旧备份")
        print("[dry-run] 未执行任何写入或删除")
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        source = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
    except sqlite3.Error as exc:
        print(f"错误: 备份失败: {exc}", file=sys.stderr)
        return 1

    print(f"备份完成: {backup_path}")

    for path in to_delete:
        try:
            path.unlink()
            print(f"已删除旧备份: {path}")
        except OSError as exc:
            print(f"警告: 删除旧备份失败 {path}: {exc}", file=sys.stderr)

    remaining = _existing_backups(backup_dir)
    print(f"当前备份数量: {len(remaining)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
