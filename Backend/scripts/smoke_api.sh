#!/usr/bin/env bash
# 当前 API 契约冒烟。默认使用临时数据库与临时 token，不碰生产台账。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
PY="${PY:-.venv/bin/python}"
HOST=127.0.0.1
PORT="${PORT:-8099}"
BASE="http://$HOST:$PORT/api/v1"

export DB_PATH="${DB_PATH:-/tmp/neckline_smoke_$$.db}"
export API_TOKEN="${API_TOKEN:-smoke_token_at_least_16_chars_long}"
export NECKLINE_ENABLE_MORNING_TASKS=0
AUTH=(-H "Authorization: Bearer $API_TOKEN")
JSON=(-H "Content-Type: application/json")

"$PY" -m uvicorn neckline.api.app:app --host "$HOST" --port "$PORT" \
  --log-level warning >/tmp/neckline_smoke.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true; rm -f "$DB_PATH" "$DB_PATH"-* /tmp/neckline_smoke_push.json 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" "$BASE/health" 2>/dev/null | grep -q 200 && break
  sleep 1
done

echo "1) health"; curl -fsS "$BASE/health"; echo
echo "2) 无 token 必须 401"
test "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/settings")" = "401"

echo "3) settings + 两类通知开关"
curl -fsS "${AUTH[@]}" "$BASE/settings" | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
assert [x['kind'] for x in d['push']['kinds']]==['report_ready','precall']
print(json.dumps({'kinds':{x['kind']:True for x in d['push']['kinds']}}))
" >/tmp/neckline_smoke_push.json
curl -fsS "${AUTH[@]}" "${JSON[@]}" -X PUT \
  -d @/tmp/neckline_smoke_push.json "$BASE/settings/push" >/dev/null

echo "4) 设备注册"
curl -fsS "${AUTH[@]}" "${JSON[@]}" \
  -d '{"token":"smoke-device","platform":"ios"}' "$BASE/devices" >/dev/null

echo "5) 选股三态空态"
curl -fsS "${AUTH[@]}" "$BASE/selection/latest" | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
assert d['state']=='not_run'
print(d['headline'])
"

echo "6) K9-v3 成绩包空态"
curl -fsS "${AUTH[@]}" "$BASE/scoreboard/packages?state=active" | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
assert d['strategyVersion']=='K9-v3' and d['packages']==[]
"
curl -fsS "${AUTH[@]}" "$BASE/scoreboard/packages?state=settled" | "$PY" -c "
import json,sys
assert json.load(sys.stdin)['packages']==[]
"

echo "7) 次日核对只接受成绩包 ID"
test "$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/checklists/no-such-package")" = "404"

echo "8) 复盘现行两段"
curl -fsS "${AUTH[@]}" "$BASE/review/overview?week=20240430" | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
assert set(d)>={'reconcile','conclusions'}
"
curl -fsS "${AUTH[@]}" "$BASE/review/bindery?week=2024-W18" | "$PY" -c "
import json,sys
assert json.load(sys.stdin)['found'] is False
"

echo "9) 退役路由必须 404"
for path in eval/weekly legacy/k8/baskets baskets positions alerts market-regime; do
  test "$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/$path")" = "404"
done

echo ">> 冒烟完成"
