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
echo "2) 无 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "$BASE/settings"
echo "3) 错 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" -H "Authorization: Bearer nope" "$BASE/settings"
# —— (4~5) `report/latest` / `board` → **V2.5.0 S1 已删端点**(K8 报告与看板整链退役,
#    PROJECT_PLAN §5.12),步骤号留空。S7 的替代品是 `/api/selection/latest`(步骤 38)。
echo "6) settings(默认):"; curl -s "${AUTH[@]}" "$BASE/settings"; echo
# —— (7~8) `PUT /settings/llm` → **V2-⑬ 已删端点**(明文 key 那条),步骤号留空。
echo "9) PUT settings/push(**V2-⑪ 起全量覆盖式、必须给全每一个 kind**):"
# 🔴 payload 从 `GET /settings` 现取,⛔ 不写死字段名 —— 写死过一次就是 (7~8) 的下场:
#    kind 清单一变,这一步默默变成"打印一条 422 正文"。
curl -s "${AUTH[@]}" "$BASE/settings" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print(json.dumps({'kinds':{k['kind']:True for k in d['push']['kinds']}}))" > /tmp/neckline_smoke_push.json
curl -s "${AUTH[@]}" "${JSON[@]}" -d @/tmp/neckline_smoke_push.json -X PUT "$BASE/settings/push"; echo
echo "9b) 缺键 → 422(invalid_push_kinds):"; curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -d '{"kinds":{"report_ready":true}}' -X PUT "$BASE/settings/push"
# —— (10~13) 持仓开/列/平/重复平 → **V2.5.0 S1 持仓整条线退役**(裁定 11),步骤号留空 ——
# —— (14) 问询台 → **V2.1-① 整链退役**,步骤号留空 ——
echo "15) device register:"; curl -s "${AUTH[@]}" "${JSON[@]}" -d '{"token":"smoke-device","platform":"ios"}' "$BASE/devices"; echo

# —— (16~24) v1.1-C 自选池 + 同花顺 txt 对账/导出 → **V2-⑬-11 整链删除**,步骤号留空 ——
# —— (25~33) `/decisions*` 决策日志 → **V2.5.0 S1 已删端点**,步骤号留空 ——

# —— V2.1-⑤ 复盘板块聚合端点 ————————————————————————————————————————————
# 🔴 判据是 **200 而不是 404**:该端点一律不 404,空态走 `available=false` +
# 可读原因(这正是 V2.1「零新增 reason」的由来)。空库跑出来三段全 false 是**对的**。
# ⚠ V2.5.0 S1:原「五段」里的 `preference` / `capability` 随 `profile/` 退役而删除,
#    V2.2-④ 追加的三段随双时钟复盘退役而删除 —— ⛔ 不留恒空的壳。
echo "34) GET /review/overview(空库 → 200,三段各自 available + 可读原因):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/review/overview"
curl -s "${AUTH[@]}" "$BASE/review/overview" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  window=%s→%s %s'%(d.get('weekStart'),d.get('weekEnd'),d.get('weekKey')))
for k in ('calibration','reconcile','observations'):
    s=d.get(k) or {}
    d2=s.get('detail') or {}
    why=s.get('unavailableReason') or (d2.get('note') if isinstance(d2,dict) else '') or ('%d 条'%len(s.get('items') or []))
    print('  %-13s available=%-5s %s'%(k,s.get('available'),str(why)[:52]))"
echo "35) GET /eval/weekly(空库 → 200 + available=false,⛔ 不在线补算):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/eval/weekly"
curl -s "${AUTH[@]}" "$BASE/eval/weekly" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  available=%s reason=%s'%(d.get('available'),(d.get('unavailableReason') or '')[:60]))"

# —— V2.5.0 S4 覆盖率成绩线 ——————————————————————————————————————————————
# 🔴 判据是 **200 + `coverageAll: null`**,⛔ 不是 0:空库 = 昨天还没有清单。
# 这条线以涨停为口径、**不读参数包**,是参数标定完成之前唯一能跑起来的尺子(§5.8.1)。
echo "37) GET /scoreboard/coverage(空库 → 200,days 为空):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/scoreboard/coverage"
curl -s "${AUTH[@]}" "$BASE/scoreboard/coverage?window=5" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  window=%s days=%d latestMisses=%d reasons=%s'%(
    d.get('window'),len(d.get('days') or []),len(d.get('latestMisses') or []),d.get('missReasonCounts')))
