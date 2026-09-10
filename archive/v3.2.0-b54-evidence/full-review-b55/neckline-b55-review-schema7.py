from pathlib import Path
import subprocess,types,tempfile,sqlite3,json,stat
from neckline.k10.schema import schema_version
from neckline.k10.migration import migrate_to_v3,restore_backup,file_sha256
root=Path(tempfile.mkdtemp(prefix='neckline-b55-schema7-review-'));db=root/'target.sqlite';backup=root/'backup.sqlite'
source=subprocess.check_output(['git','show','v3.1.0-b53:Backend/neckline/k10/schema.py']).decode()
old=types.ModuleType('neckline.k10.schema_b53_review');old.__package__='neckline.k10'
exec(compile(source,'git:v3.1.0-b53/schema.py','exec'),old.__dict__)
assert old.initialize_schema(db)==7
with sqlite3.connect(db) as conn:
 conn.execute("UPDATE k10_run_controls SET reason_code='review-paused-sentinel',changed_by='review' WHERE control_key='system'")
 conn.execute("INSERT INTO k10_run_config_revisions(config_id,revision,content_sha256,payload_json,created_at) VALUES ('review-frozen',1,'immutable-review','{\"historical\":true}','2026-09-08T13:00:00+00:00')")
def snapshot(path):
 with sqlite3.connect(path) as conn:
  return {name:(tuple(r[1] for r in conn.execute('PRAGMA table_info("'+name+'")')),conn.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall()) for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
before=snapshot(db);receipt=migrate_to_v3(target=db,confirmed_target=db,backup=backup,writers_stopped=True)
assert schema_version(db)==8
now=snapshot(db)
assert all(now[k]==v for k,v in before.items() if k!='k10_schema_migrations')
assert file_sha256(backup)==receipt.backup_sha256
restore_backup(target=db,confirmed_target=db,backup=backup,expected_sha256=receipt.backup_sha256,writers_stopped=True)
assert snapshot(db)==before and old.schema_version(db)==7
print(json.dumps({'oldTablesVerified':len(before)-1,'schema7to8':'passed','verifiedRollbackTo7':'passed','temporaryRoot':str(root)}))
