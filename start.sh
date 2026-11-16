#!/bin/bash
# run in wsl
set -e
cd /code/daily-stock-analysis

source .venv/bin/activate
sudo service cron start

# 启动前先停止旧的 main.py --serve 实例并等待其完全退出：
# 旧实例尚未退出时新实例会撞端口，导致 --serve 启动失败。
# pattern 匹配 --serve 结尾或后跟其他参数（不含 --serve-only，桌面端在用），
# [m] 防止 pgrep/pkill 匹配到自身 cmdline。
# 说明：进程退出后端口即随套接字释放，无需额外等待固定秒数。
OLD_PIDS=$(pgrep -f '[m]ain\.py --serve($| )' || true)
if [ -n "$OLD_PIDS" ]; then
    echo "发现旧实例 PID: $OLD_PIDS，发送 SIGINT 等待退出..."
    kill -INT $OLD_PIDS 2>/dev/null || true
    for _ in $(seq 1 30); do
        pgrep -f '[m]ain\.py --serve($| )' >/dev/null || break
        sleep 1
    done
    if pgrep -f '[m]ain\.py --serve($| )' >/dev/null; then
        echo "旧实例 30s 内未退出，强制结束"
        pkill -KILL -f '[m]ain\.py --serve($| )' || true
        sleep 2
    fi
fi

python main.py --serve
