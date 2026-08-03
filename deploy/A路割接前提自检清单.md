# A 路割接前提自检清单(V2-⑭-D 落档;执行窗口 = ⑯-G)

> **本文件是 ⑭-D 的交付物,执行在 ⑯-G。** ⑭ 只负责把「A 路凭什么成立」写成一份
> **可逐条现场核对**的清单;⑯-G 的 builder 照单跑,**任何一条不成立就停手回 planner**,
> ⛔ 不许"先割了再说"。
>
> **D2 = A 路已由用户拍板,只有一条路,不必犹豫**(PROJECT_PLAN §五 D1–D8 / ⑭-D)。

---

## 〇、A 路是什么,以及它靠什么成立

**A 路**:新机挂**新子域** `nk.linotsai.top`;老机 `ln.linotsai.top` **原样服务**到 ⑰
双端换包完成 → **老 App 打老机、新 App 打新机,两者不交叉** → V2 契约得以**一次性
换血、不留过渡键**。

**它成立的全部依据只有一句**:*那两拨客户端永远不会打到同一个服务端。*

一旦这句话破了(例如新机 nginx 顺手也接管了 `ln`),**老 App 会当场撞上 V2 契约**,
而 V2 已经删了它硬解码的键 —— 后果不是"少个字段",是**整份报告解不出、今日计划页
全空**(逐条见 `archive/V2_契约三方对拍_20260803.md` §〇 的三处硬失败)。

> 📌 **两步淘汰纪律本身没有被废除**:「先客户端可选解码、下版服务端才删键」仍是
> CLAUDE.md 铁律,V2 之后的版本照守。V2 只是**靠换机窗口结构性地绕开了它** ——
> 而这份清单就是那个窗口的**看门人**。窗口关不严,纪律就白绕了。

---

## 一、前置条件(⑯-G 开跑前逐条现场确认)

### P1 · 新机 nginx **只**服务 `nk.linotsai.top`

判据(在**新机**上跑):

```bash
# ① 站点配置里出现的 server_name 只能有 nk(以及可选的 IP / default_server)
sudo grep -rn "server_name" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/
# ② 反向判据:全量配置里**零** ln 字样
sudo nginx -T 2>/dev/null | grep -n "ln\.linotsai\.top"   # 期望:无输出
```

- ✅ 通过 = `server_name` 只有 `nk.linotsai.top`,且第 ② 条**零命中**。
- ❌ 不通过 = **停手**。哪怕是「反正现在 DNS 还没指过来,先写上以后省事」也不行 ——
  DNS 是用户手上的开关,配置先写好就等于把闸门交给了一次误操作。

### P2 · 新机没有 `default_server` 兜底吃下任意 Host

判据(新机):

```bash
sudo nginx -T 2>/dev/null | grep -n "default_server"
# 若存在 default_server,确认它 return 444 / 421,而不是把请求转给 V2 后端
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: ln.linotsai.top' http://127.0.0.1/api/v1/health
```

- ✅ 通过 = 带 `Host: ln.linotsai.top` 打新机,**不返回 200**(444 / 421 / 404 均可)。
- ❌ 返回 200 且是 V2 的 `/health` = **停手**:老域名一旦被解析或被谁手工指过来,
  就会直接吃到 V2 契约。

### P3 · `nk.linotsai.top` 已解析到**新机**(用户侧动作,§八 第 14 项)

```bash
dig +short nk.linotsai.top          # 期望 = 新机公网 IP
dig +short ln.linotsai.top          # 期望 = **老机**公网 IP,与上一条不同
```

- ✅ 通过 = 两个 IP **不同**,且各自指向预期那台机器。
- ❌ 两个域名解析到同一台 = **停手回 planner**(A 路的物理前提直接不存在)。
- ⚠ 顺序纪律:**解析生效后才申请证书**(`dig` 拿到新机 IP 才是可以往下走的判据)。

