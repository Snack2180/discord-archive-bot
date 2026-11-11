"""Discord entry point. --check never connects or requires credentials."""
import argparse
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
from .config import load_config, token
from .store import Store
from .runtime import Runtime
from .discord_gateway import DiscordGateway
from .indexing import index_history
from .review import authorize, persistent_reviews
from .commands import search_results

log = logging.getLogger('discord_archive')


def build_bot(cfg, store):
    import discord
    from discord import app_commands

    class OwnerTree(app_commands.CommandTree):
        async def interaction_check(self, interaction):
            try:
                authorize(str(interaction.user.id), str(interaction.guild_id), cfg.owner_id, cfg.guild_id)
                return True
            except PermissionError:
                await interaction.response.send_message('지정 서버의 소유자만 사용할 수 있습니다.', ephemeral=True)
                return False

    class Bot(discord.Client):
        def __init__(self):
            intents = discord.Intents.default()
            intents.message_content = True
            super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
            self.tree = OwnerTree(self)
            self.gateway = DiscordGateway(self, cfg, store)
            extras = json.loads(store.setting('extra_channels', '[]'))
            self.gateway.allowed_channels.extend(str(x) for x in extras if str(x) not in self.gateway.allowed_channels)
            self.runtime = Runtime(store, self.gateway, cfg.data_dir)
            self.worker_task = None

        async def setup_hook(self):
            for item_id, message_id in persistent_reviews(store):
                self.add_view(ReviewView(item_id), message_id=int(message_id))
            self.tree.copy_global_to(guild=discord.Object(id=int(cfg.guild_id)))
            await self.tree.sync(guild=discord.Object(id=int(cfg.guild_id)))

        async def on_ready(self):
            if self.worker_task is None:
                self.worker_task = asyncio.create_task(self.worker())
            log.info('Bot connected. Initial indexing and inbox replay enabled.')

        async def worker(self):
            while not self.is_closed():
                try:
                    if store.setting('initial_scan') != 'done':
                        report = await index_history(self.gateway, store, self.gateway.allowed_channels)
                        if not report.errors:
                            store.set_setting('initial_scan', 'done')
                        store.set_setting('index_report', json.dumps(report.__dict__))
                    await self.runtime.poll()
                    for job in store.jobs({'review_pending'}):
                        await self.notify(job)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning('Background operation paused: %s', type(exc).__name__)
                await asyncio.sleep(cfg.poll_seconds)

        async def notify(self, job):
            if job['review_message']:
                self.add_view(ReviewView(job['id']), message_id=int(job['review_message']))
                try:
                    await self.gateway.channel(cfg.review_id).fetch_message(int(job['review_message']))
                    return
                except discord.NotFound:
                    store.update_job(job['id'], review_message='')
            item = store.get_item(job['id'])
            target = f'<#{job["destination"]}>' if job['destination'] else '선택 필요'
            embed = discord.Embed(title='분류 확인: ' + (item.title or '자료')[:150], description=(item.body or item.extracted_text or '첨부파일 자료')[:1200])
            embed.add_field(name='자료 ID', value=item.id)
            embed.add_field(name='추천 채널', value=target)
            embed.add_field(name='태그', value=', '.join(item.tags)[:500] or '없음')
            embed.add_field(name='상태', value=(job['error'] or '분류 확인이 필요합니다')[:500], inline=False)
            if item.source_url:
                embed.add_field(name='원본', value=item.source_url, inline=False)
            msg = await self.gateway.channel(cfg.review_id).send(embed=embed, view=ReviewView(item.id))
            store.update_job(item.id, review_message=str(msg.id))

        async def close(self):
            if self.worker_task:
                self.worker_task.cancel()
                try:
                    await self.worker_task
                except asyncio.CancelledError:
                    pass
            await super().close()

    class TagsModal(discord.ui.Modal):
        def __init__(self, item_id):
            super().__init__(title='태그 수정')
            self.item_id = item_id
            self.tags = discord.ui.TextInput(label='쉼표로 태그 구분', default=', '.join(store.get_item(item_id).tags), required=False, max_length=300)
            self.add_item(self.tags)

        async def on_submit(self, interaction):
            authorize(str(interaction.user.id), str(interaction.guild_id), cfg.owner_id, cfg.guild_id)
            job = store.job(self.item_id)
            if not job or job['state'] != 'review_pending':
                await interaction.response.send_message('이미 처리된 자료입니다.', ephemeral=True)
                return
            item = store.get_item(self.item_id)
            item.tags = list(dict.fromkeys(t.strip() for t in self.tags.value.split(',') if t.strip()))[:20]
            store.upsert_item(item)
            await interaction.response.send_message('태그를 저장했습니다. 보관 승인 시 학습에 반영합니다.', ephemeral=True)

    class NewChannelModal(discord.ui.Modal):
        def __init__(self, item_id):
            super().__init__(title='새 채널 생성 승인 및 보관')
            self.item_id = item_id
            self.name_input = discord.ui.TextInput(label='생성할 채널 이름', max_length=80)
            self.add_item(self.name_input)

        async def on_submit(self, interaction):
            authorize(str(interaction.user.id), str(interaction.guild_id), cfg.owner_id, cfg.guild_id)
            await interaction.response.defer(ephemeral=True)
            async with bot.runtime.lock:
                job = store.job(self.item_id)
                if not job or job['state'] != 'review_pending' or job['receipt'] or job['error'].startswith(('게시','보관')):
                    await interaction.followup.send('이 작업은 이미 처리됐거나 게시 확인이 필요합니다.', ephemeral=True)
                    return
                channel_id = await bot.gateway.create_channel(self.name_input.value)
                # Persist the selected destination before releasing the owner-action lock.
                store.update_job(self.item_id, destination=channel_id)
            result = await bot.runtime.confirm(self.item_id, channel_id, store.get_item(self.item_id).tags)
            await interaction.followup.send(f'새 채널 <#{channel_id}> · 처리 상태: {result}', ephemeral=True)

    class ReviewView(discord.ui.View):
        def __init__(self, item_id):
            super().__init__(timeout=None)
            self.item_id = item_id
            choose = discord.ui.ChannelSelect(placeholder='기존 보관 채널 선택', channel_types=[discord.ChannelType.text], custom_id='archive:channel:' + item_id)
            choose.callback = self.choose
            self.add_item(choose)
            for name, action in [('보관 승인','confirm'), ('태그 수정','tags'), ('새 채널 만들고 보관','new')]:
                button = discord.ui.Button(label=name, custom_id=f'archive:{action}:{item_id}', style=discord.ButtonStyle.primary if action == 'confirm' else discord.ButtonStyle.secondary)
                button.callback = getattr(self, action)
                self.add_item(button)

        async def interaction_check(self, interaction):
            try:
                authorize(str(interaction.user.id), str(interaction.guild_id), cfg.owner_id, cfg.guild_id)
                return True
            except PermissionError:
                await interaction.response.send_message('소유자만 확인할 수 있습니다.', ephemeral=True)
                return False

        async def on_error(self, interaction, error, item):
            text = '처리를 완료하지 못했습니다. /상태로 확인해주세요. 원본은 유지됩니다.'
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            log.warning('Review action failed: %s', type(error).__name__)

        async def choose(self, interaction):
            channel_id = interaction.data['values'][0]
            job = store.job(self.item_id)
            if channel_id not in bot.gateway.allowed_channels or not job or job['state'] != 'review_pending':
                await interaction.response.send_message('허용된 보관 채널만 선택할 수 있습니다.', ephemeral=True)
                return
            store.update_job(self.item_id, destination=channel_id)
            await interaction.response.send_message(f'<#{channel_id}> 선택됨. 태그 확인 후 보관 승인을 눌러주세요.', ephemeral=True)

        async def confirm(self, interaction):
            await interaction.response.defer(ephemeral=True)
            job = store.job(self.item_id)
            result = await bot.runtime.confirm(self.item_id, job['destination'], store.get_item(self.item_id).tags)
            await interaction.followup.send('처리 상태: ' + result, ephemeral=True)

        async def tags(self, interaction):
            await interaction.response.send_modal(TagsModal(self.item_id))

        async def new(self, interaction):
            await interaction.response.send_modal(NewChannelModal(self.item_id))

    bot = Bot()

    @bot.tree.command(name='검색', description='제목·본문·추출 내용 키워드 검색')
    async def search(interaction: discord.Interaction, 키워드: str, 페이지: int = 1):
        await interaction.response.send_message(search_results(store, 키워드, [], 페이지), ephemeral=True)

    @bot.tree.command(name='태그', description='쉼표로 구분한 모든 태그를 포함하는 자료 검색')
    async def tags_search(interaction: discord.Interaction, 태그: str, 페이지: int = 1):
        tags = [x.strip() for x in 태그.split(',') if x.strip()]
        await interaction.response.send_message(search_results(store, '', tags, 페이지), ephemeral=True)

    @bot.tree.command(name='기준', description='기존 자료의 정답 분류와 태그를 지정해 학습 (원문 이동 없음)')
    async def feedback(interaction: discord.Interaction, 자료id: str, 채널: discord.TextChannel, 태그: str = ''):
        if str(채널.id) not in bot.gateway.allowed_channels:
            raise ValueError('허용 채널 아님')
        job = store.job(자료id)
        if job and job['state'] != 'done':
            raise ValueError('수집함 작업은 분류확인에서 처리해주세요')
        await interaction.response.defer(ephemeral=True)
        store.record_feedback(자료id, str(채널.id), [t.strip() for t in 태그.split(',') if t.strip()])
        stats = bot.runtime.retrain()
        await interaction.followup.send(f'학습 반영 완료. 원문은 그대로입니다. 검증: {stats}', ephemeral=True)

    @bot.tree.command(name='색인', description='허용 채널의 새 기록을 읽기 전용으로 색인')
    async def index(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        report = await index_history(bot.gateway, store, bot.gateway.allowed_channels)
        await interaction.followup.send(f'색인 {report.indexed}건, 추출 확인 {report.issues}건, 채널 오류 {report.errors}', ephemeral=True)

    @bot.tree.command(name='학습', description='확정된 예시로 로컬 분류기를 다시 학습')
    async def learn(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        stats = bot.runtime.retrain()
        await interaction.followup.send(f'학습 완료 · 검증 통계 {stats}', ephemeral=True)

    @bot.tree.command(name='상태', description='대기·실패·자동 분류 상태 확인')
    async def status(interaction: discord.Interaction):
        from collections import Counter
        counts = dict(Counter(j['state'] for j in store.jobs()))
        pending = '\n'.join(f'{j["id"]}: {j["error"][:80]}' for j in store.jobs({'review_pending','deleting'})[:10])
        await interaction.response.send_message(f'작업 {counts}\n수집함 원본 삭제: {cfg.delete_inbox}\n{pending}'[:1900], ephemeral=True)

    @bot.tree.command(name='태그별칭', description='같은 뜻의 태그를 대표 태그로 통합')
    async def alias(interaction: discord.Interaction, 별칭: str, 대표태그: str):
        store.set_alias(별칭, 대표태그)
        await interaction.response.send_message('태그 별칭을 반영했습니다.', ephemeral=True)

    @bot.tree.command(name='재시도', description='게시 결과가 불확실한 작업을 재게시 없이 다시 검증')
    async def retry(interaction: discord.Interaction, 자료id: str):
        await interaction.response.defer(ephemeral=True)
        result = await bot.runtime.retry(자료id)
        await interaction.followup.send('재검증 상태: ' + result, ephemeral=True)

    @bot.tree.command(name='재수집', description='ALT/추출 실패 자료를 명시적으로 다시 분석')
    async def reimport(interaction: discord.Interaction, 자료id: str):
        job = store.job(자료id)
        if not job or job['state'] != 'review_pending' or job['receipt'] or job['error'].startswith(('게시','보관')):
            raise ValueError('재수집 가능한 대기 자료가 아닙니다')
        await interaction.response.defer(ephemeral=True)
        # Reload the actual inbox message so edited content is indexed and original deletion is safe.
        msg = await bot.gateway.channel(cfg.inbox_id).fetch_message(int(자료id))
        store.update_job(자료id, state='received', error='')
        result = await bot.runtime.ingest(bot.gateway.item_from_message(msg))
        await interaction.followup.send('재수집 상태: ' + result, ephemeral=True)

    @bot.tree.command(name='백업', description='PC에 DB와 학습 기준 백업 (첨부파일은 data 폴더 별도 복사)')
    async def backup(interaction: discord.Interaction):
        from datetime import datetime
        path = cfg.data_dir / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite')
        store.backup(path)
        await interaction.response.send_message('로컬 data/backups에 DB 백업을 만들었습니다.', ephemeral=True)

    @bot.tree.error
    async def on_command_error(interaction, error):
        message = '요청을 완료하지 못했습니다. 자료 ID·권한·상태를 확인해주세요. 원본은 유지됩니다.'
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        log.warning('Command failed: %s', type(error).__name__)

    return bot


class InstanceLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            import os
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('같은 데이터 폴더에서 봇이 이미 실행 중입니다')
        return self

    def __exit__(self, *args):
        self.file.close()


def main():
    parser = argparse.ArgumentParser(description='Free local-learning Discord archive')
    parser.add_argument('--config', type=Path, default=Path('config.toml'))
    parser.add_argument('--check', action='store_true', help='Check config/dependencies without connecting')
    args = parser.parse_args()
    stage = 'configuration'
    try:
        cfg = load_config(args.config)
        if args.check:
            print('Configuration OK (no Discord connection made)')
            for module in ('discord', 'pypdf', 'PIL', 'pytesseract'):
                print(f'{module}: ' + ('installed' if importlib.util.find_spec(module) else 'missing'))
            print('Local learning / text / DOCX: Python standard library')
            return
        stage = 'token input'
        secret = token()
        stage = 'instance lock'
        with InstanceLock(cfg.data_dir / 'bot.lock'):
            stage = 'database'
            store = Store(cfg.data_dir / 'archive.sqlite')
            try:
                stage = 'bot initialization'
                bot = build_bot(cfg, store)
                stage = 'Discord connection'
                bot.run(secret, log_level=logging.WARNING)
            finally:
                store.close()
    except (ValueError, OSError, ImportError) as exc:
        # Never print config payload, environment variables, or secret-bearing exception bodies.
        print(f'Startup failed ({type(exc).__name__}; stage: {stage}).')
        hints = {
            'configuration': 'Check config.toml IDs and settings.',
            'token input': 'Bot token input was empty. Run scripts/start.ps1 and paste the token at its hidden prompt.',
            'instance lock': 'Check whether another bot instance is already running with the same data directory.',
            'database': 'Check the data directory and database file access.',
            'bot initialization': 'Offline bot initialization failed. Report this stage without sharing your token.',
            'Discord connection': 'Discord startup failed. Report this stage without sharing your token.',
        }
        print(hints[stage])
        raise SystemExit(1)


if __name__ == '__main__':
    main()
