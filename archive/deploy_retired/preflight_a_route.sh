#!/usr/bin/env bash
# A 路割接前提自检(plan §五 V2-⑯-G;判据全文与"不成立时怎么办"见
# `archive/deploy_retired/A路割接前提自检清单.md` —— **本脚本只是那份清单里"机器可判"的那几条**,
# 不能替代人读清单)。
#
# **在新机上跑**(部分判据需要 sudo 读 nginx 全量配置):
#   sudo bash /opt/neckline/archive/deploy_retired/preflight_a_route.sh
#
# 退出码:0 = 全过;1 = 有判据不通过(⛔ **停手**,别"先割了再说")。
#
# ⚠ **DNS 判据不用 `dig`**:本机 / 开发机可能走代理,`dig` 会返回 198.18.x fake-ip
#   (项目 CLAUDE.md 登记过)。一律用 DoH(阿里 223.5.5.5 的 JSON 接口)拿权威答案。
# ⚠ **`ln` 判据按"不指向本机"判,不按"必须指向老机"判**:2026-08-04 现场实测
#   `ln.linotsai.top` 的 A 记录已从 DNS 消失(NXDOMAIN,非本次操作所致,已上报)。
#   A 路要的是"两拨客户端不交叉",NXDOMAIN 比"指向老机"更强地满足这一点;真正要
#   守死的是**它绝不能解析到新机**,以及**新机绝不接管这个 Host**。
# ⚠ **P1 的 `ln` 命中判据必须先剥掉注释行**(2026-08-04 ⑰ 现场修):⑯-G 落产的
#   `npm-custom-http.conf` 文件头**自己就写着**「绝不接管 ln.linotsai.top」这句护栏注释,
#   裸 grep 会把它判成"配置里出现了 ln" → 每次都红。**一个对自己的注释报警的闸门,
#   等于没有闸门** —— 下次真出现一条 `server_name ln...` 时人只会当它又是那条老误报。
#   故此处一律用 `grep -vE '^\s*#'` 先剥注释再判。
set -uo pipefail

NK_DOMAIN="${NK_DOMAIN:-nk.linotsai.top}"
LN_DOMAIN="${LN_DOMAIN:-ln.linotsai.top}"
NEW_IP="${NEW_IP:-114.66.0.38}"
FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

_doh_a() {   # $1=域名 → 打印全部 A 记录(每行一个);无记录则不打印
  curl -fsS --max-time 10 "https://223.5.5.5/resolve?name=$1&type=A" 2>/dev/null \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);[print(a["data"]) for a in d.get("Answer",[]) if a.get("type")==1]' 2>/dev/null
}

hdr "P1 · 新机 nginx **只**服务 ${NK_DOMAIN}"
if command -v nginx >/dev/null 2>&1 && nginx -T >/dev/null 2>&1; then
  NAMES=$(nginx -T 2>/dev/null | grep -E '^\s*server_name' | sed 's/^[[:space:]]*//' | sort -u)
  printf '%s\n' "$NAMES" | sed 's/^/      /'
  if nginx -T 2>/dev/null | grep -vE '^\s*#' | grep -q "${LN_DOMAIN}"; then
    bad "系统 nginx 全量配置里出现了 ${LN_DOMAIN}(非注释行)—— A 路前提破,停手删掉那段"
  else
    ok "系统 nginx 全量配置零命中 ${LN_DOMAIN}(已剥注释)"
  fi
else
  warn "系统 nginx 未运行 / 无法 nginx -T(新机 80/443 被 nginx-proxy-manager 容器占用时属预期)"
fi
# 反代若由容器(NPM 等)承担,判据换成读它生成的站点配置
if [ -d /opt/npm/data/nginx ]; then
  # 剥注释后再判(见文件头 P1 说明);另打一行"含注释的原始命中数"供人眼核对,
  # 好让"只命中在注释里"这件事一眼可见,而不是被静默吃掉。
  _RAW_HITS=$(grep -rns "${LN_DOMAIN}" /opt/npm/data/nginx/ 2>/dev/null | wc -l | tr -d ' ')
  if grep -rhs "${LN_DOMAIN}" /opt/npm/data/nginx/ 2>/dev/null | grep -vE '^\s*#' | grep -q .; then
    bad "nginx-proxy-manager 的站点配置里出现了 ${LN_DOMAIN}(非注释行)—— A 路前提破,停手"
  else
    ok "nginx-proxy-manager 站点配置零命中 ${LN_DOMAIN}(已剥注释;含注释的原始命中 ${_RAW_HITS} 处 = 护栏注释本身)"
    grep -rhs 'server_name' /opt/npm/data/nginx/proxy_host/*.conf 2>/dev/null \
      | sed 's/^[[:space:]]*/      (NPM) /' | sort -u
  fi
