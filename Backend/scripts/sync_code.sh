#!/usr/bin/env bash
# Neckline 后端代码同步。rsync 仓库根 → deploy@host:/opt/neckline。
# **只同步源码**,显式排除:业务数据 / 密钥 / 本机 db / 研究缓存。
#
#   · GNU rsync 3.x(macOS 自带 openrsync 与 --delete 不兼容)——自动探测。
#   · **exclude 锚定根 `/data/`**(前导斜杠):Neckline 同时有 data/(Parquet+db,排)与
#     源码包 neckline/data/(tushare_client/realtime/limit_derived,**绝不能排**)。
#   · 排除 .env / *.p8(远端独立维护,--delete 绝不清)。
#   · rsync -a 会冲掉 setgid → 同步后须 chown/chmod 复原(见脚本尾提示)。
#   · 收尾权限修复必须跳过 data/；脚本末尾会只读核对生产数据库属主。
#
# 用法:
#   NECKLINE_DEPLOY_HOST=114.66.2.205 bash scripts/sync_code.sh
#   DRY_RUN=1 bash scripts/sync_code.sh    # 预演,不实传;属主自检随之跳过(dry-run 未
#                                           # 触碰远端,检查无意义,见脚本内注释)
#   NECKLINE_DEPLOY_HOST=... NECKLINE_DEPLOY_USER=... NECKLINE_DEPLOY_PATH=... bash scripts/sync_code.sh
#   bash scripts/sync_code.sh --selfcheck-only <远端绝对路径>
#                                           # 只跑属主自检本身、不 rsync(验收/演练用;
#                                           # 指向无害临时路径,不碰生产 data/)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${NECKLINE_DEPLOY_HOST:-}"
USER_NAME="${NECKLINE_DEPLOY_USER:-deploy}"
REMOTE_PATH="${NECKLINE_DEPLOY_PATH:-/opt/neckline}"
if [ -z "${HOST}" ]; then
  echo "[sync_code] NECKLINE_DEPLOY_HOST 必填；禁止使用隐式生产目标。" >&2
  exit 64
fi

# —— 只读属主自检(v1.2-0 新增):核对远端指定路径属主是否 neckline:neckline ——
# 用 sudo -n stat(非交互):实测 data/ 目录 drwxrws--- 属 neckline:neckline,deploy 不在
# neckline 组、对 data/ 没有搜索权限,plain stat 会直接 Permission denied,必须 sudo;
# -n 免密失败即报错不卡死(已实测该 host 上 deploy 配 NOPASSWD: ALL)。
# 参数 $1 = 待核对的远端绝对路径——真部署传生产库路径;--selfcheck-only 可传任意无害
# 临时路径单独验证这段逻辑,不触碰生产 data/。
# 返回:0 = 属主吻合;1 = 不吻合 / 远端不可达 / 路径不存在 / 权限不足(一律视为失败,
# 不静默当通过,失败原因打印到 stderr)。
_check_owner() {
  local target_path="$1"
  local stat_output
  if ! stat_output=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "${USER_NAME}@${HOST}" \
      "sudo -n stat -c '%U:%G' '${target_path}'" 2>&1); then
    echo -e "\033[31m[sync_code] 自检失败:无法读取远端 ${target_path} 属主(远端不可达 / 路径不存在 / 权限不足)。\n远端返回:${stat_output}\033[0m" >&2
    return 1
  fi
  if [ "${stat_output}" != "neckline:neckline" ]; then
    echo -e "\033[31m[sync_code] 自检失败:${target_path} 属主是「${stat_output}」,应为「neckline:neckline」。\n很可能是收尾 chown/chmod 命令误碰了 data/,生产 DB 可能已只读——立即人工核对并复原:\n  ssh ${USER_NAME}@${HOST} 'sudo chown -R neckline:neckline ${REMOTE_PATH}/data'\033[0m" >&2
    return 1
  fi
  echo "[sync_code] 自检通过:${target_path} 属主 = neckline:neckline"
  return 0
}

# —— 独立自检模式:只验自检逻辑本身,不 rsync、不动 data/(验收 / 演练用)——
if [ "${1:-}" = "--selfcheck-only" ]; then
  TARGET="${2:?用法: sync_code.sh --selfcheck-only <远端绝对路径>}"
  _check_owner "${TARGET}"
  exit $?
fi

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
  --exclude '.git'            # 【无尾斜杠:目录与文件都排】worktree 的 .git 是**文件**
                              # (内容 `gitdir: …`),旧版 '.git/' 只排目录,于是从
                              # worktree 同步时会把它当普通文件推上生产(2026-07-29 回滚
                              # 演练真踩,生产多出一个 65B 的 /opt/neckline/.git 指向本机
                              # 路径的死指针,次日 ⑨ 正式部署时才被 --delete 清掉)
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
[sync_code] 完成。远端收尾(rsync -a 冲了 setgid 须复原;下面 chown/chmod 两条均用
find -path ... -prune 跳过 data/——data/ 本就被 rsync --exclude 未触碰、属主天然正确,
收尾绝不碰它。chown -R 仍会把
.env/.p8 也翻成 deploy,须 chown 回 neckline 否则服务 User=neckline 读不到;改包结构
须删 stale .pyc):
  ssh ${USER_NAME}@${HOST} 'sudo find ${REMOTE_PATH} -path ${REMOTE_PATH}/data -prune -o -exec chown deploy:neckline {} + \\
    && sudo find ${REMOTE_PATH} -path ${REMOTE_PATH}/data -prune -o -type d -exec chmod 2770 {} + \\
    && sudo chown neckline:neckline ${REMOTE_PATH}/.env ${REMOTE_PATH}/*.p8 \\
    && sudo chmod 600 ${REMOTE_PATH}/.env ${REMOTE_PATH}/*.p8; \\
    sudo find ${REMOTE_PATH}/neckline -name "*.pyc" -delete'
  # 下方脚本已自动跑只读属主自检;若已看到自检红字,先按提示复原 data/ 属主再排查,
  # 别急着跑这段收尾命令。
  # ECS Python 3.12:--delete 不清 stale __pycache__/*.pyc,改包结构后手动删(上一行已含)。
  # systemd unit / nginx conf 改动须手动 scp + daemon-reload / nginx reload(不走本脚本)。
EOF

# —— rsync 完成后的只读属主自检(v1.2-0 新增)——
# DRY_RUN 下 rsync 未实传,data/ 属主必然不受本次运行影响,自检没有新信息量可核对,
# 故跳过(不是「假装通过」——是这次预演压根没做任何可能弄脏 data/ 属主的事)。
# 真部署(非 DRY_RUN)才会自动跑:核对 data/neckline.db 属主——既防本次 rsync 意外
# 触碰(理论上 --exclude 已挡,双保险),也防上次部署遗留的属主污染被继续无视,
# 不符直接红字 + exit 1,把问题挡在「发现服务 502」之前。
if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "[sync_code] DRY_RUN:跳过远端只读属主自检(dry-run 未实传,data/ 属主不受本次影响)。"
else
  echo "[sync_code] 自检:核对 ${REMOTE_PATH}/data/neckline.db 属主…"
  _check_owner "${REMOTE_PATH}/data/neckline.db" || exit 1
fi
