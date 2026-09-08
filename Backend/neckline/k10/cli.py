"""K10 定时入口：只入队或运行 worker，不加载 .env、不猜配置或日历缺口。"""
from __future__ import annotations

import argparse, json, os, signal, threading
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from neckline.calendar.trading_calendar import official_is_trading_day
from . import store
from .config import validate_execution_config, validate_run_config
from .evaluation_schedule import maintain_evaluations
from .evaluation_runtime import evaluation_handler, market_day_fact_handler
from .notification_runtime import create_notification_maintenance
from .notifications import require_notifications_schema
from .schema import require_schema, read_connection
from .pipeline import production_handlers
from .windows import evening_cutoff, morning_cutoff
from .worker import run_once, run_worker


def _id(prefix: str, *parts: str) -> str: return prefix+"_"+sha256("\x1f".join(parts).encode()).hexdigest()[:32]
def _day(text: str) -> date: return date.fromisoformat(text)


def configure(*, db_path: Path, config_id: str, file_path: Path, now: datetime) -> tuple[str, int]:
    """Append one validated immutable K10 pack; this never changes existing task revisions."""
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("K10 配置文件不可读或不是 JSON") from exc
    state = validate_run_config(payload, scope="discovery")
    if not state.ready:
        detail = "; ".join((*state.missing, *state.errors)) or "未知配置错误"
        raise ValueError(f"K10 配置未就绪：{detail}")
    routes = payload.get("modelRoutes")
    if not isinstance(routes, dict) or set(routes) != {"discovery", "analysis", "morning"}:
        raise ValueError("K10-v1.4 配置必须明确 discovery、analysis、morning 三个 modelRoutes")
    revision = store.append_run_config(config_id=config_id, payload=payload, created_at=now.isoformat(), db_path=db_path)
    return config_id, revision


def configure_execution(*, db_path: Path, config_id: str, file_path: Path, now: datetime) -> tuple[str, int]:
    """Append one explicit operational profile without rewriting K10 strategy configuration."""
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("执行配置文件不可读或不是 JSON") from exc
    state = validate_execution_config(payload)
    if not state.ready:
        detail = "; ".join((*state.missing, *state.errors)) or "未知配置错误"
        raise ValueError(f"执行配置未就绪：{detail}")
    revision = store.append_execution_config(config_id=config_id, payload=payload, created_at=now.isoformat(), db_path=db_path)
    return config_id, revision


def _approved_v3_execution(*, db_path: Path, config_id: str, revision: int) -> bool:
    """Check the only execution profile an entry point may bind live work to."""
    profile = store.read_execution_config(config_id=config_id, revision=revision, db_path=db_path)
    payload = profile.get("payload") if isinstance(profile, dict) else None
    return (isinstance(payload, dict) and payload.get("executionVersion") == "k10-execution-v3"
            and validate_execution_config(payload).ready)

