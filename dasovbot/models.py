from dataclasses import dataclass, field


@dataclass
class VideoOrigin:
    width: int | None = None
    height: int | None = None
    format: str | None = None

    def to_dict(self) -> dict:
        return {
            'width': self.width,
            'height': self.height,
            'format': self.format,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'VideoOrigin':
        return cls(
            width=data.get('width'),
            height=data.get('height'),
            format=data.get('format'),
        )


@dataclass
class VideoInfo:
    title: str
    description: str = ""
    file_id: str | None = None
    webpage_url: str | None = None
    upload_date: str | None = None
    timestamp: str | None = None
    thumbnail: str | None = None
    duration: int = 0
    uploader_url: str | None = None
    width: int | None = None
    height: int | None = None
    caption: str = ""
    url: str | None = None
    filepath: str | None = None
    filename: str | None = None
    format: str | None = None
    entries: list | None = None
    origin: VideoOrigin | None = None
    source: str | None = None
    processed_at: str | None = None

    def to_dict(self) -> dict:
        d = {
            'title': self.title,
            'description': self.description,
            'file_id': self.file_id,
            'webpage_url': self.webpage_url,
            'upload_date': self.upload_date,
            'timestamp': self.timestamp,
            'thumbnail': self.thumbnail,
            'duration': self.duration,
            'uploader_url': self.uploader_url,
            'width': self.width,
            'height': self.height,
            'caption': self.caption,
            'url': self.url,
            'filepath': self.filepath,
            'filename': self.filename,
            'format': self.format,
            'entries': self.entries,
            'source': self.source,
            'processed_at': self.processed_at,
        }
        if self.origin is not None:
            d['origin'] = self.origin.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'VideoInfo':
        origin_data = data.get('origin')
        origin = VideoOrigin.from_dict(origin_data) if origin_data else None
        return cls(
            title=data.get('title', ''),
            description=data.get('description', ''),
            file_id=data.get('file_id'),
            webpage_url=data.get('webpage_url'),
            upload_date=data.get('upload_date'),
            timestamp=data.get('timestamp'),
            thumbnail=data.get('thumbnail'),
            duration=int(data.get('duration') or 0),
            uploader_url=data.get('uploader_url'),
            width=data.get('width'),
            height=data.get('height'),
            caption=data.get('caption', ''),
            url=data.get('url'),
            filepath=data.get('filepath'),
            filename=data.get('filename'),
            format=data.get('format'),
            entries=data.get('entries'),
            origin=origin,
            source=data.get('source'),
            processed_at=data.get('processed_at'),
        )


@dataclass
class IntentMessage:
    chat: str
    message: str

    def to_dict(self) -> dict:
        return {'chat': self.chat, 'message': self.message}

    @classmethod
    def from_dict(cls, data: dict) -> 'IntentMessage':
        return cls(chat=data['chat'], message=data['message'])


@dataclass
class Intent:
    chat_ids: list[str] = field(default_factory=list)
    inline_message_ids: list[str] = field(default_factory=list)
    messages: list[IntentMessage] = field(default_factory=list)
    priority: int = 0
    ignored: bool = False
    source: str | None = None

    def to_dict(self) -> dict:
        return {
            'chat_ids': self.chat_ids,
            'inline_message_ids': self.inline_message_ids,
            'messages': [m.to_dict() for m in self.messages],
            'priority': self.priority,
            'ignored': self.ignored,
            'source': self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Intent':
        messages = [IntentMessage.from_dict(m) for m in data.get('messages', [])]
        return cls(
            chat_ids=data.get('chat_ids', []),
            inline_message_ids=data.get('inline_message_ids', []),
            messages=messages,
            priority=data.get('priority', 0),
            ignored=data.get('ignored', False),
            source=data.get('source'),
        )


@dataclass
class Subscription:
    chat_ids: list[str] = field(default_factory=list)
    title: str = ""
    uploader: str = ""
    uploader_videos: str = ""

    def to_dict(self) -> dict:
        return {
            'chat_ids': self.chat_ids,
            'title': self.title,
            'uploader': self.uploader,
            'uploader_videos': self.uploader_videos,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Subscription':
        return cls(
            chat_ids=data.get('chat_ids', []),
            title=data.get('title', ''),
            uploader=data.get('uploader', ''),
            uploader_videos=data.get('uploader_videos', ''),
        )


@dataclass
class TemporaryInlineQuery:
    timestamp: str = ""
    results: list = field(default_factory=list)
    inline_queries: dict = field(default_factory=dict)
    marked: bool = False
    ignored: bool = False
