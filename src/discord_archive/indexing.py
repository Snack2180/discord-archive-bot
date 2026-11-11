from dataclasses import dataclass, field


@dataclass
class IndexReport:
    indexed: int = 0
    issues: int = 0
    errors: dict = field(default_factory=dict)


async def index_history(gateway, store, channel_ids: list[str]) -> IndexReport:
    if not set(channel_ids) <= set(gateway.allowed_channels):
        raise ValueError('Channel outside allowlist')
    report = IndexReport()
    for channel_id in channel_ids:
        key = 'history:' + channel_id
        try:
            async for item in gateway.history(channel_id, store.setting(key)):
                if item.channel_id != channel_id:
                    raise ValueError('Channel mismatch')
                if not store.has_item(item.id):
                    item = await gateway.prepare(item)
                    store.upsert_item(item)
                    report.indexed += 1
                    report.issues += bool(item.issues)
                store.set_setting(key, item.source_message_id)
        except Exception as exc:
            report.errors[channel_id] = type(exc).__name__
    return report