fi

hdr "P2 · 带 Host: ${LN_DOMAIN} 打新机,**不得**返回 200"
for scheme_port in "http 80" "https 443"; do
  set -- $scheme_port
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 ${3:-} -k \
         -H "Host: ${LN_DOMAIN}" "$1://127.0.0.1:$2/api/v1/health" 2>/dev/null || echo 000)
  if [ "$CODE" = "200" ]; then
    bad "$1://127.0.0.1:$2 带 Host: ${LN_DOMAIN} 返回 200 —— 老域名一旦被指过来就会吃到 V2 契约,停手"
  else
    ok "$1://127.0.0.1:$2 带 Host: ${LN_DOMAIN} → HTTP $CODE(非 200,合格)"
  fi
done

hdr "P3 · DNS:${NK_DOMAIN} 指向新机;${LN_DOMAIN} **绝不**指向新机"
NK_IPS=$(_doh_a "$NK_DOMAIN"); LN_IPS=$(_doh_a "$LN_DOMAIN")
echo "      ${NK_DOMAIN} → ${NK_IPS:-<无 A 记录>}"
echo "      ${LN_DOMAIN} → ${LN_IPS:-<无 A 记录 / NXDOMAIN>}"
if [ "$NK_IPS" = "$NEW_IP" ]; then ok "${NK_DOMAIN} 解析到新机 ${NEW_IP}"
else bad "${NK_DOMAIN} 未解析到新机 ${NEW_IP}(拿到 '${NK_IPS:-空}')—— 解析生效才准申请证书"; fi
if [ -z "$LN_IPS" ]; then
  warn "${LN_DOMAIN} 无 A 记录(NXDOMAIN)—— 不交叉这一点成立,但**老 App 当前也连不上任何服务端**,须上报用户"
elif echo "$LN_IPS" | grep -qx "$NEW_IP"; then
  bad "${LN_DOMAIN} 解析到了新机 —— A 路物理前提直接不存在,停手回 planner"
else
  ok "${LN_DOMAIN} 解析到 ${LN_IPS}(非新机)"
fi

hdr "P5 · 版本号:新机 v2.0.0 / 老机 v1.5.2(两者必须不同)"
NEW_H=$(curl -fsS --max-time 10 http://127.0.0.1:8002/api/v1/health 2>/dev/null || echo '<新机后端不可达>')
echo "      新机(直连 8002):${NEW_H}"
case "$NEW_H" in *'"version":"v2.0.0"'*) ok "新机后端 = v2.0.0" ;;
                 *) bad "新机后端不是 v2.0.0:${NEW_H}" ;; esac
if [ -n "${LN_IPS:-}" ]; then
  OLD_H=$(curl -fsS --max-time 10 --resolve "${LN_DOMAIN}:443:${LN_IPS}" "https://${LN_DOMAIN}/api/v1/health" 2>/dev/null || echo '<不可达>')
  echo "      老机(${LN_DOMAIN}):${OLD_H}"
  case "$OLD_H" in *'"version":"v2.0.0"'*) bad "老机 /health 已是 v2.0.0 —— 有人把新代码推到老机了,A 路已破" ;;
                   *'"version":'*) ok "老机仍是老版本(未被误升级)" ;;
                   *) warn "老机 /health 不可达,无法核对(见上方 ${LN_DOMAIN} 无解析)" ;; esac
else
  warn "${LN_DOMAIN} 无解析,跳过老机版本核对(改用 IP + Host 头人工核)"
fi

hdr "结论"
if [ "$FAIL" = 0 ]; then printf '  \033[32mA 路前提自检全过。\033[0m\n'; else
  printf '  \033[31m有判据未通过 —— ⛔ 停手,照 A路割接前提自检清单.md §三 处理,不许自行变通。\033[0m\n'; fi
exit $FAIL
