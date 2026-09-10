"""Persist the task behind direct handler unit tests in an isolated schema."""
from neckline.k10.schema import initialize_schema
from neckline.k10 import store

def persist_context(context):
    initialize_schema(context.db_path)
    store.enqueue_task(task_id=context.task.task_id,kind='morning_review',idempotency_key=context.task.task_id,
        input_version=context.input_version,input_cutoff_at=context.input_cutoff_at,payload=context.task.payload,
        budget={},created_at=context.input_cutoff_at,db_path=context.db_path)
    return context
