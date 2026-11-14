from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from app import dialogs


@pytest.mark.skipif(os.name != 'nt', reason='Systemowe okno wyboru plików jest przeznaczone dla Windows.')
@pytest.mark.parametrize('paths', [[], [r'D:\Filmy\żółw.mp4'], [r'D:\Filmy\pierwszy.mp4', r'D:\Filmy\drugi plik.mp4']])
def test_windows_dialog_preserves_selection(monkeypatch, paths):
    result = SimpleNamespace(returncode=0, stdout=json.dumps(paths, ensure_ascii=False).encode('utf-8'), stderr=b'')
    monkeypatch.setattr(dialogs.subprocess, 'run', lambda *args, **kwargs: result)
    assert dialogs.choose_local_files() == paths


@pytest.mark.skipif(os.name != 'nt', reason='Systemowe okno wyboru plików jest przeznaczone dla Windows.')
def test_windows_dialog_reports_failure(monkeypatch):
    result = SimpleNamespace(returncode=1, stdout=b'', stderr='Brak dostępu do pulpitu'.encode('utf-8'))
    monkeypatch.setattr(dialogs.subprocess, 'run', lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match='Brak dostępu do pulpitu'):
        dialogs.choose_local_files()

    result.returncode = 0
    result.stdout = b'not json'
    with pytest.raises(RuntimeError, match='nieprawidłowe dane'):
        dialogs.choose_local_files()
