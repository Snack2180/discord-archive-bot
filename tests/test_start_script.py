import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell Core required')
class StartScriptTests(unittest.TestCase):
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
