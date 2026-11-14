from __future__ import annotations

import base64
import json
import os
import subprocess


_WINDOWS_FILE_DIALOG = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$owner = $null
$dialog = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    $owner = New-Object System.Windows.Forms.Form
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $owner.ShowInTaskbar = $false
    $owner.TopMost = $true
    $owner.Opacity = 0
    $owner.Show()
    $dialog.Title = 'Wybierz pliki multimedialne'
    $dialog.Multiselect = $true
    $dialog.CheckFileExists = $true
    $dialog.RestoreDirectory = $true
    $selected = @()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        $selected = @($dialog.FileNames)
    }
    [Console]::Out.Write((ConvertTo-Json -InputObject $selected -Compress))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($dialog) { $dialog.Dispose() }
    if ($owner) { $owner.Dispose() }
}
'''


def choose_local_files() -> list[str]:
    if os.name != 'nt':
        raise RuntimeError('Wybór plików w oknie systemowym jest dostępny w systemie Windows.')
    script = base64.b64encode(_WINDOWS_FILE_DIALOG.encode('utf-16le')).decode('ascii')
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', script],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(message[-500:] or f'Kod błędu: {result.returncode}')
    try:
        paths = json.loads(result.stdout.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('Okno wyboru zwróciło nieprawidłowe dane.') from exc
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise RuntimeError('Okno wyboru zwróciło nieprawidłowe dane.')
    return paths
