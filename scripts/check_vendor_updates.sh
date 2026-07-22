#!/bin/bash
# 定时检查 vendor 仓库更新，有变动通过 TG 通知南杉
# cron: 每天 10:00 跑一次

BOT_TOKEN="8656179565:AAFjrh-r31bnhVIz0bxBlCJXFFXo1Vgy4OQ"
CHAT_ID="8624126581"
VENDOR_DIR="/opt/cedartoy/vendor"
UPDATES=""

for d in "$VENDOR_DIR"/*/; do
  [ -d "$d/.git" ] || continue
  name=$(basename "$d")
  cd "$d"
  
  git fetch origin 2>/dev/null
  
  # 找远端主分支
  remote_ref=$(git rev-parse --verify origin/main 2>/dev/null || git rev-parse --verify origin/master 2>/dev/null)
  [ -z "$remote_ref" ] && continue
  
  local_ref=$(git rev-parse HEAD 2>/dev/null)
  [ "$local_ref" = "$remote_ref" ] && continue
  
  behind=$(git rev-list --count HEAD.."$remote_ref" 2>/dev/null)
  [ "$behind" -eq 0 ] 2>/dev/null && continue
  
  # 拿最新 commit 摘要
  latest=$(git log --oneline HEAD.."$remote_ref" | head -3 | sed 's/^/  /')
  UPDATES="${UPDATES}
🔄 ${name} (${behind}个新commit)
${latest}
"
done

[ -z "$UPDATES" ] && exit 0

MSG="📦 cedartoy vendor 仓库更新检测
${UPDATES}
需要更新请让小克处理，不会自动拉取。"

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d chat_id="$CHAT_ID" \
  -d text="$MSG" \
  -d parse_mode="" \
  > /dev/null 2>&1
