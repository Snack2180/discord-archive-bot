import os
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass
class Config:
    guild_id: str
    owner_id: str
    inbox_id: str
    review_id: str
    channel_ids: list[str]
    data_dir: Path = Path('data')
    max_file_bytes: int = 25 * 1024**2
    max_storage_bytes: int = 5 * 1024**3
    poll_seconds: int = 60
    delete_inbox: bool = False


def load_config(path: Path) -> Config:
    with path.open('rb') as f:
        raw = tomllib.load(f)
    for key in ('guild_id', 'owner_id', 'inbox_id', 'review_id', 'channel_ids'):
        if not raw.get(key):
            raise ValueError(f'Missing setting: {key}')
    for key in ('guild_id', 'owner_id', 'inbox_id', 'review_id'):
        raw[key] = str(raw[key])
        if not raw[key].isdigit():
            raise ValueError(f'Invalid Discord ID: {key}')
    raw['channel_ids'] = [str(v) for v in raw['channel_ids']]
    if any(not v.isdigit() for v in raw['channel_ids']):
        raise ValueError('Invalid channel_ids')
    if raw['inbox_id'] == raw['review_id'] or set(raw['channel_ids']) & {raw['inbox_id'], raw['review_id']}:
        raise ValueError('Inbox/review/archive channels must be distinct')
    if 'data_dir' in raw:
        raw['data_dir'] = Path(raw['data_dir'])
    cfg = Config(**raw)
    if cfg.max_file_bytes <= 0 or cfg.max_storage_bytes <= 0 or cfg.poll_seconds < 10:
        raise ValueError('Invalid size or poll limit')
    return cfg


class TokenInputError(ValueError):
    """Safe reason code only; never retain the supplied credential in an error."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def token() -> str:
    value = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
    if not value:
        raise TokenInputError('empty')
    if '\r' in value or '\n' in value:
        raise TokenInputError('line_break')
    if any(not ('!' <= char <= '~') for char in value):
        raise TokenInputError('invalid_character')
    return value
