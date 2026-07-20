#!/usr/bin/env bash
# Neckline 后端代码同步(plan 4B.2)。rsync 仓库根 → deploy@host:/opt/neckline。
# **只同步源码**,显式排除:业务数据 / 密钥 / 本机 db / 研究缓存。
#
# 全量吸收 hz_info.md §12 + LinoN CLAUDE.md 部署坑:
#   · GNU rsync 3.x(macOS 自带 openrsync 与 --delete 不兼容)——自动探测。
#   · **exclude 锚定根 `/data/`**(前导斜杠):Neckline 同时有 data/(Parquet+db,排)与
#     源码包 neckline/data/(tushare_client/realtime/limit_derived,**绝不能排**)。这正是
#     LinoN 坑 4——无锚 'data/' 会匹配任意层级、连 neckline/data/ 一起误删。
#   · 排除 .env / *.p8(远端独立维护,--delete 绝不清)。
#   · rsync -a 会冲掉 setgid → 同步后须 chown/chmod 复原(见脚本尾提示)。
#
# 用法:
#   bash scripts/sync_code.sh              # 用默认 hz 目标(deploy@118.178.122.194:/opt/neckline)
#   DRY_RUN=1 bash scripts/sync_code.sh    # 预演,不实传
#   NECKLINE_DEPLOY_HOST=... NECKLINE_DEPLOY_USER=... NECKLINE_DEPLOY_PATH=... bash scripts/sync_code.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${NECKLINE_DEPLOY_HOST:-118.178.122.194}"
USER_NAME="${NECKLINE_DEPLOY_USER:-deploy}"
REMOTE_PATH="${NECKLINE_DEPLOY_PATH:-/opt/neckline}"

# —— 选 GNU rsync 3.x(用命令替换捕获版本,避免 pipefail 下 head 关管道误判)——
_is_gnu_rsync3() {
  case "$("$1" --version 2>/dev/null | head -1 || true)" in
    *rsync*"version 3"*) return 0 ;;
    *) return 1 ;;
  esac
}
RSYNC_BIN="${RSYNC_BIN:-rsync}"
if ! _is_gnu_rsync3 "${RSYNC_BIN}"; then
  for cand in /opt/homebrew/bin/rsync /usr/local/bin/rsync; do
    if [ -x "${cand}" ] && _is_gnu_rsync3 "${cand}"; then RSYNC_BIN="${cand}"; break; fi
  done
fi
if ! _is_gnu_rsync3 "${RSYNC_BIN}"; then
  echo "[sync_code] 未找到 GNU rsync 3.x(macOS openrsync 与 --delete 不兼容)。brew install rsync"
  exit 1
fi

DEST="${USER_NAME}@${HOST}:${REMOTE_PATH}"
RSYNC_OPTS=(-az --delete
  --exclude '/data/'          # 【必须前导斜杠锚定根】只排顶层 data/(Parquet+db),不误伤 neckline/data/
  --exclude '/research/'      # 顶层研究脚本 + 缓存(运行期不需要;neckline/research/ 包不受影响)
  --exclude '/archive/'
  --exclude '/scratchpad/'
  --exclude '.venv/'
  --exclude '.git/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.pytest_cache/'
  --exclude '.env'            # 密钥不随同步覆盖(远端 .env 独立维护)
  --exclude '*.p8'            # APNs 私钥:远端独立维护,--delete 绝不清
  --exclude '*.db'
  --exclude '*.db-*'
  --exclude '.DS_Store'
)

if [ "${DRY_RUN:-0}" = "1" ]; then
  RSYNC_OPTS+=(--dry-run --verbose)
  echo "[sync_code] DRY_RUN:预演,不实传"
fi

echo "[sync_code] ${RSYNC_BIN} ${ROOT_DIR}/  ->  ${DEST}"
"${RSYNC_BIN}" "${RSYNC_OPTS[@]}" "${ROOT_DIR}/" "${DEST}/"

cat <<EOF
[sync_code] 完成。远端收尾(rsync -a 冲了 setgid 须复原;chown -R 会把 .env/.p8 也翻成 deploy,
须 chown 回 neckline 否则服务 User=neckline 读不到;改包结构须删 stale .pyc):
  ssh ${USER_NAME}@${HOST} 'sudo chown -R deploy:neckline ${REMOTE_PATH} \\
    && sudo find ${REMOTE_PATH} -type d -exec chmod 2770 {} + \\
    && sudo chown neckline:neckline ${REMOTE_PATH}/.env ${REMOTE_PATH}/*.p8 \\
    && sudo chmod 600 ${REMOTE_PATH}/.env ${REMOTE_PATH}/*.p8; \\
    sudo find ${REMOTE_PATH}/neckline -name "*.pyc" -delete'
  # ECS Python 3.12:--delete 不清 stale __pycache__/*.pyc,改包结构后手动删(上一行已含)。
  # systemd unit / nginx conf 改动须手动 scp + daemon-reload / nginx reload(不走本脚本)。
EOF
