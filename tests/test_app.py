import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AppTests(unittest.TestCase):
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

