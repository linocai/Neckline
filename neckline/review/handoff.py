"""校准移交件(plan §五 V2.1-⑤):把复盘板块攒下的证据整理成**一份能直接交给策略台**
的 markdown。

🔴 **本模块的全部本事是「读现成的 + 排版」,⛔ 一个数都不算**。

**为什么这条边界是硬的**(⛔ 施工时别改主意):它的两个消费方是
`GET /review/overview` 与 `GET /review/handoff`,两条端点都跑在常驻
`neckline.service` 里、**与盘中哨兵同进程** —— §七 **P0-23** 的原教旨:重活进常驻服务
= `MemoryHigh` 先节流 → 进程陷进回收死循环 = **卡死不报错**,盘中点一次就拖累哨兵。
故校准报告由 ⑥ 的**周度 unit 离线算好落盘**,本模块只读产物;读不到就如实说读不到,
**⛔ 永远不许在线补算**(守门:`tests/test_review_handoff.py` 静态 + 运行期双向断言
两条端点路径零调用 `eval.calibration.build_report`)。

**三态,一个都不许合并**(承 V2 B1「冻结件读不出是独立第三态」的同一条纪律):

    · `ok`            —— 产物在、读得出;
    · `not_generated` —— 产物**根本没有**(⑥ 还没跑到这个窗口 / 这周没有交易日);
    · `corrupt`       —— 产物**在,但 JSON 解不出**。

前两者会自愈(等下一次周度 unit),`corrupt` **不会** —— 把它混进 `not_generated`
就是叫人一直等一份永远好不了的产物。端点文案据此分开写。

**命名不另起一套**:产物文件名由既有 `eval/calibration.py::write_report` 定死
(`calibration_{from}_{to}.{md,json}`),本模块只**解析**这个约定,⛔ 不发明第二套
命名、⛔ 不改 `write_report`。

**观察项清单(`HANDOFF_OBSERVATIONS`)与 §七 Backlog 的闭合**:清单里每个 `id` 都必须
能在 `PROJECT_PLAN.md` §七 里 grep 到字面 `[P3-xx]`(守门单测)。这比"人工记得同步"
可靠 —— 一旦 Backlog 那条被删掉 / 改了 ID,清单当场报红,而不是默默端给用户一条已经
不存在的观察项。⚠ **V2.2-④ 起清单是五条**(`P3-32` / `P3-34` / `P3-37` / 🆕 `P3-49` /
🆕 `P3-51`);`P3-33` **已摘掉** —— 它的主体随门槛制作废,留一条 grep 得到但已经没有
意义的观察项,比没有更糟。

**V2.2-④ 新增 §④「修改建议四分类」**:排版复用
`eval/calibration.render_iteration_section`(⛔ 不在这里另写一份表格),数据来自
落盘产物里的 `iteration` 段。🔴 那一段在分界线未由用户拍板前**不给分类**,移交件
如实把「还缺哪两个数」端给用户 —— 这正是移交件该干的事。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 产物三态(⛔ 不许合并,理由见模块头)
CAL_OK = "ok"
CAL_NOT_GENERATED = "not_generated"
CAL_CORRUPT = "corrupt"

# `write_report` 定死的文件名前缀(⛔ 不另起一套命名)
_ARTIFACT_PREFIX = "calibration_"


@dataclass(frozen=True)
class Period:
    """一期已落盘的周度校准产物。`markdown_path=None` = 只有 `.json`(异常态:
    `write_report` 两份一起写)—— 如实留着这个可能,别假设 `.md` 一定在。"""

    date_from: str
    date_to: str
    json_path: Path
    markdown_path: Optional[Path] = None

    @property
    def label(self) -> str:
        return f"{self.date_from}→{self.date_to}"


def calibration_dir(out_dir: Optional[Path] = None) -> Path:
    """校准产物目录。缺省 = `data/reports/calibration`(与
    `scripts/weekly_calibration.py` 的缺省 `--out` 逐字相同 —— 那是**同一个目录**,
    ⛔ 别在这里换一个)。

    ⚠ **测试必须显式传 `out_dir`**:CLAUDE.md「测试隔离」条 —— `api_env`/`isolated_env`
    只重写三处 `settings` 绑定,**不含 `neckline.config.settings`**,不传就会读到真实
    项目的 `data/reports/`。端点侧由 `api/app.py::_calibration_dir()` 注入。"""
    if out_dir is not None:
        return Path(out_dir)
    from neckline.config import settings

    return settings.data_dir / "reports" / "calibration"


def _parse_stem(stem: str) -> Optional[Tuple[str, str]]:
    """`calibration_20260803_20260807` → `("20260803", "20260807")`;不合规 → `None`
    (⛔ 不抛:目录里混进别的文件是运维现实,不是本模块该崩的理由)。"""
    if not stem.startswith(_ARTIFACT_PREFIX):
        return None
    parts = stem[len(_ARTIFACT_PREFIX):].split("_")
    if len(parts) != 2 or not all(len(p) == 8 and p.isdigit() for p in parts):
        return None
    return parts[0], parts[1]


def list_calibration_artifacts(out_dir: Optional[Path] = None) -> List[Period]:
    """列出已落盘的周度校准产物,**最近的在前**(同 `review/store.list_review_weeks`
    的降序惯例)。目录不存在 / 空 → 空列表(正常场景:⑥ 还没跑过第一次)。"""
    d = calibration_dir(out_dir)
    if not d.exists():
        return []
    out: List[Period] = []
    try:
        candidates = sorted(d.glob(f"{_ARTIFACT_PREFIX}*.json"))
    except OSError:  # 目录权限异常等 —— 读侧永远不比"没有这个目录"更糟
        logger.warning("[handoff] 校准产物目录列举失败:%s", d, exc_info=True)
        return []
    for p in candidates:
        parsed = _parse_stem(p.stem)
        if parsed is None:
            continue
        lo, hi = parsed
        md = p.with_suffix(".md")
        out.append(Period(date_from=lo, date_to=hi, json_path=p,
                          markdown_path=md if md.exists() else None))
    out.sort(key=lambda x: (x.date_to, x.date_from), reverse=True)
    return out


def load_calibration_with_status(
    date_from: str, date_to: str, out_dir: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """**唯一读实现**。返回 `(报告字典 | None, 三态之一)`。

    🔴 三态分开的意义全在这里:`not_generated` 会自愈(等 ⑥ 下一跑),`corrupt`
    **不会** —— 端点必须用不同的话讲这两件事,否则用户会一直等一份永远好不了的产物。
    """
    path = calibration_dir(out_dir) / f"{_ARTIFACT_PREFIX}{date_from}_{date_to}.json"
    if not path.exists():
        return None, CAL_NOT_GENERATED
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # ⛔ 不降级成"还没生成":文件在那儿、它不会自己好,这是要人排查的事故。
        logger.error("[handoff] 校准产物读不出(需人工排查):%s", path, exc_info=True)
        return None, CAL_CORRUPT
    if not isinstance(payload, Mapping):
        logger.error("[handoff] 校准产物不是 JSON 对象(需人工排查):%s", path)
        return None, CAL_CORRUPT
    return dict(payload), CAL_OK


def load_calibration(
    date_from: str, date_to: str, out_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """plan §五⑤ 点名的签名。**薄封装**,读实现只有 `load_calibration_with_status`
    一处 —— 想区分「没生成」与「读不出」的调用方(= 两条端点)用带状态那个。"""
    return load_calibration_with_status(date_from, date_to, out_dir)[0]


def load_calibration_markdown(
    date_from: str, date_to: str, out_dir: Optional[Path] = None,
) -> Optional[str]:
    """已落盘的 `.md` 原文(移交件 §② 原样引用它,**⛔ 不重排版**)。"""
    path = calibration_dir(out_dir) / f"{_ARTIFACT_PREFIX}{date_from}_{date_to}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.error("[handoff] 校准产物 markdown 读不出:%s", path, exc_info=True)
        return None


# ══════════════════════════════════════════════════════════════════════════
# 观察项清单(§七 Backlog 的镜像,守门单测钉死闭合)
# ══════════════════════════════════════════════════════════════════════════

# 每条五个键(plan 定死):`{id, title, question, evidence_needed, status}`。
# ⚠ `status` 是**描述性文字不是枚举** —— 这是一份要给人读的移交件,不是状态机;
#   ⛔ 别把它当成客户端可以 switch 的码。
# ⚠ **id 必须是 §七 里那个稳定 ID 的字面量**(守门 grep `[P3-xx]`):
#   P3-34 在 §七 里是**一条**(内含 (a)(b) 两项),这里也只放一条 —— 拆成
#   `P3-34a`/`P3-34b` 会让守门 grep 不到,而那条守门正是清单与 Backlog 的唯一闭合。
HANDOFF_OBSERVATIONS: Tuple[Dict[str, str], ...] = (
    {
        "id": "P3-32",
        "title": "主归属 lift 门槛与「小簇 vs 大概念」",
        "question": (
            "涨停簇成分常只 2–5 只,`占比 ÷ 全市场占比` 实测 70–90 倍 lift,与行业闸"
            "(成员以百计)不是一个统计量级 → 同一只票跨「小簇篮 + 大概念篮」时主归属"
            "几乎恒落在小簇篮。① 最小成分数门槛 = 5 对涨停簇合不合适?"
            "② 「小簇篮天然该输给大概念篮」这个方向本身对不对(也可能反过来:小簇更聚焦)?"
        ),
        "evidence_needed": (
            "分层成绩单里「主归属落在小簇 vs 大概念篮」的实际表现对照(见本件 §② 的 "
            "`contribution` 分桶与可交易收益)。"
        ),
        "status": "待策略线 / 用户过目;在有新证据之前按现默认执行,builder 不许自行改数。",
    },
    {
        "id": "P3-34",
        "title": "工程重解读 / 零审计背书登记册(V2.2 已扩容)",
        "question": (
            "(a) K7 需求 5 `warn_streak_top` 的「簇内」被重解读为「共振邻域」—— "
            "照字面「任一簇头名」在真实数据上会让 86% 涨停票都打警示、标注失去区分度,"
            "生产读法改成「所在全部簇成员并集内并列或独占最高,且邻域至少有一只更低」。"
            "**这个重解读是否偏离 H12/H13 原意?**"
            "(b) 验证 / 失效条件集与聚合门槛(`min_members_hit = ceil(n/2)`)是全新发明、"
            "**零回测或事件研究支持**。验证率 / 证伪率分布是否合理?`falsified` 打得过早过滥?"
            "失效侧要不要按角色加权(龙头破位比弹性破位更重 —— 合理但无证据,刻意未做)?"
            "(c) **落地起跳位置关的机械读数口径**(近端/前区间最低价窗口、平台下沿分位、"
            "抛压衰减的 5v20、60 日高点、3 日累计涨幅、`platform_days` 的两个定义参数)"
            "—— 由 K8 §二 五条定性状态翻译而来,**零回测、零事件研究支持**;"
            "⚠ 裁定 #11 之后这些**只是读数、不再是阈值**(四态与十二个阈值已删),"
            "但「用哪个窗口算这个事实」本身仍是个待过目的口径选择。"
            "(d) **C1/Z1/Y1 三引擎首版阈值里标 `engineering_v1` 的那些**"
            "(回撤深度带 / `platform_days` / 证据份数 / 板块广度分位)—— 同上,"
            "包 JSON 里已带 `provenance: engineering_v1 + calibration: pending`(闸 1 强制)。"
        ),
        "evidence_needed": (
            "本件 §② 的**验证率四态分布**与 `not_evaluated` 计数(后者不进分母),"
            "以及按驱动类型 / 角色分桶的可交易收益;(c)(d) 两项另需本件 §④ 里"
            "**按 骨架 × 引擎 × 版本 分层**的选股时钟成绩单(样本够了才谈得上校准)。"
        ),
        "status": (
            "待策略线 / 用户过目。⛔ 本版不进包:§12.2 插槽边界把「篮子卡冻结体例」列在"
            "「引擎本体,不进包」一侧,要包化必须走「扩插槽边界(用户拍板)→ 扩 schema → 发包」三步。"
            "**校准路径 = 选股时钟攒样本 → 周度四分类建议 → 用户确认 → `C2`/`Z2`/`Y2`;"
            "在有新证据之前 ⛔ 不许自行改数。**"
        ),
    },
    {
        "id": "P3-37",
        "title": "退潮红色刹车「主线跳水」路的灵敏度",
        "question": (
            "该路自 ⑧-G 起从「聋」变「能响」:此前真跳水日(全市场中位 −2.86%、89% 个股下跌)"
            "样本读数只有 −0.18%,而主线板块本身已达 −2.98%,差 2.8pp、四天 0/4 触发。"
            "样本口径修好后,`sector_dive` 这个 V1 传下来的阈值**实际含义变了** —— 现在多灵敏?"
            "连同 K=4、保底配额、per-seed 估计量三个无实盘背书的工程默认一起回看。"
        ),
        "evidence_needed": (
            "≥1 个月实盘 + 经历 2~3 次真实急跌日的触发记录(`retreat_metrics` 逐日读数 + "
            "实际触发日清单),以及 71 只保底配额的抽样噪声量级。"
        ),
        "status": "⛔ 有实盘证据前阈值一字不动,要动走用户拍板。",
    },
    {
        # 🔴 V2.2-④ 新增。移交件里**最该让用户看见的一条** —— K8 的位置关正落在策略线
        # 已判死的域上,而本项目为它保留的唯一裁决方式是前向实盘证伪。
        "id": "P3-49",
        "title": "K8「落地起跳」位置关落在 K3 已判死的域里 —— 前向证伪义务",
        "question": (
            "K8 §二「选择完成下落或调整、确认支撑并**刚刚向上启动**的核心股票」翻成机械"
            "形状后,与 **K3-B2 臂③「升势回撤 + 启动确认」** 同形,而那一臂的判决原文是"
            "「确认信号 = 死猫跳顶点,比直接买更差」;只用第一条判据的形状则撞上"
            "「K3 系统化超跌反弹四臂全灭」。⚠ **这不是说「K8 错了」**:K8 §九 排除项九条里"
            "有六条与雷区地图正面同向,冲突只落在**位置关正向判据**这一处;且本项目选股链"
            "自 V2 起本就与回测信号解耦(输出「值得关注的票」而非「会涨的票」)。"
            "**要回答的问题**:位置关到底有没有用?"
        ),
        "evidence_needed": (
            "🔴 **判据来源写死 = 选股时钟的实盘前向数据**(用户 2026-08-09 裁定 #10"
            "「信实盘别信回测」;⛔ 不为本条立任何回测战役 / 事件研究)。"
            "**结案条件**:选股时钟累计 **≥ 100 个已结案篮子**样本,且「D0 位置关判定"
            "(`ok`/`weak`/`unfit`)× 后续表现 vs 安慰剂基线」三方对照跑出方向性结论。"
            "本件 §④-1 的 `by_position_verdict` 分桶就是这份证据的落点。"
        ),
        "status": (
            "⛔ **结案前不许对外称 K8 位置关「有效」**;**结论无论正负都必须上报**"
            "(⛔ 不许只在结论为正时才报)。雷区对照只作「知情登记」、⛔ 不作否决依据。"
        ),
    },
    {
        # 🔴 V2.2-④ 新增(冷启动边界,如实披露即可,不是缺陷)。
        "id": "P3-51",
        "title": "行情状态层第五维「近期 T1/T2 正确率」在样本攒够前恒缺席",
        "question": (
            "K8 §一 把「近期 T1、T2 正确率」列为三态判定的五个依据之一,而它的数据源 = "
            "**选股时钟已结案样本**,选股时钟到 V2.2 批 3 才上线 → 那之前(以及上线后最初"
            "十来个交易日)该维恒 `available=false`。**要过目的是两件事**:① 目前的退化源"
            "(`basket_verification` 的 EOD 定论行,`source='basket_verification_fallback'`)"
            "作近似合不合适?② 样本攒够之后切换到选股时钟为源,这个切换要不要"
            "**重算历史**、还是只对新数据生效?"
        ),
        "evidence_needed": (
            "本件 §④-1 的选股时钟样本量(按交易日计)——够 10 个交易日之后,"
            "同一窗口下「退化源算出的正确率」与「选股时钟算出的正确率」对拍一次即知差多少。"
        ),
        "status": (
            "**已知冷启动边界,不是缺陷**:缺维如实标 `unavailable_reason="
            "'clock_samples_insufficient'`,⛔ 不当 0 参与比较、⛔ 不因为少一维就不落行"
            "(四维照判,少一维是「证据薄」不是「判不了」)。"
            "⚠ **切源那一步 V2.2-④ 刻意没做**:换判定输入是行情状态层(②)的事,"
            "本块只把数据源建起来 —— 何时切、要不要重算历史,等这条过目。"
        ),
    },
)


# ══════════════════════════════════════════════════════════════════════════
# 渲染(五节)
# ══════════════════════════════════════════════════════════════════════════

_LOW_CONFIDENCE_NOTE = "样本不足,不给结论"

_PREF_COLS = (("dimension", "维度"), ("value", "取值"), ("share", "占比"),
              ("sampleN", "样本量"), ("window", "窗口"), ("confidence", "置信度"))
_CAP_COLS = (("dimension", "维度"), ("value", "取值"), ("sampleN", "样本量"),
             ("winRate", "胜率"), ("profitFactor", "盈亏比"), ("vsPeerDelta", "vs 同篮未选"),
             ("window", "窗口"), ("confidence", "置信度"))


def _num(x: Any, nd: int = 3) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"          # ⛔ 「算不出」不用 0 冒充
    return f"{float(x):.{nd}f}"


def _ratio(x: Any) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return f"{float(x):.0%}"


def _signed_pct(x: Any) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return f"{float(x) * 100:+.2f}%"


def _window(row: Mapping[str, Any]) -> str:
    lo, hi = row.get("windowStart") or "", row.get("windowEnd") or ""
    return f"{lo}→{hi}" if (lo or hi) else "—"


def _conf(row: Mapping[str, Any]) -> str:
    c = str(row.get("confidence") or "")
    # 🔴 `low` **必须**当场写出那句话 —— 低置信度的数字旁边不写这一句,它就会被当成结论读。
    return f"`{c}` —— {_LOW_CONFIDENCE_NOTE}" if c == "low" else (f"`{c}`" if c else "—")


def _table(cols: Sequence[Tuple[str, str]], rows: Sequence[Mapping[str, Any]],
           fmt: Mapping[str, Any]) -> List[str]:
    out = ["| " + " | ".join(label for _, label in cols) + " |",
           "|" + "---|" * len(cols)]
    for r in rows:
        cells = []
        for key, _label in cols:
            f = fmt.get(key)
            cells.append(f(r) if callable(f) else str(r.get(key, "") or "—"))
        out.append("| " + " | ".join(cells) + " |")
    return out


def _sample_counts(
    calibration: Optional[Mapping[str, Any]],
    preference: Sequence[Mapping[str, Any]],
    capability: Sequence[Mapping[str, Any]],
) -> Dict[str, int]:
    cal = calibration or {}
    strata = cal.get("strata") or []
    return {
        "tradingDays": int(cal.get("nTradingDays") or 0),
        "baskets": int(cal.get("nBaskets") or 0),
        "strata": len(strata) if isinstance(strata, list) else 0,
        "preferenceRows": len(preference),
        "capabilityRows": len(capability),
    }


def render_handoff(
    *,
    date_from: str,
    date_to: str,
    calibration: Optional[Mapping[str, Any]] = None,
    calibration_status: str = CAL_NOT_GENERATED,
    calibration_markdown: Optional[str] = None,
    preference: Sequence[Mapping[str, Any]] = (),
    capability: Sequence[Mapping[str, Any]] = (),
    profile_as_of: str = "",
    generated_at: str = "",
) -> str:
    """移交件 markdown 五节。**纯函数**:所有输入都已经由调用方读好
    (`build_handoff` 是那个调用方),本函数零 I/O、零 DB、⛔ 零重算。"""
    from neckline.eval.calibration import DISCLAIMER      # 免赔声明复用,⛔ 不另写一份

    n = _sample_counts(calibration, preference, capability)
    out: List[str] = []
    out.append(f"# 校准移交件 · {date_from} → {date_to}")
    out.append("")
    out.append(f"> 生成于 {generated_at or '(未记录)'}。**这份文件是给策略台的输入,"
               f"不是结论**:系统攒证据、用户拍板改包,改包唯一通道仍是"
               f"「攒够样本 → 带材料去策略台 → 新 K 包 → 用户过门 → 四道闸激活」。"
               f"⛔ 系统不做任何自动反馈回写选股。")
    out.append("")

    # —— ① 窗口与样本量 ————————————————————————————————————————————————
    out.append("## ① 窗口与样本量")
    out.append("")
    out.append(f"- 校准窗口:**{date_from} → {date_to}**")
    out.append(f"- 样本:**{n['tradingDays']} 个交易日 / {n['baskets']} 个篮子 / "
               f"{n['strata']} 个分层**(分层 = `pack_version` × `verification_ruleset_version`)")
    out.append(f"- 画像期:**{profile_as_of or '(未取得)'}** —— "
               f"偏好 {n['preferenceRows']} 行 / 能力 {n['capabilityRows']} 行")
    if calibration_status == CAL_CORRUPT:
        out.append("- 🔴 **本窗口的校准产物读不出**(文件在、JSON 解不出)——"
                   "**它不会自己好**,需人工排查;⛔ 别当成「还没生成」等下去。")
    elif calibration_status != CAL_OK:
        out.append("- ⚠ **本窗口尚无周度校准产物**(周度 unit 还没跑到这个窗口)——"
                   "下面 §② 为空是**如实**,不是缺陷。")
    strata = (calibration or {}).get("strata") or []
    if isinstance(strata, list) and strata:
        out.append("")
        out.append("**各分层样本量**(即包成绩单的分层,⛔ 本件不另建第二份聚合):")
        out.append("")
        out.append("| 包版本 | 条件集版本 | 交易日 | 篮子 |")
        out.append("|---|---|--:|--:|")
        for s in strata:
            if not isinstance(s, Mapping):
                continue
            out.append(f"| `{s.get('packVersion') or '?'}` | `{s.get('rulesetVersion') or '?'}` | "
                       f"{s.get('nDays', 0)} | {s.get('nBaskets', 0)} |")
    out.append("")

    # —— ② 校准报告全文 ————————————————————————————————————————————————
    out.append("## ② 周度校准报告(原文)")
    out.append("")
    if calibration_markdown:
        out.append("> 以下为 `calibration_%s_%s.md` **原文**,⛔ 未重排版 —— "
                   "移交件不改写审计件,历史可比性优先。" % (date_from, date_to))
        out.append("")
        out.append(calibration_markdown.rstrip())
    elif calibration_status == CAL_CORRUPT:
        out.append("🔴 **产物读不出**(见 §①)。⛔ 本件不在线重算一份 ——"
                   "那会拿今天的数据编造那个窗口的结论。")
    else:
        out.append("⚠ **本窗口尚无校准产物**。这一节为空是如实的:周度校准由离线的周度"
                   "作业算好落盘,在线路径只读产物、**⛔ 永不在线补算**(§七 P0-23)。")
    out.append("")

    # —— ③ 画像两张表 ————————————————————————————————————————————————
    out.append("## ③ 用户画像(每行必带样本量 / 窗口 / 置信度)")
    out.append("")
    out.append("> ⚠ **两张账刻意分开,⛔ 不合并**:偏好答「喜欢什么」、能力答「什么真有效」"
               "—— 合成一张就等于用喜好给能力背书。")
    out.append("")
    out.append(f"### ③-1 偏好画像(期:{profile_as_of or '—'})")
    out.append("")
    if preference:
        out.extend(_table(_PREF_COLS, preference, {
            "share": lambda r: _ratio(r.get("share")),
            "window": _window, "confidence": _conf,
        }))
    else:
        out.append("(本期无偏好画像行 —— 要么周度批算还没跑到这一期,要么这一期确实没有"
                   "够样本的维度;⛔ 两者别读成同一句话,以端点的 `available` 为准。)")
    out.append("")
    out.append(f"### ③-2 能力画像(期:{profile_as_of or '—'})")
    out.append("")
    if capability:
        out.extend(_table(_CAP_COLS, capability, {
            "winRate": lambda r: _ratio(r.get("winRate")),
            "profitFactor": lambda r: _num(r.get("profitFactor"), 2),
            "vsPeerDelta": lambda r: _signed_pct(r.get("vsPeerDelta")),
            "window": _window, "confidence": _conf,
        }))
        out.append("")
        out.append("*`vs 同篮未选` 为 `—` = **配对样本不足**,⛔ 不是「没有差异」。*")
    else:
        out.append("(本期无能力画像行,同上。)")
    out.append("")

    # —— ④ 修改建议四分类(V2.2-④-D)————————————————————————————————————
    out.append("## ④ 修改建议四分类(K8 §十七)")
    out.append("")
    out.append("> 🔴 **这是建议,不是动作**:K8 §十七 原文「复盘板块输出修改建议。"
               "**用户确认后**,新规则从下一版本生效」。系统对选股包**零自动回写** —— "
               "发新引擎版本必须走「你确认 → 新包 → 四道闸激活」。")
    out.append("")
    iteration = (calibration or {}).get("iteration") if isinstance(calibration, Mapping) else None
    if iteration:
        from neckline.eval.calibration import render_iteration_section

        # ⛔ 不在这里另写一份表格:同一份数据两处排版,迟早讲两种话。
        body = render_iteration_section(iteration)
        # 落盘产物里那一节自带 `## §4 …` 标题,这里已经有 `## ④` 了 —— 去掉重复的
        # 顶层标题行,子标题(`### §4-x`)原样保留。
        out.extend(line for line in body if not line.startswith("## §4 "))
    elif calibration_status == CAL_CORRUPT:
        out.append("🔴 **产物读不出**(见 §①),四分类无从谈起。⛔ 本件不在线重算。")
    else:
        out.append("⚠ **本窗口尚无校准产物**,四分类段为空是如实的"
                   "(周度作业跑到这个窗口即有)。")
    out.append("")

    # —— ⑤ 观察项清单 ————————————————————————————————————————————————
    out.append("## ⑤ 观察项清单(等证据的策略问题)")
    out.append("")
    out.append("> 这些是**已经登记在 `PROJECT_PLAN.md` §七 Backlog 里**的待过目项 —— "
               "每一条都有稳定 ID,⛔ 别在这份文件里新发明观察项。")
    out.append("")
    for ob in HANDOFF_OBSERVATIONS:
        out.append(f"### [{ob['id']}] {ob['title']}")
        out.append("")
        out.append(f"- **要回答的问题**:{ob['question']}")
        out.append(f"- **需要什么证据**:{ob['evidence_needed']}")
        out.append(f"- **当前状态**:{ob['status']}")
        out.append("")

    # —— ⑥ disclaimer ————————————————————————————————————————————————
    out.append("## ⑥ 免责与口径")
    out.append("")
    out.append(f"> {DISCLAIMER}")
    out.append("")
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════════
# 装配(端点直接用这个;⛔ 零写库、零现算)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Handoff:
    available: bool = False
    unavailable_reason: Optional[str] = None
    window_from: str = ""
    window_to: str = ""
    generated_at: str = ""
    sample_n: Dict[str, int] = field(default_factory=dict)
    markdown: str = ""


def build_handoff(
    date_from: str = "", date_to: str = "", *,
    out_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    profile_as_of: str = "",
    now: Optional[date] = None,
) -> Handoff:
    """装配一份移交件。缺省窗口 = **最近一期已落盘的校准产物**。

    **`available=False` 的两种情形,文案分开**:① 一期产物都没有(⑥ 还没跑过)——
    会自愈;② 指定窗口的产物**读不出** —— **不会**自愈,要人排查。

    ⛔ 零写库、⛔ 零在线补算:窗口给了却没产物 → 照样出一份**只有画像与观察项**的
    移交件(§② 如实写"本窗口尚无产物")—— 那仍然是能交给策略台的东西,比什么都不给强。
    """
    from neckline.profile.store import latest_as_of, load_capability, load_preference

    lo, hi = (date_from or "").strip(), (date_to or "").strip()
    if not (lo and hi):
        periods = list_calibration_artifacts(out_dir)
        if not periods:
            return Handoff(
                available=False,
                unavailable_reason=(
                    "尚无任何周度校准产物 —— 周度作业还没跑过第一次(它按周落盘,"
                    "⛔ 在线路径不补算)。等下一次周度作业跑完即有。"
                ),
            )
        lo, hi = periods[0].date_from, periods[0].date_to

    payload, status = load_calibration_with_status(lo, hi, out_dir)
    if status == CAL_CORRUPT:
        return Handoff(
            available=False, window_from=lo, window_to=hi,
            unavailable_reason=(
                f"本窗口({lo}→{hi})的校准产物**读不出**(文件在、JSON 解析失败)。"
                f"它是落盘产物、不会自己好 —— 需人工排查,⛔ 别当成「还没生成」等下去。"
            ),
        )

    as_of = (profile_as_of or "").strip() or (latest_as_of("preference", db_path=db_path) or "")
    cap_as_of = (profile_as_of or "").strip() or (latest_as_of("capability", db_path=db_path) or "")
    pref_rows = load_preference(as_of, db_path) if as_of else []
    cap_rows = load_capability(cap_as_of, db_path) if cap_as_of else []
    generated = (now or date.today()).strftime("%Y%m%d")
    md = render_handoff(
        date_from=lo, date_to=hi, calibration=payload, calibration_status=status,
        calibration_markdown=load_calibration_markdown(lo, hi, out_dir),
        preference=pref_rows, capability=cap_rows,
        profile_as_of=as_of or cap_as_of, generated_at=generated,
    )
    return Handoff(
        available=True, window_from=lo, window_to=hi, generated_at=generated,
        sample_n=_sample_counts(payload, pref_rows, cap_rows), markdown=md,
    )


__all__ = [
    "CAL_CORRUPT", "CAL_NOT_GENERATED", "CAL_OK", "HANDOFF_OBSERVATIONS",
    "Handoff", "Period",
    "build_handoff", "calibration_dir", "list_calibration_artifacts",
    "load_calibration", "load_calibration_markdown", "load_calibration_with_status",
    "render_handoff",
]
