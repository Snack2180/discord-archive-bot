"""Public ALT pages only; no authenticated or undocumented API access."""
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urlparse
from urllib.request import build_opener, HTTPRedirectHandler, Request


class AltError(ValueError):
    pass


@dataclass
class AltNote:
    note_id: str
    title: str
    transcript: str
    source_url: str


def validate_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme != 'https' or p.hostname not in {'altalt.io', 'www.altalt.io'} or p.username or p.password or p.port not in (None, 443):
        raise AltError('ALT HTTPS 공유 링크만 허용됩니다')
    m = re.fullmatch(r'/(?:ko/)?note/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/?', p.path)
    if not m:
        raise AltError('지원하지 않는 ALT 링크 경로')
    return m[1].lower()


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignore = 0
        self.heading = False
        self.started = False
        self.title = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'nav', 'footer'}:
            self.ignore += 1
        if tag == 'h1' and not self.ignore:
            self.heading = True
            self.started = True

    def handle_endtag(self, tag):
        if tag == 'h1':
            self.heading = False
        if tag in {'script', 'style', 'noscript', 'nav', 'footer'}:
            self.ignore = max(0, self.ignore - 1)

    def handle_data(self, data):
        if self.ignore or not data.strip():
            return
        if self.heading:
            self.title.append(data.strip())
        elif self.started:
            self.parts.append(data.strip())


def parse_note(url: str, html: str) -> AltNote:
    note_id = validate_url(url)
    parser = _Page()
    parser.feed(html)
    text = '\n'.join(parser.parts)
    # Require visible timed transcript. A login/error/summary-only page is not a successful import.
    if not parser.title or not re.search(r'\b\d{1,2}:\d{2}:\d{2}\b', text) or len(text) < 30:
        raise AltError('공유 페이지 전사문을 읽지 못했습니다. TXT를 첨부해주세요')
    return AltNote(note_id, ' '.join(parser.title), text, url)


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_note(url: str) -> AltNote:
    validate_url(url)
    req = Request(url, headers={'User-Agent': 'DiscordArchiveBot/0.1 (public shared note reader)'})
    try:
        with build_opener(_Redirects()).open(req, timeout=20) as response:
            if 'text/html' not in response.headers.get('Content-Type', ''):
                raise AltError('ALT HTML 페이지가 아닙니다')
            data = response.read(5 * 1024**2 + 1)
            if len(data) > 5 * 1024**2:
                raise AltError('공유 페이지 크기 제한 초과')
        return parse_note(url, data.decode('utf-8'))
    except AltError:
        raise
    except Exception as exc:
        raise AltError('ALT 연결 실패. 공유 상태를 확인하거나 TXT를 첨부해주세요') from exc
