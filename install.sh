#!/usr/bin/env bash
# 把 tome 技能族安装到 Claude Code 的技能目录（默认 ~/.claude/skills）。
#
# 默认软链；git pull 后开新会话即生效。
#
# 用法：
#   ./install.sh            # 目标不存在时创建软链
#   ./install.sh --force    # 目标已存在时先备份再替换
#   ./install.sh --copy     # 复制一份而不是软链
#   CLAUDE_SKILLS_DIR=... ./install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
SKILLS=(tome tome-bili tome-wechat tome-clip tome-portrait tome-spread tome-book lecture-notes)
FORCE=0
LINK=1
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --link)  LINK=1 ;;
    --copy)  LINK=0 ;;
    *) echo "未知参数：$arg"; exit 2 ;;
  esac
done

if [[ "$REPO" == "$(cd "$DEST" 2>/dev/null && pwd)" ]]; then
  echo "当前目录就是技能目录（${DEST}），无需安装。"
  exit 0
fi

mkdir -p "$DEST"

conflicts=()
for s in "${SKILLS[@]}"; do
  target="$DEST/$s"
  if [[ -e "$target" || -L "$target" ]]; then
    if (( LINK == 1 )) && [[ -L "$target" && "$(readlink "$target")" == "$REPO/$s" ]]; then
      continue
    fi
    conflicts+=("$s")
  fi
done

if (( ${#conflicts[@]} > 0 )) && (( FORCE == 0 )); then
  printf '以下目录已存在，未做任何改动：\n'
  for s in "${conflicts[@]}"; do printf '  %s\n' "$DEST/$s"; done
  printf '\n确认要替换请加 --force（会先备份到 %s/tome-backup-<时间>）\n' "$DEST"
  exit 1
fi

if (( ${#conflicts[@]} > 0 )); then
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup="$DEST/tome-backup-$stamp"
  mkdir -p "$backup"
  for s in "${conflicts[@]}"; do
    cp -R "$DEST/$s" "$backup/$s"
  done
  printf '已备份到 %s\n' "$backup"
fi

for s in "${SKILLS[@]}"; do
  rm -rf "$DEST/$s"
  if (( LINK == 1 )); then
    ln -s "$REPO/$s" "$DEST/$s"
  else
    cp -R "$REPO/$s" "$DEST/$s"
  fi
  printf '  ✓ %s\n' "$DEST/$s"
done

cat <<EOF

安装完成。下一步：

  python3 $DEST/tome/scripts/setup.py
  python3 $DEST/tome/scripts/doctor.py

更新：git pull（软链模式下开新会话即生效）
然后在 Claude Code 里说「用 tome 把这些资料整理成书」。
EOF