### P4 · 老机在 ⑰ 完成前**不停机、不改代码、不改 DNS**

判据(在**老机**上跑,只读):

```bash
systemctl is-active neckline.service                 # 期望 active
curl -s https://ln.linotsai.top/api/v1/health        # 期望 {"status":"ok","version":"v1.5.2"}
cd /opt/neckline && sudo -u neckline git rev-parse HEAD   # 记下来,⑰ 之后复核未变
```

- ✅ 通过 = `/health` 仍是 **`v1.5.2`**、服务 active、git 号与本次记录一致。
- ❌ 老机 `/health` 已经是 `v2.0.0` = **有人把新代码推到老机了** → 停手,A 路已破。
- ⚠ **老机上不许跑 `sync_code.sh`**。⑯ 的所有部署动作**只对新机**。

### P5 · 新机 `/health` 返 `v2.0.0`,老机仍返 `v1.5.2`

```bash
curl -s https://nk.linotsai.top/api/v1/health    # 期望 v2.0.0
curl -s https://ln.linotsai.top/api/v1/health    # 期望 v1.5.2(**必须仍是老版本**)
```

- ✅ 通过 = 两个版本号**不同**,各自对应各自的机器。
- ❌ 两边同版本 = 要么老机被升级了(P4 破),要么两个域名指向同一台(P3 破)。

---

## 二、割接后的复核(⑯-H 收证据时一并留痕)

| # | 复核项 | 命令 / 判据 | 期望 |
|---|---|---|---|
| R1 | 新机不接管老域名 | `sudo nginx -T \| grep ln.linotsai.top` | 零命中 |
| R2 | 老机原样在跑 | `curl .../ln .../health` | `v1.5.2`,git 号与 P4 记录一致 |
| R3 | 新机契约是 V2 | `curl .../nk .../health` | `v2.0.0` |
| R4 | 老 App 打不到新机 | 带 `Host: ln...` 打新机 IP | 非 200 |
| R5 | 证书只覆盖 nk | `sudo certbot certificates` | 域名列表**只有** `nk.linotsai.top` |

---

## 三、不成立时怎么办(⛔ 不许自行变通)

| 现象 | 判定 | 动作 |
|---|---|---|
| 新机 nginx 里出现 `ln.linotsai.top` | **A 路前提破** | 停手,删掉那段配置后重新走 P1;若是有意为之(如"想做灰度"),**回 planner**,不自行决定 |
| 两个域名解析到同一 IP | **A 路前提破** | 停手回 planner + 请用户核对 DNS(解析归用户,§八 第 14 项)|
| 老机 `/health` 已是 v2 | **A 路前提破** | 停手,查清是谁部署的;⛔ 不许"既然已经上了就将就" |
| 用户临时想让老域名也走新机 | **等于放弃 A 路** | 停手回 planner:那需要**两步淘汰**(先发一版客户端把硬解码键改 `decodeIfPresent`,下版服务端才删键)+ ⑰ 换包前移,是一次重排期,不是一个 nginx 改动 |

---

## 四、与 ⑰ 的交接

- ⑰(双端换包)完成**并经用户确认**之后,`ln.linotsai.top` 才进入**退役讨论**;
  在那之前本清单的 P4 一直有效。
- 老机退役与 `linon.db` / `neckline.db` 老库归档**由用户决定**(§八 第 8 项),
  ⛔ builder 不自行退役老机。

---

## 五、本清单的机器可判部分

P1 / P2 / P5 / R1 / R4 都是**一条命令 + 一个明确期望**,⑯-G 时可以直接串成一个
`deploy/preflight_a_route.sh`。**⑭ 不预写那个脚本** —— 它要跑在服务器上,而
①–⑮ 期间**不碰任何服务器**(⑯ 的工序时序纪律),现在写一个跑不了的脚本只会给人
"已经验过了"的错觉。到 ⑯-G 现场照本清单写、当场跑、当场留痕。
