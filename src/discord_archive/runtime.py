import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from .learning import train, predict, save_model, load_model
from .organize import process_job
from .review import approve


class Runtime:
    def __init__(self, store, gateway, data_dir: Path):
        self.store, self.gateway, self.root = store, gateway, data_dir
        self.model_path = data_dir / 'models' / 'current.json'
        self.model = load_model(self.model_path)
        self.lock = asyncio.Lock()
        # v0.1 File context-manager failure happened before channel.send, so no
        # publication exists when there is also no persisted receipt. Unlock
        # only this exact historical error; keep real network ambiguity gated.
        known_unsent = "게시 결과 불확실: 'File' object does not support the context manager protocol"
        for job in self.store.jobs({'review_pending'}):
            if not job['receipt'] and job['error'] == known_unsent:
                self.store.update_job(job['id'], error='')

    def retrain(self):
        version = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        self.model = train(self.store.examples(), version)
        save_model(self.model, self.root / 'models' / (version + '.json'))
        save_model(self.model, self.model_path)
        return self.model.validation

    def recommend(self, item):
        metadata = {'tags': self.store.vocabulary()}
        result = predict(item.text(), metadata, self.model)
        if not result.channel_id:
            weak = train(self.store.weak_examples(), 'initial-channel-hints')
            result = predict(item.text(), metadata, weak)
            result.review_required = True
        return result

    async def ingest(self, item):
        async with self.lock:
            if item.channel_id != self.gateway.inbox_id:
                raise ValueError('수집함 자료만 처리할 수 있습니다')
            job = self.store.job(item.id)
            if job and job['state'] != 'received':
                return job['state']
            self.store.upsert_item(item)
            self.store.create_job(item.id)
            item = await self.gateway.prepare(item)
            result = self.recommend(item)
            item.tags = result.tags
            self.store.upsert_item(item)
            state = 'review_pending' if item.issues or result.review_required else 'ready'
            self.store.update_job(item.id, state=state, destination=result.channel_id, error='; '.join(item.issues)[:500])
        return await process_job(item.id, self.store, self.gateway)

    async def confirm(self, item_id, channel_id, tags):
        async with self.lock:
            if not approve(self.store, item_id, channel_id, tags, self.gateway.allowed_channels):
                return 'already_processed'
            self.retrain()
        return await process_job(item_id, self.store, self.gateway)

    async def poll(self):
        # Replay persisted jobs first. A failed network call leaves the cursor unchanged.
        for job in self.store.jobs({'ready','publishing','published','verified','deleting'}):
            await process_job(job['id'], self.store, self.gateway)
        async for item in self.gateway.history(self.gateway.inbox_id, self.store.setting('inbox_cursor')):
            await self.ingest(item)
            self.store.set_setting('inbox_cursor', item.source_message_id)

    async def retry(self, item_id):
        async with self.lock:
            job = self.store.job(item_id)
            if not job or job['state'] != 'review_pending' or not job['destination']:
                raise ValueError('재검증할 작업이 없습니다')
            if not (job['receipt'] or job['error'].startswith(('게시', '보관'))):
                raise ValueError('이 작업은 분류 확인 또는 재분석이 필요합니다')
            self.store.update_job(item_id, state='publishing')
        return await process_job(item_id, self.store, self.gateway)
