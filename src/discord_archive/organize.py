import asyncio
from dataclasses import asdict
import json
from .models import PublishReceipt

# The application has one process. A separate OS lock prevents a second bot instance.
_locks = {}


async def process_job(job_id: str, store, gateway) -> str:
    key = (id(store), job_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        job = store.job(job_id)
        if not job or job['state'] not in {'ready','publishing','published','verified','deleting'}:
            return job['state'] if job else 'missing'
        item = store.get_item(job_id)
        if item.channel_id != gateway.inbox_id or job['destination'] not in gateway.allowed_channels:
            store.update_job(job_id, state='review_pending', error='Invalid source or destination')
            return 'review_pending'
        try:
            if job['state'] == 'ready':
                # Definitive local failures are recoverable without treating them as sends.
                await gateway.preflight(item, job['destination'])
                store.update_job(job_id, state='publishing')
                receipt = await gateway.publish(item, job['destination'], job_id)
                store.update_job(job_id, state='published', receipt=json.dumps(asdict(receipt)))
            elif job['state'] == 'publishing':
                receipt = await gateway.reconcile(job_id, job['destination'])
                if receipt is None:
                    raise ValueError('게시 결과 불확실: 목적지 확인 후 /재시도 필요')
                store.update_job(job_id, state='published', receipt=json.dumps(asdict(receipt)))
            job = store.job(job_id)
            receipt = PublishReceipt(**json.loads(job['receipt']))
            # Re-verify even after a crash in deleting state, before ever deleting the source.
            if not await gateway.verify(receipt, item):
                raise ValueError('보관 본문/파일 검증 실패')
            item.destination_id = job['destination']
            item.archive_url = gateway.archive_url(job['destination'], receipt.message_ids[0])
            store.upsert_item(item)
            store.update_job(job_id, state='verified')
            if gateway.delete_inbox:
                store.update_job(job_id, state='deleting')
                await gateway.delete_source(item.source_message_id)
            store.update_job(job_id, state='done', error='')
            return 'done'
        except Exception as exc:
            # Preserve a publishing state for an ambiguous send so it can be reconciled.
            state = store.job(job_id)['state']
            if state == 'deleting':
                store.update_job(job_id, error=type(exc).__name__)
                return state
            reason = ('게시 결과 불확실: ' if state == 'publishing' else '') + str(exc)[:200]
            store.update_job(job_id, state='review_pending', error=reason)
            return 'review_pending'
