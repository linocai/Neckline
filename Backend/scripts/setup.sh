#!/usr/bin/env bash
# Neckline 后端一键安装。幂等:建 venv、装钉死依赖、建 SQLite schema。
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

# 4) 建 SQLite schema(幂等)
# ⚠ ECS 部署:.env 为 600 neckline:neckline,且 neckline.db 应由服务用户(neckline)拥有并可写。
# 若当前是 deploy(非 neckline)且 neckline 用户存在 → 用 `sudo -u neckline` 建库(库归 neckline,
# 服务才写得动 WAL);否则(本地开发)直接建。服务 lifespan 启动也会 init_schema(幂等兜底)。
echo "==> 初始化 SQLite schema"
if [ "$(id -un)" != "neckline" ] && id neckline >/dev/null 2>&1; then
  echo "  (以 neckline 用户建库,保证服务可写)"
  sudo -u neckline "${VENV_DIR}/bin/python" -c "from neckline.db import init_schema; init_schema(); print('DB schema ready (owner=neckline)')"
else
  python -c "from neckline.db import init_schema; init_schema(); print('DB schema ready')"
fi

echo "==> setup 完成。激活:source ${VENV_DIR}/bin/activate"
echo "==> 冒烟:bash scripts/smoke_api.sh"