def enqueue_scan(*, db_path: Path, kind: str, trading_day: date, config_id: str, config_revision: int, now: datetime,
                 bootstrap_cutoff: str | None = None, execution_config_id: str | None = None,
                 execution_config_revision: int | None = None) -> str:
    if kind not in {"evening","morning"}: raise ValueError("kind 必须是 evening 或 morning")
    control = store.run_control_status(db_path=db_path)
    if control.get("state") != "open":
        raise RuntimeError("K10 运行已暂停，拒绝入队")
    calendar_state = official_is_trading_day(trading_day, db_path=db_path)
    if calendar_state is None:
        raise RuntimeError("交易日历缺覆盖，拒绝猜测交易日")
    if calendar_state is False:
        raise RuntimeError("该日为非交易日，不能入队")
    config=store.read_run_config(config_id=config_id,revision=config_revision,db_path=db_path)
    if config is None: raise RuntimeError("指定 K10 配置修订不存在")
    policy=(config["payload"].get("taskPolicies") or {}).get("discovery")
    if not isinstance(policy,dict) or not isinstance(policy.get("maxAttempts"),int) or not isinstance(policy.get("maxSourceRequests"),int):
        raise RuntimeError("discovery 重试和来源分页上限未配置")
    cutoff=evening_cutoff(trading_day) if kind=="evening" else morning_cutoff(trading_day)
    if bootstrap_cutoff is not None:
        try:
            parsed = datetime.fromisoformat(bootstrap_cutoff)
        except ValueError as exc:
            raise ValueError("bootstrap cutoff 必须是带时区 ISO 时间") from exc
        if parsed.tzinfo is None or parsed >= cutoff:
            raise ValueError("bootstrap cutoff 必须早于固定扫描截止")
    if kind != "evening" and bootstrap_cutoff is not None:
        raise ValueError("bootstrap cutoff 仅适用于 evening")
    if execution_config_id is None or execution_config_revision is None:
        raise RuntimeError("正式扫描入队必须显式绑定已批准的 V3 执行配置")
    if not _approved_v3_execution(db_path=db_path, config_id=execution_config_id,
                                  revision=execution_config_revision):
        raise RuntimeError("指定 V3 执行配置修订不存在或未就绪")
    task_id=_id("task",kind,cutoff.isoformat(),config_id,str(config_revision),bootstrap_cutoff or "")
    task_kind=f"{kind}_scan"
    store.enqueue_task(task_id=task_id,kind=task_kind,idempotency_key=f"{task_kind}:{cutoff.isoformat()}:{config_id}:{config_revision}:{bootstrap_cutoff or ''}",
                       input_version=str(config["contentSha256"]),input_cutoff_at=cutoff.isoformat(),
                       payload={"windowKind":kind,"tradingDay":trading_day.isoformat(),"configId":config_id,"configRevision":config_revision,
                                **({"sourceBootstrapCutoff": bootstrap_cutoff} if bootstrap_cutoff is not None else {})},
                       budget={},created_at=now.isoformat(),db_path=db_path)
    store.bind_task_execution(task_id=task_id, execution_config_id=execution_config_id,
                              execution_config_revision=execution_config_revision, binding_kind="scheduled",
                              bound_at=now.isoformat(), db_path=db_path)
    return task_id


