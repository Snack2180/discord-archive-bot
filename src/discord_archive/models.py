from dataclasses import dataclass, field, asdict


@dataclass
class Attachment:
    id: str
    filename: str
    local_path: str = ''
    sha256: str = ''
    size: int = 0
    url: str = ''


@dataclass
class Item:
    id: str
    source_message_id: str
    channel_id: str
    author_id: str
    created_at: str
    body: str
    extracted_text: str = ''
    attachments: list[Attachment] = field(default_factory=list)
    source_url: str = ''
    title: str = ''
    tags: list[str] = field(default_factory=list)
    destination_id: str = ''
    archive_url: str = ''
    issues: list[str] = field(default_factory=list)

    def text(self):
        return '\n'.join([self.title, self.body, self.extracted_text]).strip()

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data['attachments'] = [Attachment(**a) for a in data.get('attachments', [])]
        return cls(**data)

    def as_dict(self):
        return asdict(self)


@dataclass
class Extraction:
    text: str = ''
    status: str = 'ok'
    reason: str = ''


@dataclass
class Prediction:
    channel_id: str = ''
    score: float = 0.0
    tags: list[str] = field(default_factory=list)
    model_version: str = ''
    review_required: bool = True


@dataclass
class PublishReceipt:
    message_ids: list[str]
    attachment_hashes: list[str] = field(default_factory=list)
