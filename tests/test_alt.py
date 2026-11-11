import unittest
from discord_archive.alt import parse_note, validate_url, AltError


NOTE = '11111111-2222-3333-4444-555555555555'


class AltTests(unittest.TestCase):
    def test_public_transcript_and_time(self):
        html = '<html><body><nav>로그인</nav><main><h1>강의 예시</h1><p>16:06:08</p><p>전사문 첫 문장입니다.</p><p>16:07:00</p><p>다음 문장입니다.</p></main><script>secret()</script></body></html>'
        note = parse_note('https://altalt.io/note/' + NOTE, html)
        self.assertEqual(note.title, '강의 예시')
        self.assertIn('16:06:08', note.transcript)
        self.assertIn('전사문 첫 문장입니다.', note.transcript)
        self.assertNotIn('secret()', note.transcript)
        self.assertEqual(note.note_id, NOTE)

    def test_bad_urls_and_missing_content(self):
        for url in ['http://altalt.io/note/' + NOTE, 'https://localhost/note/' + NOTE, 'https://altalt.io.evil.com/note/' + NOTE, 'https://altalt.io:444/note/' + NOTE, 'https://user:pass@altalt.io/note/' + NOTE]:
            with self.assertRaises(AltError):
                validate_url(url)
        self.assertEqual(validate_url('https://altalt.io/ko/note/' + NOTE), NOTE)
        with self.assertRaises(AltError):
            parse_note('https://altalt.io/note/' + NOTE, '<h1>로그인</h1><p>로그인이 필요합니다</p>')

