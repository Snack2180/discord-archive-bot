import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from .models import Item, Attachment, PublishReceipt
from .extract import extract_file
from .alt import fetch_note, AltError


def safe_filename(name: str) -> str:
    return re.sub(r'[^\w.()+ -]', '_', name, flags=re.UNICODE).strip('. ')[:100] or 'file'


def archive_files(item: Item, root: Path):
    root.mkdir(parents=True, exist_ok=True)
    result = []
    for i, a in enumerate(item.attachments):
        path = Path(a.local_path)
        if not a.local_path or not path.is_file():
            raise ValueError('첨부파일 보관 준비 미완료')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if a.sha256 and digest != a.sha256:
            raise ValueError('로컬 첨부 해시 불일치')
        result.append((path, f'{i:03d}-{safe_filename(a.filename)}', digest))
    body = root / f'{item.id}-original.txt'
    # Generated transcript and original body are both retained, never shortened to a summary.
    text = f'원작성자: {item.author_id}\n원래 시간: {item.created_at}\n출처: {item.source_url}\n\n[원문]\n{item.body}\n\n[추출 내용]\n{item.extracted_text}'
    body.write_text(text, encoding='utf-8', newline='\n')
    result.append((body, 'original.txt', hashlib.sha256(body.read_bytes()).hexdigest()))
    return result


