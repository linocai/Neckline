#!/usr/bin/env bash
# Neckline 后端一键安装。幂等:建 venv、装钉死依赖；不触碰数据库。
# pip 默认走阿里云镜像；可用 PIP_INDEX_URL 覆盖。
#
# 用法(在 /opt/neckline 或任意目录均可,脚本自定位到仓库根):
#   bash scripts/setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Neckline setup @ ${ROOT_DIR}"

# 1) venv(幂等)
if [ ! -d "${VENV_DIR}" ]; then
  echo "==> 建 venv: ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  echo "==> venv 已存在,复用"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# 2) 依赖(钉死版本;pip 阿里云镜像 + 超时余量)
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
echo "==> pip 源: ${PIP_INDEX_URL}"
python -m pip install --quiet --upgrade pip
echo "==> 安装钉死依赖(requirements.txt)"
python -m pip install --quiet -r "${ROOT_DIR}/requirements.txt"

# 3) .env(缺失则从样例拷占位,不覆盖已有)
if [ ! -f "${ROOT_DIR}/.env" ]; then
  echo "==> 未见 .env,从 .env.example 拷占位(请填 API_TOKEN / TUSHARE_TOKEN / APNS_* 等)"
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
else
  echo "==> .env 已存在,保留不动"
fi

# 4) 数据库迁移必须由运维明确执行，安装脚本不会创建或迁移任何库。
echo "==> setup 完成。激活:source ${VENV_DIR}/bin/activate"
echo "==> 新库须显式初始化 common + K10 + notifications schema；已有库须走经过备份校验的 k10 migration。"
echo "==> 示例(确认目标路径后执行): DB_PATH=/path/to/neckline.db ${VENV_DIR}/bin/python -c 'from pathlib import Path; import os; from neckline.db import init_schema; from neckline.k10.schema import initialize_schema; from neckline.k10.notifications import initialize_notifications_schema; p=Path(os.environ[\"DB_PATH\"]); init_schema(p); initialize_schema(p); initialize_notifications_schema(p)'"
