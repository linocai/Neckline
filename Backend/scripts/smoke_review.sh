#!/usr/bin/env bash
# 阶段4 · 4D 周复盘对账引擎冒烟(plan 4D 验收:拖入交割单 → 上传解析 → 对账 →
# 落库 → 历史回放,全走真实 uvicorn + 真实 openpyxl 解析,不是 TestClient 单测)。
#
# 用法:bash scripts/smoke_review.sh
#   · 默认用**临时库**(DB_PATH=/tmp/neckline_smoke_review_*.db)+ 临时 API_TOKEN,
#     不碰生产台账(同 smoke_api.sh 惯例)。
#   · 用真实 data/neckline.db 的 stock_basic/namechange 只读表种一份到临时库
#     (贵州茅台 600519.SH),供格式一(无代码列)反查代码用;若真实库没有该表/
#     该票,退化为跳过(反查失败会体现在 parseWarnings 里,不影响冒烟主流程)。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
PY="${PY:-.venv/bin/python}"
HOST=127.0.0.1
PORT="${PORT:-8098}"
BASE="http://$HOST:$PORT/api/v1"

export DB_PATH="${DB_PATH:-/tmp/neckline_smoke_review_$$.db}"
export API_TOKEN="${API_TOKEN:-smoke_review_token_16_chars_ok}"
export NECKLINE_ENABLE_MORNING_TASKS=0
TOKEN="$API_TOKEN"
AUTH=(-H "Authorization: Bearer $TOKEN")
JSON=(-H "Content-Type: application/json")
XLSX_PATH="/tmp/neckline_smoke_review_$$.xlsx"

echo ">> 建临时库 schema + 贵州茅台参考行..."
"$PY" - <<PYEOF
from pathlib import Path
from neckline.db import init_schema, connection

db_path = Path("$DB_PATH")
init_schema(db_path=db_path)
with connection(db_path=db_path) as conn:
    conn.execute(
        "INSERT OR REPLACE INTO stock_basic (ts_code,symbol,name,industry,market,list_date,delist_date,list_status) "
        "VALUES ('600519.SH','600519','贵州茅台','白酒','主板','20010827',NULL,'L')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO namechange (ts_code,name,start_date,end_date,ann_date,change_reason) "
        "VALUES ('600519.SH','贵州茅台','20061009',NULL,NULL,NULL)"
    )
print("  种子完成")
PYEOF

echo ">> 生成一份真实格式一交割单 xlsx(贵州茅台一买一卖)..."
"$PY" - <<PYEOF
from datetime import datetime
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "对账单"
ws.append(["交割单整理(冒烟测试样例)"])
ws.append([])
ws.append(["交易日期","券商来源","业务名称","证券名称","成交数量","股份余额","费用","发生金额","资金余额","备注"])
ws.append([datetime(2026,7,14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""])
ws.append([datetime(2026,7,16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""])
wb.save("$XLSX_PATH")
print("  已写", "$XLSX_PATH")
PYEOF

echo ">> 起 uvicorn :$PORT (DB_PATH=$DB_PATH) ..."
"$PY" -m uvicorn neckline.api.app:app --host "$HOST" --port "$PORT" --log-level warning >/tmp/neckline_smoke_review.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true; rm -f "$DB_PATH" "$DB_PATH"-* "$XLSX_PATH" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" "$BASE/health" 2>/dev/null | grep -q 200 && break
  sleep 1
done

echo "1) health(免鉴权):"; curl -s "$BASE/health"; echo
echo "2) 无 token 上传 → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" -F "files=@$XLSX_PATH;type=application/octet-stream" "$BASE/review/upload"
echo "3) 上传真实交割单 xlsx(带 token):"
UPLOAD=$(curl -s "${AUTH[@]}" -F "files=@$XLSX_PATH;type=application/octet-stream" "$BASE/review/upload")
echo "$UPLOAD" | "$PY" -m json.tool
WEEK=$(echo "$UPLOAD" | "$PY" -c "import sys,json;d=json.load(sys.stdin);print(d['weeks'][0]['week'] if d['weeks'] else '')")
if [ -z "$WEEK" ]; then
  echo "!! 未解析出任何周,冒烟失败"; exit 1
fi
echo "  解析出周: $WEEK"
echo "4) GET /review?week=$WEEK(历史回放,应命中刚落库的结果):"
curl -s "${AUTH[@]}" "$BASE/review?week=$WEEK" | "$PY" -m json.tool
echo "5) GET /review?week=2099-W01(不存在的周,应 found=false 而非 404):"
curl -s "${AUTH[@]}" "$BASE/review?week=2099-W01"; echo
echo "6) PUT settings/review-col-map + GET settings 回读:"
curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"colMap":{"手续费":"费用合计"}}' -X PUT "$BASE/settings/review-col-map"; echo
curl -s "${AUTH[@]}" "$BASE/settings"; echo
echo ">> 冒烟完成。"