class DiscordGateway:
    def __init__(self, bot, config, store):
        self.bot, self.config, self.store = bot, config, store
        self.allowed_channels = list(config.channel_ids)
        self.inbox_id, self.review_id = config.inbox_id, config.review_id
        self.delete_inbox = config.delete_inbox
        self.root = config.data_dir / 'files'
        self.root.mkdir(parents=True, exist_ok=True)

    def channel(self, channel_id):
        channel = self.bot.get_channel(int(channel_id))
        if channel is None or str(channel.guild.id) != self.config.guild_id:
            raise ValueError('지정 서버의 채널을 찾을 수 없습니다')
        return channel

    def item_from_message(self, message):
        return Item(str(message.id), str(message.id), str(message.channel.id), str(message.author.id),
                    message.created_at.isoformat(), message.content,
                    attachments=[Attachment(str(a.id), a.filename, size=a.size, url=a.url) for a in message.attachments],
                    source_url=message.jump_url, title=(message.content.splitlines() or ['파일 자료'])[0][:100])

    async def history(self, channel_id, after=''):
        import discord
        if channel_id not in self.allowed_channels + [self.inbox_id]:
            raise ValueError('허용되지 않은 채널')
        channel = self.channel(channel_id)
        async for message in channel.history(limit=None, oldest_first=True, after=discord.Object(id=int(after)) if after else None):
            if message.author.bot:
                continue
            yield self.item_from_message(message)

    def storage_used(self):
        return sum(p.stat().st_size for p in self.config.data_dir.rglob('*') if p.is_file())

    async def prepare(self, item):
        item.issues = []
        parts = []
        message = None
        if item.attachments:
            message = await self.channel(item.channel_id).fetch_message(int(item.source_message_id))
        for a in item.attachments:
            try:
                source = next(x for x in message.attachments if str(x.id) == a.id)
                if source.size > self.config.max_file_bytes or self.storage_used() + source.size > self.config.max_storage_bytes:
                    raise ValueError('파일 또는 로컬 저장공간 상한 초과')
                folder = self.root / item.id
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (a.id + '-' + safe_filename(a.filename))
                if not path.exists():
                    temp = path.with_suffix(path.suffix + '.part')
                    try:
                        await source.save(temp)
                        if temp.stat().st_size != source.size or temp.stat().st_size > self.config.max_file_bytes:
                            raise ValueError('첨부 다운로드 크기 불일치')
                        temp.replace(path)
                    finally:
                        temp.unlink(missing_ok=True)
                a.local_path, a.size = str(path), path.stat().st_size
                a.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                extraction = await asyncio.to_thread(extract_file, path)
                parts.append(a.filename + '\n' + extraction.text)
                if extraction.status != 'ok':
                    item.issues.append(a.filename + ': ' + extraction.reason)
            except Exception as exc:
                item.issues.append(a.filename + ': ' + type(exc).__name__)
        links = re.findall(r'https://(?:www\.)?altalt\.io/(?:ko/)?note/[0-9a-fA-F-]+', item.body)
        for url in dict.fromkeys(links):
            try:
                note = await asyncio.to_thread(fetch_note, url)
                prior = self.store.setting('alt:' + note.note_id)
                if prior and prior != item.id:
                    raise AltError('이미 가져온 노트입니다: ' + prior)
                parts.append(note.title + '\n' + note.transcript)
                item.title = note.title
                self.store.set_setting('alt:' + note.note_id, item.id)
            except AltError as exc:
                item.issues.append('ALT: ' + str(exc))
        item.extracted_text = '\n\n'.join(parts)
        return item

    def files(self, item):
        return archive_files(item, self.root / item.id)

    def marker(self, job_id, part, total):
        return f'[archive:{job_id}:{part}/{total}]'

    async def preflight(self, item, channel_id):
        if channel_id not in self.allowed_channels:
            raise ValueError('목적지 허용 필요')
        channel = self.channel(channel_id)
        permissions = channel.permissions_for(channel.guild.me)
        if not all((permissions.view_channel, permissions.send_messages, permissions.attach_files, permissions.read_message_history)):
            raise ValueError('보관 채널 권한이 부족합니다')
        files = self.files(item)
        if self.storage_used() > self.config.max_storage_bytes:
            raise ValueError('로컬 저장공간 상한 초과')
        limit = min(self.config.max_file_bytes, channel.guild.filesize_limit)
        if any(path.stat().st_size > limit for path, _, _ in files):
            raise ValueError('Discord 첨부 크기 상한 초과')

    async def publish(self, item, channel_id, job_id):
        import discord
        await self.preflight(item, channel_id)
        channel = self.channel(channel_id)
        files = self.files(item)
        ids, hashes = [], []
        for i, (path, name, digest) in enumerate(files):
            header = f'{self.marker(job_id, i, len(files))}\n{item.title[:100]}\n태그: {", ".join(item.tags)[:300]}\n원작성자: {item.author_id} · {item.created_at}\n{item.source_url}\n{item.body[:700]}'
            upload = discord.File(path, filename=name)
            try:
                msg = await channel.send(header[:1900], file=upload, allowed_mentions=discord.AllowedMentions.none())
            finally:
                upload.close()
            ids.append(str(msg.id))
            hashes.append(digest)
            # Persist each successful send before another network operation.
            self.store.update_job(job_id, receipt=json.dumps(asdict(PublishReceipt(ids, hashes))))
        return PublishReceipt(ids, hashes)

    async def reconcile(self, job_id, channel_id):
        item = self.store.get_item(job_id)
        expected = self.files(item)
        found = {}
        channel = self.channel(channel_id)
        import discord
        async for message in channel.history(limit=None, after=discord.Object(id=int(item.source_message_id)), oldest_first=True):
            if message.author.id != self.bot.user.id:
                continue
            for i in range(len(expected)):
                if message.content.startswith(self.marker(job_id, i, len(expected)) + '\n'):
                    if i in found:
                        raise ValueError('보관 게시 중복 발견: 직접 확인 필요')
                    found[i] = str(message.id)
        if len(found) != len(expected):
            return None
        return PublishReceipt([found[i] for i in range(len(expected))], [x[2] for x in expected])

    async def verify(self, receipt, item):
        expected = self.files(item)
        job = self.store.job(item.id)
        if len(receipt.message_ids) != len(expected) or receipt.attachment_hashes != [x[2] for x in expected]:
            return False
        channel = self.channel(job['destination'])
        for i, message_id in enumerate(receipt.message_ids):
            msg = await channel.fetch_message(int(message_id))
            if msg.author.id != self.bot.user.id or not msg.content.startswith(self.marker(item.id, i, len(expected)) + '\n') or len(msg.attachments) != 1:
                return False
            a = msg.attachments[0]
            path, filename, digest = expected[i]
            if a.filename != filename or a.size != path.stat().st_size:
                return False
            if hashlib.sha256(await a.read()).hexdigest() != digest:
                return False
        return True

    async def delete_source(self, message_id):
        import discord
        item = self.store.get_item(message_id)
        if item.channel_id != self.inbox_id:
            raise ValueError('수집함 외 삭제 금지')
        try:
            msg = await self.channel(self.inbox_id).fetch_message(int(message_id))
            if msg.content != item.body or {str(a.id) for a in msg.attachments} != {a.id for a in item.attachments}:
                raise ValueError('원문이 수정되어 삭제하지 않았습니다')
            await msg.delete()
        except discord.NotFound:
            pass

    def archive_url(self, channel_id, message_id):
        return f'https://discord.com/channels/{self.config.guild_id}/{channel_id}/{message_id}'

    async def create_channel(self, name):
        name = re.sub(r'[^\w가-힣-]', '-', name.strip().lower())[:80].strip('-')
        if not name:
            raise ValueError('채널 이름이 필요합니다')
        guild = self.bot.get_guild(int(self.config.guild_id))
        if any(c.name == name for c in guild.text_channels):
            raise ValueError('같은 이름의 채널이 있습니다')
        channel = await guild.create_text_channel(name, reason='Owner approved archive category')
        self.allowed_channels.append(str(channel.id))
        self.store.set_setting('extra_channels', json.dumps([c for c in self.allowed_channels if c not in self.config.channel_ids]))
        return str(channel.id)
