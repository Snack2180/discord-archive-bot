import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AppTests(unittest.TestCase):
    def test_multiline_token_is_rejected_before_discord_initialization(self):
        env = dict(os.environ)
        env['DISCORD_BOT_TOKEN'] = 'FAKE_TOKEN_MARKER\r\nCOPIED_EXTRA_LINE'
        proc = subprocess.run([sys.executable, '-m', 'discord_archive.app', '--config', 'config.example.toml'], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('stage: token input', proc.stdout)
        self.assertIn('line break', proc.stdout)
        self.assertNotIn('FAKE_TOKEN_MARKER', proc.stdout + proc.stderr)
        self.assertNotIn('COPIED_EXTRA_LINE', proc.stdout + proc.stderr)

    def test_token_rejects_non_header_characters_without_echoing_value(self):
        from unittest.mock import patch
        from discord_archive.config import token
        for value in ('FAKE TOKEN', 'FAKE\tTOKEN', '토큰가짜', 'FAKE\x01TOKEN'):
            with self.subTest(value_type=repr(value[:4])):
                with patch.dict(os.environ, {'DISCORD_BOT_TOKEN': value}):
                    with self.assertRaises(ValueError) as ctx:
                        token()
                    self.assertNotIn(value, str(ctx.exception))

    def test_token_allows_outer_clipboard_whitespace(self):
        from unittest.mock import patch
        from discord_archive.config import token
        with patch.dict(os.environ, {'DISCORD_BOT_TOKEN': '  fake.example.value\r\n'}):
            self.assertEqual(token(), 'fake.example.value')

    def test_empty_token_reports_input_stage(self):
        env = dict(os.environ)
        env.pop('DISCORD_BOT_TOKEN', None)
        proc = subprocess.run([sys.executable, '-m', 'discord_archive.app', '--config', 'config.example.toml'], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('stage: token input', proc.stdout)

    def test_initialization_error_does_not_print_secret_exception(self):
        import contextlib
        import io
        from unittest.mock import patch
        from discord_archive.app import main
        from discord_archive.config import load_config
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(Path('config.example.toml'))
            cfg.data_dir = Path(d)
            output = io.StringIO()
            with patch('sys.argv', ['app']), patch('discord_archive.app.load_config', return_value=cfg), patch('discord_archive.app.token', return_value='TEST_SECRET'), patch('discord_archive.app.build_bot', side_effect=ValueError('TEST_SECRET')), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit):
                    main()
            self.assertIn('stage: bot initialization', output.getvalue())
            self.assertIn('Location:', output.getvalue())
            self.assertNotIn('TEST_SECRET', output.getvalue())

    def test_diagnostic_runs_without_token_or_optional_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.toml'
            p.write_text('guild_id="1"\nowner_id="2"\ninbox_id="3"\nreview_id="4"\nchannel_ids=["5"]\n', encoding='utf-8')
            env = dict(os.environ)
            env.pop('DISCORD_BOT_TOKEN', None)
            proc = subprocess.run([sys.executable, '-m', 'discord_archive.app', '--check', '--config', str(p)], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('Configuration OK', proc.stdout)
            self.assertNotIn('Traceback', proc.stdout)