for r in (d.get('days') or [])[:1]:
    print('  %s 涨停 %s · coverageAll=%r(None=昨天还没有清单,⛔ 不是 0)'%(
        r['tradeDate'],r['limitUpCount'],r['coverageAll']))"
echo "37b) 无 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "$BASE/scoreboard/coverage"

# —— 退役面反向印证:已删端点必须 404 ————————————————————————————————————
echo "36) 已退役端点 → 全 404(V2.1-① 问询台 + V2.5.0 S1 的 K8 整链):"
for M in "POST $BASE/inquiry" "GET $BASE/inquiries" "GET $BASE/report/latest" \
         "GET $BASE/board" "GET $BASE/positions" "GET $BASE/decisions" \
         "GET $BASE/alerts" "GET $BASE/baskets" "GET $BASE/packs" \
         "GET $BASE/auction" "GET $BASE/market-regime" "GET $BASE/profile/preference" \
         "GET $BASE/clocks/selection" "GET $BASE/review/handoff"; do
  set -- $M
  printf "  %-5s %-44s " "$1" "$2"
  curl -s -o /dev/null -w "status=%{http_code}\n" -X "$1" "${AUTH[@]}" "${JSON[@]}" "$2"
done

# —— V2.5.0 S7 · 选股(报告三态 + 双日期契约)——————————————————————————————
echo "38) GET /selection/latest —— 三态 + 双日期(⚠ 空库 → not_run,⛔ 不是 500):"
curl -s "${AUTH[@]}" "$BASE/selection/latest" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  state=%s reportDate=%s tradeDate=%s listingSize=%r'%(
    d.get('state'),d.get('reportDate'),d.get('tradeDate'),d.get('listingSize')))
print('  首行:%s'%d.get('headline'))
print('  ⚠ listingSize=None 表示「今天没跑成」,⛔ 客户端不许显示成 0')
for g in (d.get('gaps') or [])[:3]: print('   - %s'%g)"
echo "38b) 无 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "$BASE/selection/latest"
echo "38c) 查一个没有报告的交易日 → 404:"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/selection/19900101"
echo "38d) 日期格式非法 → 422:"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/selection/not-a-date"


# —— V2.5.0 S8 · 次日核对表与 D1 结算(裁定 10)——————————————————————————————
# 🔴 **G20 的冒烟侧**:`/checklist/{date}` 的响应体里⛔ 没有「成立」这个取值。
#    空库 → 404 = **那天没跑过那一拍**(⛔ 不是「跑了、表是空的」)。
echo "39) GET /checklist/{date}(空库 → 404,那天没跑过那一拍):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/checklist/20240430"
echo "39b) 日期格式非法 → 422:"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/checklist/not-a-date"
echo "39c) 无 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "$BASE/checklist/20240430"
echo "40) GET /scoreboard/verdicts/{date}(10:00 结算拍的三分支终值;空库 → 200 + 空数组):"
curl -s "${AUTH[@]}" "$BASE/scoreboard/verdicts/20240430" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  tradeDate=%s verdicts=%d'%(d.get('tradeDate'),len(d.get('verdicts') or [])))
print('  ⚠ verdict=null 表示「今天还没定案」,⛔ 不是「观察」')
print('  🔴 它挂在 scoreboard 下 = 属于成绩线,⛔ 不进选股首屏(裁定 10)')"

# —— V2.5.0 S9/S10 · 个股详情与预案修改入口 ————————————————————————————————
echo "41) GET /selection/{date}/stock/{code}(不在清单里 → 404):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" \
  "$BASE/selection/20240430/stock/600001.SH"
echo "42) POST .../playbook(不在清单里 → 404;⛔ 不给不存在的票冻预案):"
curl -s -o /dev/null -w "  status=%{http_code}\n" -X POST "${AUTH[@]}" "${JSON[@]}" \
  -d '{}' "$BASE/selection/20240430/stock/600001.SH/playbook"


