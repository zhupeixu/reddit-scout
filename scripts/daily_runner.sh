#!/bin/bash
# Daily reddit-scout runner — 由 launchd 调用
# 加载用户 shell 环境（确保 ANTHROPIC_API_KEY、PATH 等可用）

# 1. 加载环境变量
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc" 2>/dev/null
elif [ -f "$HOME/.bashrc" ]; then
    source "$HOME/.bashrc" 2>/dev/null
fi
if [ -f "$HOME/.zprofile" ]; then
    source "$HOME/.zprofile" 2>/dev/null
fi

# 兜底：明确加上常见 PATH
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 2. 日志路径
LOG_DIR="$HOME/reddit-scout-reports"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily_$(date +%Y%m%d).log"

# 3. 跑脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "==== Daily run started: $(date) ====" >> "$LOG"
/usr/bin/env python3 -u "$SCRIPT_DIR/daily.py" >> "$LOG" 2>&1
EXIT_CODE=$?
echo "==== Daily run finished: $(date) (exit $EXIT_CODE) ====" >> "$LOG"
exit $EXIT_CODE
