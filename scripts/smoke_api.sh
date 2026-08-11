#!/usr/bin/env bash
# 阶段4 · 4A FastAPI 脊椎冒烟(plan 4A 验收:本地 uvicorn 起 → curl 全端点闭环)。
# 起 uvicorn → health(免鉴权 200)+ 无/错 token 401 + 报告/看板 degraded 空态 +
# 持仓 open→list→close→重复 close 404 + 设置(GET / PUT push 全量 kind)+ 设备注册
# + **V2.1-⑤ 复盘两端点(空态 200,⛔ 不是 404)** + **V2.1-① 问询台三端点 404**。
#
# ⚠ **步骤号一律留空不重排**(与 plan 完工记录、契约对拍表逐条对得上比"号码连续"重要):
# (7~8)(16~24)(26)(28~33) = 已删端点,(14) = V2.1-① 问询台。
# 🔴 **2026-08-08(⑧)修复**:此前脚本自 V2-⑬ 起在第 25 步硬中断(`POST /decisions`
# 改成"可选补充入口"后不再回 `id`,`set -e` 当场退出),**其后所有断言从未跑过**;
# 另有三步打已删端点/旧契约、只打印 404/422 正文不中断,看起来像正常输出。详见
# `archive/对照表/V2.1_契约对拍_20260808.md` §4.1。
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
# —— (7~8) `PUT /settings/llm` → **V2-⑬ 已删端点**(明文 key 那条,契约测试的删除面
#    第 8 项),步骤号留空;⑧(2026-08-08)顺手收口 —— 此前这两步只是打印 404 正文,
#    看起来像正常输出,实际什么都没验。
echo "9) PUT settings/push(**V2-⑪ 起全量覆盖式、必须给全每一个 kind**):"
# 🔴 payload 从 `GET /settings` 现取,⛔ 不写死字段名 —— 写死过一次就是 (7~8) 的下场:
#    kind 清单一变,这一步默默变成"打印一条 422 正文"。
curl -s "${AUTH[@]}" "$BASE/settings" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print(json.dumps({'kinds':{k['kind']:True for k in d['push']['kinds']}}))" > /tmp/neckline_smoke_push.json
curl -s "${AUTH[@]}" "${JSON[@]}" -d @/tmp/neckline_smoke_push.json -X PUT "$BASE/settings/push"; echo
echo "9b) 缺键 → 422(invalid_push_kinds):"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"kinds":{"report_ready":true}}' -X PUT "$BASE/settings/push"
echo "10) open:"; OPEN=$(curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600519.SH","name":"贵州茅台","buy_price":1500.0,"qty":100,"entry_reason":"回调低吸"}' "$BASE/positions"); echo "  $OPEN"
PID=$(echo "$OPEN" | "$PY" -c "import sys,json;print(json.load(sys.stdin)['position_id'])")
echo "11) list:"; curl -s "${AUTH[@]}" "$BASE/positions"; echo
echo "12) close:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"sell_price":1520.0}' "$BASE/positions/$PID/close"; echo
echo "13) 重复 close → 404:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"sell_price":1520.0}' "$BASE/positions/$PID/close"
# —— (14) 问询台 → **V2.1-① 整链退役**,步骤号留空 ——
echo "15) device register:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"token":"smoke-device","platform":"ios"}' "$BASE/devices"; echo

# —— (16~24) v1.1-C 自选池 + 同花顺 txt 对账/导出 → **V2-⑬-11 整链删除**,步骤号留空 ——

# —— v1.2-B 预注册决策日志 → **V2-⑩-C 强制表单退役**(`decision_log` 停写留档)————
# `POST /decisions` 自 v2.0.0 起是「用户可选补充入口」:全部字段可选、空提交也 200,
# 落 `user_actions` 的 label/voice_note 两 kind,**不再回 `id`**。
echo "25) POST /decisions(可选补充入口:labels + voiceNote → recorded 两项):"
curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"code":"600001.SH","labels":["THEME_SHIFT"],"voiceNote":"冒烟一句"}' "$BASE/decisions"; echo
echo "25b) 空提交同样 200 且 recorded=[](⛔ 不是 400 —— 九项强制表单已退役):"
curl -s "${AUTH[@]}" "${JSON[@]}" -d '{}' "$BASE/decisions"; echo
# —— (26) 非法论点标签码 → **随 ⑩-C 强制表单退役**(`thesisTags` 已不是入参),步骤号留空 ——
echo "27) GET /decisions(历史归因只读;停写后新库应为空):"; curl -s "${AUTH[@]}" "$BASE/decisions"; echo
# —— (28~33) `/decisions/{id}/{link,cancel,revise,scenario-outcome}` → **V2-⑬ 已删端点**
#    (契约测试删除面第 9~12 项),步骤号留空 ——

# —— V2.1-⑤ 复盘板块聚合端点(两条,⑧ 契约对拍「新增面」)——————————————
# 🔴 判据是 **200 而不是 404**:两条端点一律不 404,空态走 `available=false` +
# 可读原因(这正是 V2.1「零新增 reason」的由来)。空库跑出来五段全 false 是**对的**。
echo "34) GET /review/overview(空库 → 200,五段各自 available + 可读原因):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/review/overview"
curl -s "${AUTH[@]}" "$BASE/review/overview" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  window=%s→%s %s'%(d.get('weekStart'),d.get('weekEnd'),d.get('weekKey')))
for k in ('calibration','preference','capability','reconcile','observations'):
    s=d.get(k) or {}
    d2=s.get('detail') or {}
    why=s.get('unavailableReason') or (d2.get('note') if isinstance(d2,dict) else '') or ('%d 条'%len(s.get('items') or []))
    print('  %-13s available=%-5s %s'%(k,s.get('available'),str(why)[:52]))"
echo "35) GET /review/handoff(空库 → 200 + available=false,⛔ 不在线补算):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/review/handoff"
curl -s "${AUTH[@]}" "$BASE/review/handoff" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  available=%s reason=%s'%(d.get('available'),(d.get('unavailableReason') or '')[:60]))"

# —— V2.1-① 问询台整链退役:三条端点必须 404(与 (14) 步骤号留空互为印证)——
echo "36) 问询台三条端点已退役 → 全 404:"
for M in "POST $BASE/inquiry" "GET $BASE/inquiries" "GET $BASE/inquiries/1"; do
  set -- $M
  printf "  %-5s %-40s " "$1" "$2"
  curl -s -o /dev/null -w "status=%{http_code}\n" -X "$1" "${AUTH[@]}" "${JSON[@]}" "$2"
done

echo ">> 冒烟完成。"