# —— V2.5.0 S11 · 交割单分析台(架构 §六:解析 / 装订 / 结论存档,🔴 零 LLM)————
echo "43) GET /review/bindery?week=(没上传过交割单 → 200 + found=false,⛔ 不是 404):"
curl -s "${AUTH[@]}" "$BASE/review/bindery?week=2026-W29" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  found=%s binding=%r'%(d.get('found'), d.get('binding')))
print('  reason:%s'%(d.get('unavailableReason') or ''))
print('  ⚠ 「没有」不是「没取到」:装订的输入只能由用户上传,系统补不出来')"
echo "43b) 无 token → 401:"; curl -s -o /dev/null -w "  status=%{http_code}\n" "$BASE/review/bindery?week=2026-W29"
echo "44) POST /review/conclusions(存一版结论,append-only):"
curl -s "${AUTH[@]}" "${JSON[@]}" -X POST "$BASE/review/conclusions" \
  -d '{"week":"2026-W29","title":"冒烟结论","body":"这是一条冒烟写入的结论。","tags":["smoke"]}' \
  | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  week=%s version=%s'%(d.get('week'), (d.get('latest') or {}).get('version')))"
echo "44b) 再存一版 → version=2,且 v1 一个字不动:"
curl -s "${AUTH[@]}" "${JSON[@]}" -X POST "$BASE/review/conclusions" \
  -d '{"week":"2026-W29","title":"改口径","body":"复看之后改判。"}' >/dev/null
curl -s "${AUTH[@]}" "$BASE/review/conclusions?week=2026-W29" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
vs=d.get('versions') or []
print('  versions=%s'%[v['version'] for v in vs])
print('  v1 标题仍是 %r(⛔ append-only,老版本不许被改)'%(vs[0]['title'] if vs else None))"
echo "44c) 空 body → 422(⛔ 不静默截断、不静默接受):"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "${JSON[@]}" -X POST \
  "$BASE/review/conclusions" -d '{"week":"2026-W29","title":"t","body":""}'
echo "44d) 检索(下周可检索;⚠ 每周只出**最新版**):"
# ⚠ 中文 query 必须走 `-G --data-urlencode`:直接拼进 URL 会被服务端当成非法字符,
#    curl 拿回空响应,而这一步会「安静地」变成一次 JSON 解析崩溃(踩过一次)。
for Q in 复看 冒烟; do
  printf "  q=%-4s " "$Q"
  curl -s -G "${AUTH[@]}" --data-urlencode "q=$Q" "$BASE/review/conclusions" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
ms=d.get('matches') or []
print('matches=%s'%[(m['week'],m['version']) for m in ms])"
done
echo "  ⚠ 「冒烟」只在 v1 里 → 命中为空是**对的**:检索只看每周最新版"
echo "45) GET /review/overview —— 结论存档段(还没写 → available=true + found=false):"
curl -s "${AUTH[@]}" "$BASE/review/overview?week=20990101" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
c=d.get('conclusions') or {}
print('  available=%s found=%s'%(c.get('available'), (c.get('detail') or {}).get('found')))
print('  ⛔ 「还没写结论」不等于「这周没问题」')"

# —— V2.5.0 S13 · K8 只读追溯(裁定 6)————————————————————————————————————
echo "46) GET /legacy/k8/baskets(只读追溯唯一入口;新库 → 从没跑过 K8):"
curl -s "${AUTH[@]}" "$BASE/legacy/k8/baskets?date=20260724" | "$PY" -c "
import sys,json;d=json.load(sys.stdin)
print('  available=%s found=%s basketCount=%s'%(
    d.get('available'), d.get('found'), (d.get('overview') or {}).get('basketCount')))
print('  reason:%s'%(d.get('reason') or ''))"
echo "46b) 写方法 → 405(路由只注册了 GET,⛔ 不是 404):"
for M in POST PUT DELETE; do
  printf "  %-6s " "$M"
  curl -s -o /dev/null -w "status=%{http_code}\n" -X "$M" "${AUTH[@]}" "${JSON[@]}" "$BASE/legacy/k8/baskets"
done
echo "46c) 日期格式非法 → 422:"
curl -s -o /dev/null -w "  status=%{http_code}\n" "${AUTH[@]}" "$BASE/legacy/k8/baskets?date=2026-07-24"

echo ">> 冒烟完成。"
