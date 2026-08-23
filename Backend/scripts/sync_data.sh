#!/usr/bin/env bash
# Neckline 行情 Parquet 产物同步(plan 4B.1 方案 B / 内存门禁用)。**只把 data/parquet/
# 推上云**(行情只读),**显式排除 *.db**——绝不上传本机 neckline.db 覆盖 ECS 权威台账
# (settings/LLM key/devices/K9 报告/成绩/reviews 恒以 ECS 为准)。
#
# 与 sync_code.sh 反向:那个排 /data/ 传源码;这个只传 data/parquet/、排 db。全量 backfill
# (六年历史)恒在 Mac 一次性跑,ECS 不做全量 backfill(§3.6)。
#
# 用法:
#   bash scripts/sync_data.sh              # 用默认 hz 目标
#   DRY_RUN=1 bash scripts/sync_data.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${NECKLINE_DEPLOY_HOST:-118.178.122.194}"
USER_NAME="${NECKLINE_DEPLOY_USER:-deploy}"
REMOTE_PATH="${NECKLINE_DEPLOY_PATH:-/opt/neckline}"

LOCAL_PARQUET="${ROOT_DIR}/data/parquet/"
if [ ! -d "${LOCAL_PARQUET}" ]; then
  echo "[sync_data] 本机无 ${LOCAL_PARQUET},无可同步(先在 Mac 跑 backfill)。"
  exit 1
fi

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
  echo "[sync_data] 未找到 GNU rsync 3.x。brew install rsync"; exit 1
fi

DEST="${USER_NAME}@${HOST}:${REMOTE_PATH}/data/parquet/"
RSYNC_OPTS=(-az --delete
  --exclude '*.db'            # 双保险:绝不上传任何 db(ECS 台账权威)
  --exclude '*.db-*'
  --exclude '.DS_Store'
)
if [ "${DRY_RUN:-0}" = "1" ]; then
  RSYNC_OPTS+=(--dry-run --verbose)
  echo "[sync_data] DRY_RUN:预演,不实传"
fi

echo "[sync_data] ${RSYNC_BIN} ${LOCAL_PARQUET}  ->  ${DEST}"
"${RSYNC_BIN}" "${RSYNC_OPTS[@]}" "${LOCAL_PARQUET}" "${DEST}"

cat <<EOF
[sync_data] 完成。远端收尾(setgid 复原):
  ssh ${USER_NAME}@${HOST} 'sudo chown -R deploy:neckline ${REMOTE_PATH}/data/parquet \\
    && sudo find ${REMOTE_PATH}/data/parquet -type d -exec chmod 2770 {} +'
EOF
