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
echo "9) PUT settings/push(v1.1-G.1 四字段):"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"report":true,"retreatBrake":false,"precall":true,"d5exit":true}' -X PUT "$BASE/settings/push"; echo
echo "10) open:"; OPEN=$(curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600519.SH","name":"贵州茅台","buy_price":1500.0,"qty":100,"entry_reason":"回调低吸"}' "$BASE/positions"); echo "  $OPEN"
PID=$(echo "$OPEN" | "$PY" -c "import sys,json;print(json.load(sys.stdin)['position_id'])")
echo "11) list:"; curl -s "${AUTH[@]}" "$BASE/positions"; echo
echo "12) close:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"sell_price":1520.0}' "$BASE/positions/$PID/close"; echo
echo "13) 重复 close → 404:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"sell_price":1520.0}' "$BASE/positions/$PID/close"
echo "14) inquiry(无 key 降级,裁决二值):"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600519.SH","messages":[]}' "$BASE/inquiry"; echo
echo "15) device register:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"token":"smoke-device","platform":"ios"}' "$BASE/devices"; echo

# —— v1.1-C 自选池 + 同花顺 txt 对账/导出 ————————————————————————————————
echo "16) watchlist add 600001.SH:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600001.SH","name":"示例甲"}' "$BASE/watchlist"; echo
echo "17) watchlist add 000001.SZ:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"000001.SZ"}' "$BASE/watchlist"; echo
echo "18) watchlist list(应 2 只,check 均为 null——尚未跑过报告):"; curl -s "${AUTH[@]}" "$BASE/watchlist"; echo
echo "19) watchlist pin 600001.SH:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"pinned":true}' -X PUT "$BASE/watchlist/600001.SH/pin"; echo
echo "20) watchlist export-ths:"; curl -s "${AUTH[@]}" "$BASE/watchlist/export-ths"; echo
echo "21) watchlist reconcile-ths(txt: 600001.SH 两边都有,600002.SH 只在同花顺):"
printf '600001\n600002\n' > /tmp/neckline_smoke_ths_$$.txt
curl -s "${AUTH[@]}" -F "file=@/tmp/neckline_smoke_ths_$$.txt;type=text/plain" "$BASE/watchlist/reconcile-ths"; echo
rm -f /tmp/neckline_smoke_ths_$$.txt
echo "22) watchlist delete 000001.SZ:"; curl -s -o /dev/null -w "  status=%{http_code}\n" -X DELETE "${AUTH[@]}" "$BASE/watchlist/000001.SZ"
echo "23) watchlist delete 不存在代码 → 404:"; curl -s -o /dev/null -w "  status=%{http_code}\n" -X DELETE "${AUTH[@]}" "$BASE/watchlist/999999.SH"
echo "24) watchlist list(应剩 1 只):"; curl -s "${AUTH[@]}" "$BASE/watchlist"; echo

# —— v1.2-B 预注册决策日志(八项)————————————————————————————————————————
echo "25) POST /decisions(八项 + 情景树 + 打法标签,附带一个荒谬 createdAt 入参验证被忽略):"
DEC=$(curl -s "${AUTH[@]}" "${JSON[@]}" -d '{
  "code":"600001.SH","name":"示例甲",
  "whyBuy":"题材热+量能启动","whyEntryPrice":"回调至10日线企稳",
  "targetPrice":12.0,"exitLow":9.0,"exitHigh":9.5,
  "thesisTags":["THEME","CAPITAL_FLOW"],"invalidation":"跌破10日线",
  "contingencyScenarios":[
    {"scenario":"次日高开","trigger":"开盘涨幅>3%","action":"HOLD"},
    {"scenario":"次日低开","trigger":"开盘跌幅>2%","action":"ABANDON"}
  ],
  "playbookTag":"SWING_CHASE","plannedPrice":10.0,"plannedQty":1000,
  "createdAt":"1999-01-01T00:00:00Z"
}' "$BASE/decisions"); echo "  $DEC"
DID=$(echo "$DEC" | "$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "26) 非法论点标签码 → 422:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600001.SH","whyBuy":"x","whyEntryPrice":"x","thesisTags":["NOT_REAL"],"invalidation":"x","contingencyScenarios":[],"playbookTag":"SWING_CHASE"}' "$BASE/decisions"
echo "27) GET /decisions(应 1 条,createdAt 不是 1999):"; curl -s "${AUTH[@]}" "$BASE/decisions"; echo
echo "28) POST /decisions/{id}/link:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"positionId":1}' "$BASE/decisions/$DID/link"; echo
echo "29) POST /decisions/{id}/revise(新增行,旧行原地不变):"
REV=$(curl -s "${AUTH[@]}" "${JSON[@]}" -d '{
  "whyBuy":"修订后理由","whyEntryPrice":"修订后入场价理由","targetPrice":13.0,
  "thesisTags":["NEWS"],"invalidation":"修订后证伪","contingencyScenarios":[],
  "playbookTag":"BREATHING_TRIAL"
}' "$BASE/decisions/$DID/revise"); echo "  $REV"
echo "30) POST /decisions/{id}/scenario-outcome(只翻 matched):"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"outcomes":[{"index":0,"matched":true}]}' "$BASE/decisions/$DID/scenario-outcome"; echo
echo "31) scenario-outcome index 越界 → 422:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"outcomes":[{"index":99,"matched":true}]}' "$BASE/decisions/$DID/scenario-outcome"
echo "32) POST /decisions/{id}/cancel(把首版 $DID 从 filled 改判 cancelled)+ 不存在 id → 404:"; curl -s -X POST "${AUTH[@]}" "$BASE/decisions/$DID/cancel"; echo
curl -s -o /dev/null -w "  status=%{http_code}\n" -X POST "${AUTH[@]}" "$BASE/decisions/999999/cancel"
echo "33) GET /decisions?status=pending(首版 $DID 已 cancelled 不在内,应只剩 29) 的修订行):"; curl -s "${AUTH[@]}" "$BASE/decisions?status=pending"; echo

echo ">> 冒烟完成。"
