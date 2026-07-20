#!/usr/bin/env bash
# 阶段4 · 4A FastAPI 脊椎冒烟(plan 4A 验收:本地 uvicorn 起 → curl 全端点闭环)。
# 起 uvicorn → health(免鉴权 200)+ 无/错 token 401 + 报告/看板 degraded 空态 +
# 持仓 open→list→close→重复 close 404 + 设置(GET / PUT llm 不回明文 / PUT push)+
# 问询台二值裁决 + 设备注册。
#
# 用法:bash scripts/smoke_api.sh
#   · 默认用**临时库**(DB_PATH=/tmp/neckline_smoke_*.db)+ 临时 API_TOKEN,不碰生产台账。
#   · 本地默认端口 8099(避开生产 8002);ECS 上跑传 PORT=8002 对生产库冒烟需先备份。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
PY="${PY:-.venv/bin/python}"
HOST=127.0.0.1
PORT="${PORT:-8099}"
BASE="http://$HOST:$PORT/api/v1"

# 隔离:临时库 + 临时 token(除非调用方显式传 API_TOKEN/DB_PATH)
export DB_PATH="${DB_PATH:-/tmp/neckline_smoke_$$.db}"
export API_TOKEN="${API_TOKEN:-smoke_token_at_least_16_chars_long}"
export NECKLINE_ENABLE_SENTINEL=0       # 关后台哨兵轮询(冒烟不需要)
TOKEN="$API_TOKEN"
AUTH=(-H "Authorization: Bearer $TOKEN")
JSON=(-H "Content-Type: application/json")

echo ">> 起 uvicorn :$PORT (DB_PATH=$DB_PATH) ..."
"$PY" -m uvicorn neckline.api.app:app --host "$HOST" --port "$PORT" --log-level warning >/tmp/neckline_smoke.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true; rm -f "$DB_PATH" "$DB_PATH"-* 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" "$BASE/health" 2>/dev/null | grep -q 200 && break
  sleep 1
done

echo "1) health(免鉴权):"; curl -s "$BASE/health"; echo
echo "2) 无 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "$BASE/positions"
echo "3) 错 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" -H "Authorization: Bearer nope" "$BASE/positions"
echo "4) report/latest(空 → degraded):"; curl -s "${AUTH[@]}" "$BASE/report/latest"; echo
echo "5) board(空):"; curl -s "${AUTH[@]}" "$BASE/board"; echo
echo "6) settings(默认):"; curl -s "${AUTH[@]}" "$BASE/settings"; echo
echo "7) PUT settings/llm(key 不回明文):"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"provider":"glm","apiKey":"sk-smoke-secret"}' -X PUT "$BASE/settings/llm"; echo
echo "8) settings(应 llmKeySet=true 且无明文):"; curl -s "${AUTH[@]}" "$BASE/settings"; echo
echo "9) PUT settings/push:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"report":true,"retreatBrake":false}' -X PUT "$BASE/settings/push"; echo
echo "10) open:"; OPEN=$(curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600519.SH","name":"贵州茅台","buy_price":1500.0,"qty":100,"entry_reason":"回调低吸"}' "$BASE/positions"); echo "  $OPEN"
PID=$(echo "$OPEN" | "$PY" -c "import sys,json;print(json.load(sys.stdin)['position_id'])")
echo "11) list:"; curl -s "${AUTH[@]}" "$BASE/positions"; echo
echo "12) close:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"sell_price":1520.0}' "$BASE/positions/$PID/close"; echo
echo "13) 重复 close → 404:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"sell_price":1520.0}' "$BASE/positions/$PID/close"
echo "14) inquiry(无 key 降级,裁决二值):"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600519.SH","messages":[]}' "$BASE/inquiry"; echo
echo "15) device register:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"token":"smoke-device","platform":"ios"}' "$BASE/devices"; echo
echo ">> 冒烟完成。"