def frozen_scan_input_sha256(*, scan_id: str, db_path: Path) -> str:
    scan = store.get_scan(scan_id=scan_id, db_path=db_path)
    if scan is None:
        raise RuntimeError("指定扫描不存在")
    coverage = scan.get("coverage")
    refs = coverage.get("inputDocumentRefs") if isinstance(coverage, dict) else None
    if not isinstance(refs, list) or not refs:
        raise RuntimeError("扫描没有可恢复的冻结资料引用")
    return sha256(json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def recover_scan(
    *, db_path: Path, scan_id: str, execution_config_id: str, execution_config_revision: int,
    confirmed_input_sha256: str, now: datetime,
) -> str:
    """Create the one controlled recovery task for a failed frozen scan, without recollection."""
    control = store.run_control_status(db_path=db_path)
    if control.get("state") != "open":
        raise RuntimeError("K10 运行已暂停，拒绝恢复")
    scan = store.get_scan(scan_id=scan_id, db_path=db_path)
    if scan is None or (scan.get("status") not in {"failed", "not_configured", "running"} and not store.is_failed_title_scan(scan)):
        raise RuntimeError("只有当前 failed 或 not_configured 的扫描可建立受控恢复")
    if not isinstance(scan.get("configId"), str) or not isinstance(scan.get("configRevision"), int):
        raise RuntimeError("冻结扫描缺少策略配置绑定")
    coverage = scan.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("inputSnapshotFrozen") is not True:
        raise RuntimeError("扫描未冻结输入快照，拒绝恢复")
    actual_input_sha256 = frozen_scan_input_sha256(scan_id=scan_id, db_path=db_path)
    if confirmed_input_sha256 != actual_input_sha256:
        raise RuntimeError("冻结资料哈希确认不匹配，拒绝恢复")
    if store.read_run_config(config_id=scan["configId"], revision=scan["configRevision"], db_path=db_path) is None:
        raise RuntimeError("冻结策略配置修订不存在")
    if not _approved_v3_execution(db_path=db_path, config_id=execution_config_id,
                                  revision=execution_config_revision):
        raise RuntimeError("指定 V3 执行配置修订不存在或未就绪")
    if any(batch.get("scanId") == scan_id for batch in store.list_publication_batches(db_path=db_path)):
        raise RuntimeError("已有正式发布批次的扫描不能建立恢复任务")
    # B39 keeps title audit, actual article admissions, model checkpoints and
    # research snapshots on the original task. Requeue that exact task after a
    # confirmed frozen-input authorization; a replacement task would reset the
    # 80/40 boundary and could repeat already successful paid work.
    progress = store.execution_progress_for_scan(scan_id=scan_id, db_path=db_path)
    if isinstance(progress, dict) and isinstance(progress.get("taskId"), str):
        try:
            task = store.authorize_discovery_recovery(
                scan_id=scan_id, execution_config_id=execution_config_id,
                execution_config_revision=execution_config_revision,
                confirmed_input_sha256=actual_input_sha256, authorized_at=now.isoformat(), db_path=db_path,
            )
        except store.K10Conflict as exc:
            raise RuntimeError(str(exc)) from exc
        return task.task_id
    task_id = _id("task", "recovery", scan_id, execution_config_id, str(execution_config_revision), actual_input_sha256)
    task_kind = f"{scan['windowKind']}_scan"
    policy = store.read_run_config(config_id=scan["configId"], revision=scan["configRevision"], db_path=db_path)["payload"]["taskPolicies"]["discovery"]
    store.enqueue_task(
        task_id=task_id, kind=task_kind, idempotency_key=f"recovery:{scan_id}:{execution_config_id}:{execution_config_revision}:{actual_input_sha256}",
        input_version=str(store.read_run_config(config_id=scan["configId"], revision=scan["configRevision"], db_path=db_path)["contentSha256"]),
        input_cutoff_at=str(scan["cutoffAt"]),
        payload={"windowKind": scan["windowKind"], "configId": scan["configId"], "configRevision": scan["configRevision"],
                 "resumeScanId": scan_id, "frozenInputSha256": actual_input_sha256, "sourceCollection": "forbidden"},
        budget={},
        created_at=now.isoformat(), db_path=db_path,
    )
    store.bind_task_execution(task_id=task_id, execution_config_id=execution_config_id,
                              execution_config_revision=execution_config_revision, binding_kind="recovery",
                              bound_at=now.isoformat(), db_path=db_path)
    store.bind_scan_execution(scan_id=scan_id, task_id=task_id, execution_config_id=execution_config_id,
                              execution_config_revision=execution_config_revision, binding_kind="recovery",
                              bound_at=now.isoformat(), db_path=db_path)
    return task_id


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    config=sub.add_parser("configure"); config.add_argument("--db",required=True,type=Path); config.add_argument("--config-id",required=True); config.add_argument("--file",required=True,type=Path)
    execution_config=sub.add_parser("configure-execution"); execution_config.add_argument("--db",required=True,type=Path); execution_config.add_argument("--config-id",required=True); execution_config.add_argument("--file",required=True,type=Path)
    enqueue=sub.add_parser("enqueue"); enqueue.add_argument("--db",required=True,type=Path); enqueue.add_argument("--kind",choices=("evening","morning"),required=True); enqueue.add_argument("--trading-day",required=True); enqueue.add_argument("--config-id",required=True); enqueue.add_argument("--config-revision",required=True,type=int); enqueue.add_argument("--bootstrap-cutoff"); enqueue.add_argument("--execution-config-id"); enqueue.add_argument("--execution-config-revision",type=int)
    recover=sub.add_parser("recover-scan"); recover.add_argument("--db",required=True,type=Path); recover.add_argument("--scan-id",required=True); recover.add_argument("--execution-config-id",required=True); recover.add_argument("--execution-config-revision",required=True,type=int); recover.add_argument("--confirm-frozen-input-sha256",required=True)
    worker=sub.add_parser("worker"); worker.add_argument("--db",required=True,type=Path); worker.add_argument("--parquet-dir",required=True,type=Path); worker.add_argument("--worker-id",required=True); worker.add_argument("--tushare-token-env",required=True); worker.add_argument("--once",action="store_true")
    args=parser.parse_args(argv)
    if args.command=="configure":
        config_id, revision = configure(db_path=args.db, config_id=args.config_id, file_path=args.file, now=datetime.now().astimezone())
        print(json.dumps({"configId": config_id, "revision": revision}, ensure_ascii=False))
        return 0
    if args.command=="configure-execution":
        config_id, revision = configure_execution(db_path=args.db, config_id=args.config_id, file_path=args.file, now=datetime.now().astimezone())
        print(json.dumps({"configId": config_id, "revision": revision}, ensure_ascii=False))
        return 0
    if args.command=="enqueue":
        trading_day = _day(args.trading_day)
        calendar_state = official_is_trading_day(trading_day, db_path=args.db)
        if calendar_state is None:
            raise RuntimeError("交易日历缺覆盖，拒绝猜测交易日")
        if calendar_state is False:
            print(json.dumps({"status": "not_trading_day", "tradingDay": trading_day.isoformat()}, ensure_ascii=False))
            return 0
        if args.execution_config_id is None or args.execution_config_revision is None:
            raise RuntimeError("正式扫描入队必须显式绑定执行配置")
        print(enqueue_scan(db_path=args.db,kind=args.kind,trading_day=trading_day,config_id=args.config_id,config_revision=args.config_revision,now=datetime.now(evening_cutoff(trading_day).tzinfo),bootstrap_cutoff=args.bootstrap_cutoff,execution_config_id=args.execution_config_id,execution_config_revision=args.execution_config_revision))
        return 0
    if args.command=="recover-scan":
        print(recover_scan(db_path=args.db, scan_id=args.scan_id, execution_config_id=args.execution_config_id,
                           execution_config_revision=args.execution_config_revision,
                           confirmed_input_sha256=args.confirm_frozen_input_sha256, now=datetime.now().astimezone()))
        return 0
    token=os.environ.get(args.tushare_token_env)
    # Worker startup is a read-only gate.  The release migration, never this
    # command, owns creation/upgrades of either schema.
    with read_connection(args.db) as connection:
        require_schema(connection)
    if store.run_control_status(db_path=args.db).get("state") != "open":
        print(json.dumps({"status": "paused"}, ensure_ascii=False))
        return 0
    require_notifications_schema(args.db)
    handlers = production_handlers(tushare_token=token, parquet_dir=args.parquet_dir)
    # These two workers only persist/read frozen market facts and fixed company
    # windows.  They are deliberately independent from discovery/model routing.
    handlers.update({"collect_market_day_fact": market_day_fact_handler,
                     "evaluate_company_window": evaluation_handler})
    notification_maintenance = create_notification_maintenance(db_path=args.db, worker_id=args.worker_id)

    def maintenance() -> None:
        maintain_evaluations(db_path=args.db, now=datetime.now().astimezone())
        notification_maintenance()
    if args.once:
        run_once(db_path=args.db,worker_id=args.worker_id,lease_for=timedelta(minutes=5),handlers=handlers)
        maintenance()
        return 0
    stopped=threading.Event(); signal.signal(signal.SIGTERM,lambda *_:stopped.set()); signal.signal(signal.SIGINT,lambda *_:stopped.set())
    run_worker(db_path=args.db,worker_id=args.worker_id,lease_for=timedelta(minutes=5),idle_seconds=2,handlers=handlers,stop=stopped,maintenance=maintenance); return 0

if __name__=="__main__": raise SystemExit(main())
