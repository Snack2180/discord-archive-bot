import shutil
import subprocess
import unittest
import tempfile
import os
from pathlib import Path


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell Core required')
class StartScriptTests(unittest.TestCase):
    def test_file_mode_reads_local_file_without_echo_and_clears_env(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'token.txt'
            path.write_text('SYNTHETIC_FILE_MARKER\nEXTRA', encoding='utf-8')
            env = dict(os.environ, TEST_TOKEN_PATH=str(path), DISCORD_BOT_TOKEN='')
            command = r'''
function Read-Host {
    param($Prompt, [switch]$AsSecureString)
    return (ConvertTo-SecureString 'BAD FALLBACK' -AsPlainText -Force)
}
& ./scripts/start.ps1 -TokenFile $env:TEST_TOKEN_PATH
if (Test-Path Env:DISCORD_BOT_TOKEN) { Write-Output 'ENV_NOT_CLEARED' }
'''
            result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=20, env=env)
            output = result.stdout + result.stderr
            self.assertIn('line break', output)
            self.assertNotIn('SYNTHETIC_FILE_MARKER', output)
            self.assertNotIn('ENV_NOT_CLEARED', output)

    def test_clipboard_mode_reads_after_prompt_without_echo_and_clears_env(self):
        command = r'''
function Read-Host {
    param($Prompt, [switch]$AsSecureString)
    if ($AsSecureString) { return (ConvertTo-SecureString 'BAD FALLBACK' -AsPlainText -Force) }
    return ''
}
function Get-Clipboard {
    param([switch]$Raw)
    return "SYNTHETIC_CLIPBOARD_MARKER`r`nEXTRA"
}
$env:DISCORD_BOT_TOKEN = ''
& ./scripts/start.ps1 -ClipboardToken
if (Test-Path Env:DISCORD_BOT_TOKEN) { Write-Output 'ENV_NOT_CLEARED' }
'''
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=20)
        output = result.stdout + result.stderr
        self.assertIn('line break', output)
        self.assertNotIn('SYNTHETIC_CLIPBOARD_MARKER', output)
        self.assertNotIn('ENV_NOT_CLEARED', output)
