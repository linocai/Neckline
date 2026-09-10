"""Actual, user-authorized APNs delivery of this report only, with durable receipts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import socket
import time
from dotenv import load_dotenv

assert socket.gethostname() == 'ser657204219523'
load_dotenv('/opt/neckline/.env', override=True)
load_dotenv('/etc/neckline/k10.env', override=True)

from neckline import notify_kinds
from neckline.api.stores import list_device_tokens, delete_device
from neckline.settings_store import push_kind_enabled
from neckline.push.apns import apns_readiness, send_push
from neckline.k10 import store
from neckline.k10.notification_runtime import load_notification_delivery_config
from neckline.k10.notifications import DeliveryResult, on_task_terminal, get_notification, dispatch_task_notifications
from neckline.k10.v2_store import read_report


def deliver():
    db=Path('/opt/neckline/data/neckline.db')
    task_id='task_c9c0feab5c83a08e8a17138ebfb0041a'
    scan_id='scan_d39fbd5c631ea9a7df5f569dbd432b4d'
    task=store.get_task(task_id=task_id,db_path=db)
    assert task.status=='completed'
    report=read_report(db_path=db,report_id='report_'+scan_id)
    assert report and report['availableAt'] and report['status'] in {'completed','partial'}
    assert apns_readiness().ready
    assert list_device_tokens(db_path=db), 'No registered push devices'
    assert push_kind_enabled(notify_kinds.KIND_K10_EVENING,db_path=db)
    notification=on_task_terminal(task_id=task_id,db_path=db,created_at=datetime.now(timezone.utc))
    assert notification.kind==notify_kinds.KIND_K10_EVENING and notification.terminal_status=='completed'
    config=load_notification_delivery_config()
    receipts=[]
    def sender(*,token,title,body,kind,deep_link,evidence_disclosure,collapse_id):
        assert collapse_id==notification.notification_id and kind==notify_kinds.KIND_K10_EVENING
        result=send_push(token,title,body,category=notify_kinds.category_of(kind),thread_id='neckline-k10',
            custom={'kind':kind,**deep_link},collapse_id=collapse_id)
        receipts.append({'ok':result.ok,'status':result.status,'reason':result.reason})
        return DeliveryResult(ok=result.ok,reason=result.reason,
            configuration_unavailable=result.reason in {'apns_credentials_missing','apns_key_unreadable','apns_key_invalid'},
            permanent_invalid=result.reason in {'BadDeviceToken','Unregistered','DeviceTokenNotForTopic'})
    while True:
        dispatch_task_notifications(db_path=db,list_device_tokens=lambda:list_device_tokens(db_path=db),
            delete_device=lambda token:delete_device(token,db_path=db),sender=sender,
            worker_id='trial-b68-report-push',now=datetime.now(timezone.utc),clock=lambda:datetime.now(timezone.utc),
            retry_policy=config.retry_policy,notification_id=notification.notification_id,limit=1)
        state=get_notification(notification_id=notification.notification_id,db_path=db)
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
            count=conn.execute('SELECT COUNT(*) FROM k10_notification_deliveries WHERE notification_id=?',(notification.notification_id,)).fetchone()[0]
            detail=conn.execute('SELECT next_attempt_at,blocked_reason,last_error FROM k10_task_notifications WHERE notification_id=?',(notification.notification_id,)).fetchone()
        value={'taskId':task_id,'reportId':report['reportId'],'notificationId':notification.notification_id,
            'notificationState':state.status,'deliveredDeviceCount':count,'apnsResponses':receipts,
            'availableAt':report['availableAt'],'reportStatus':report['status'],'checkedAt':datetime.now(timezone.utc).isoformat()}
        path=db.parent/'backups/v3.2.0-b68-predeploy/report-push-receipt.json'
        path.write_text(json.dumps(value,indent=2)+'\n');path.chmod(0o600)
        if state.status=='sent':
            assert count>0, 'Logical sent state without an APNs-accepted delivery is insufficient'
            print(json.dumps(value),flush=True)
            return
        assert state.status in {'queued','sending'} and not detail[1], 'Push needs configuration repair'
        due=datetime.fromisoformat(detail[0]) if detail[0] else datetime.now(timezone.utc)
        time.sleep(min(60,max(1,(due-datetime.now(timezone.utc)).total_seconds())))


if __name__=='__main__':
    deliver()
