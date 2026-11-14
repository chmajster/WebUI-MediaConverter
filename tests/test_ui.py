from __future__ import annotations

import asyncio
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
import socketio
from websockets.exceptions import InvalidStatus
from websockets.asyncio.client import connect as ws_connect


ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def running_server(tmp_path, chosen_files=None):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env['MEDIACONVERTER_DATA_DIR'] = str(tmp_path / 'app-data')
    log = (tmp_path / 'server.log').open('w+', encoding='utf-8')
    if chosen_files is None:
        command = [sys.executable, str(ROOT / 'main.py')]
    else:
        env['MEDIACONVERTER_TEST_CHOICES'] = json.dumps([str(path) for path in chosen_files])
        launcher = ("import json, os; import app.ui as app_ui; "
                    "app_ui.choose_local_files = lambda: json.loads(os.environ['MEDIACONVERTER_TEST_CHOICES']); "
                    "import main; main.main()")
        command = [sys.executable, '-c', launcher]
    proc = subprocess.Popen([*command, '--no-browser', '--port', str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=log,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if proc.poll() is not None:
                log.seek(0)
                pytest.fail(f'Serwer zakończył pracę: {log.read()}')
            try:
                urllib.request.urlopen(url, timeout=.3).close()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        else:
            pytest.fail('Serwer nie uruchomił się.')
        yield url
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)
        log.close()


@pytest.fixture
def server(tmp_path):
    with running_server(tmp_path) as url:
        yield url


@pytest.fixture
def picker_server(tmp_path, sample):
    second = tmp_path / 'drugi plik żółw.mp4'
    shutil.copyfile(sample, second)
    choices = [sample, second]
    with running_server(tmp_path, choices) as url:
        yield url, choices


def read_page(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode('utf-8'), response.headers


def elements_from_page(page):
    content = re.search(r'parseElements\(String.raw`(.*?)`\)', page, re.S)
    assert content
    return json.loads(html.unescape(content.group(1)))


def find_element(elements, label, event=None):
    for key, element in elements.items():
        if not isinstance(element, dict): continue
        if element.get('props', {}).get('label') != label: continue
        handlers = element.get('events', [])
        if event is None: return int(key), element
        for handler in handlers:
            if handler['type'] == event:
                return int(key), handler
    raise AssertionError(f'Nie znaleziono kontrolki: {label}, {event}')


def test_all_views_and_origin_protection(server):
    for page in ('dashboard', 'video', 'audio', 'extract', 'tools', 'batch', 'queue', 'history', 'settings'):
        content, headers = read_page(f'{server}/?page={page}')
        assert 'MediaConverter' in content and 'Server error' not in content
        assert headers['X-Frame-Options'] == 'DENY'
    request = urllib.request.Request(server, headers={'Origin': 'https://example.org'})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


@pytest.mark.skipif(os.name != 'nt', reason='Skrypt startowy jest przeznaczony dla Windows.')
def test_start_script_help(tmp_path):
    env = os.environ.copy()
    env['MEDIACONVERTER_DATA_DIR'] = str(tmp_path / 'app-data')
    result = subprocess.run(['cmd.exe', '/c', 'start.bat', '--help'], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert '--no-browser' in result.stdout and '--port' in result.stdout


@pytest.mark.asyncio
async def test_websocket_origin_and_actual_ui_conversion(server, sample, tmp_path):
    websocket = server.replace('http://', 'ws://') + '/_nicegui_ws/socket.io/?EIO=4&transport=websocket'
    with pytest.raises(InvalidStatus) as error:
        async with ws_connect(websocket, origin='https://example.org'):
            pass
    assert error.value.response.status_code == 403
    page, _ = await asyncio.to_thread(read_page, server + '/?page=video')
    elements = elements_from_page(page)
    client_id = re.search(r"'client_id': '([^']+)'", page).group(1)
    sio = socketio.AsyncClient(reconnection=False)
    updates = []
    @sio.on('update')
    async def on_update(data):
        updates.append(data)
        for key, value in data.items():
            if key == '_id': continue
            if value is None: elements.pop(str(key), None)
            else: elements[str(key)] = value
    await sio.connect(server + '?client_id=' + client_id, socketio_path='/_nicegui_ws/socket.io',
                      transports=['websocket'], headers={'Origin': server})
    try:
        ok = await sio.call('handshake', {'client_id': client_id, 'document_id': uuid.uuid4().hex,
                                        'tab_id': uuid.uuid4().hex, 'next_message_id': 0}, timeout=10)
        assert ok
        async def event(label, kind, value=None):
            element_id, handler = find_element(elements, label, kind)
            args = [] if kind == 'click' else [json.dumps(value)]
            await sio.emit('event', {'client_id': client_id, 'id': element_id,
                                     'listener_id': handler['listener_id'], 'args': args})
        async def wait_until(predicate, timeout=30):
            async with asyncio.timeout(timeout):
                while not predicate(): await asyncio.sleep(.05)
        await event('Kodek wideo', 'update:modelValue', {'value': 1, 'label': 'Kopiuj bez rekompresji'})
        await wait_until(lambda: find_element(elements, 'Szerokość (opcjonalnie)')[1].get('props', {}).get('disable') is True)
        await event('Kodek wideo', 'update:modelValue', {'value': 0, 'label': 'Automatycznie'})
        await wait_until(lambda: find_element(elements, 'Szerokość (opcjonalnie)')[1].get('props', {}).get('disable') is False)
        await event('Pełne ścieżki — każda w osobnym wierszu', 'update:value', f'\n  "{sample}"  \n\n')
        await event('Dodaj ścieżki', 'click')
        await wait_until(lambda: any(isinstance(v, dict) and v.get('text') == sample.name for v in elements.values()))
        upload_url = next(v['props']['url'] for v in elements.values() if isinstance(v, dict) and '/upload/' in v.get('props', {}).get('url', ''))
        async with httpx.AsyncClient() as client:
            with sample.open('rb') as source:
                response = await client.post(server + upload_url, files={'file': (sample.name, source, 'video/mp4')}, headers={'Origin': server})
        assert response.status_code == 200
        await wait_until(lambda: sum(isinstance(v, dict) and v.get('text') == sample.name for v in elements.values()) == 2)
        await event('Folder wynikowy', 'update:value', str(tmp_path))
        await event('Podgląd komendy', 'click')
        await wait_until(lambda: any(isinstance(v, dict) and v.get('text') == 'Podgląd polecenia FFmpeg' for v in elements.values()))
        await event('Zamknij', 'click')
        before_detail = set(elements)
        info_id, info_button = next((int(k), v) for k, v in elements.items()
                                    if isinstance(v, dict) and v.get('props', {}).get('icon') == 'info')
        info_handler = next(e for e in info_button['events'] if e['type'] == 'click')
        await sio.emit('event', {'client_id': client_id, 'id': info_id,
                                 'listener_id': info_handler['listener_id'], 'args': []})
        await wait_until(lambda: any(isinstance(v, dict) and v.get('props', {}).get('poster', '').startswith('/thumbnail/') for v in elements.values()))
        poster = next(v['props']['poster'] for v in elements.values()
                      if isinstance(v, dict) and v.get('props', {}).get('poster', '').startswith('/thumbnail/'))
        async with httpx.AsyncClient() as client:
            thumbnail = await client.get(server + poster)
        assert thumbnail.status_code == 200 and thumbnail.content.startswith(b'\xff\xd8')
        close_id, close_button = next((int(k), v) for k, v in elements.items() if k not in before_detail
                                      and isinstance(v, dict) and v.get('props', {}).get('icon') == 'close')
        close_handler = next(e for e in close_button['events'] if e['type'] == 'click')
        await sio.emit('event', {'client_id': client_id, 'id': close_id,
                                 'listener_id': close_handler['listener_id'], 'args': []})
        await event('Konwertuj', 'click')
        await wait_until(lambda: sum(isinstance(v, dict) and v.get('text') == 'Zakończono' for v in elements.values()) == 2)
        assert len(list(tmp_path.glob('*_konwersja*.mp4'))) == 2

        before_job_dialog = set(elements)
        await event('Szczegóły i log', 'click')
        await wait_until(lambda: any(k not in before_job_dialog and isinstance(v, dict) and v.get('text') == 'Szczegóły zadania'
                                     for k, v in elements.items()), timeout=5)
        job_dialog_id, job_dialog = next((k, v) for k, v in elements.items() if k not in before_job_dialog
                                         and isinstance(v, dict) and v.get('tag') == 'nicegui-dialog')
        assert job_dialog['props']['model-value'] is True
        await asyncio.sleep(2.2)
        assert job_dialog_id in elements and elements[job_dialog_id]['props']['model-value'] is True

        # A dismiss event models closing the dialog by Escape or clicking outside it.
        change = next(e for e in job_dialog['events'] if e['type'] == 'update:modelValue')
        await sio.emit('event', {'client_id': client_id, 'id': int(job_dialog_id),
                                 'listener_id': change['listener_id'], 'args': [json.dumps(False)]})
        await wait_until(lambda: job_dialog_id not in elements, timeout=5)

        before_clear_dialog = set(elements)
        await event('Wyczyść zakończone', 'click')
        await wait_until(lambda: any(k not in before_clear_dialog and isinstance(v, dict)
                                     and v.get('text') == 'Wyczyścić zakończone zadania?'
                                     for k, v in elements.items()), timeout=5)
        clear_dialog_id, clear_dialog = next((k, v) for k, v in elements.items() if k not in before_clear_dialog
                                             and isinstance(v, dict) and v.get('tag') == 'nicegui-dialog')
        assert clear_dialog['props']['model-value'] is True
        old_job_button_ids = {k for k, v in elements.items() if isinstance(v, dict)
                              and v.get('props', {}).get('label') == 'Szczegóły i log'}
        await event('Anuluj', 'click')
        await wait_until(lambda: elements[clear_dialog_id]['props']['model-value'] is False, timeout=5)
        await wait_until(lambda: any(k not in old_job_button_ids and isinstance(v, dict)
                                     and v.get('props', {}).get('label') == 'Szczegóły i log'
                                     for k, v in elements.items()), timeout=5)

        await event('Wyczyść zakończone', 'click')
        await wait_until(lambda: sum(isinstance(v, dict) and v.get('tag') == 'nicegui-dialog'
                                     and v.get('props', {}).get('model-value') is True
                                     for v in elements.values()) == 1, timeout=5)
        await event('Wyczyść', 'click')
        await wait_until(lambda: any(isinstance(v, dict) and v.get('text') == 'Brak zadań do wyświetlenia.'
                                     for v in elements.values()), timeout=5)
        assert len(list(tmp_path.glob('*_konwersja*.mp4'))) == 2
    finally:
        await sio.disconnect()


@pytest.mark.asyncio
async def test_empty_path_button_selects_original_files(picker_server, tmp_path):
    server_url, choices = picker_server
    page, _ = await asyncio.to_thread(read_page, server_url + '/?page=video')
    elements = elements_from_page(page)
    client_id = re.search(r"'client_id': '([^']+)'", page).group(1)
    sio = socketio.AsyncClient(reconnection=False)
    notifications = []

    @sio.on('update')
    async def on_update(data):
        for key, value in data.items():
            if key == '_id': continue
            if value is None: elements.pop(str(key), None)
            else: elements[str(key)] = value

    @sio.on('notify')
    async def on_notify(data):
        notifications.append(data)

    await sio.connect(server_url + '?client_id=' + client_id, socketio_path='/_nicegui_ws/socket.io',
                      transports=['websocket'], headers={'Origin': server_url})
    try:
        ok = await sio.call('handshake', {'client_id': client_id, 'document_id': uuid.uuid4().hex,
                                        'tab_id': uuid.uuid4().hex, 'next_message_id': 0}, timeout=10)
        assert ok

        async def click_paths():
            element_id, handler = find_element(elements, 'Dodaj ścieżki', 'click')
            await sio.emit('event', {'client_id': client_id, 'id': element_id,
                                     'listener_id': handler['listener_id'], 'args': []})

        async def wait_until(predicate):
            async with asyncio.timeout(15):
                while not predicate(): await asyncio.sleep(.05)

        await click_paths()
        await wait_until(lambda: all(any(isinstance(v, dict) and v.get('text') == path.name
                                             for v in elements.values()) for path in choices))
        await wait_until(lambda: not find_element(elements, 'Dodaj ścieżki')[1].get('props', {}).get('disable'))
        assert not list((tmp_path / 'app-data' / 'uploads').iterdir())

        await click_paths()
        await wait_until(lambda: any('Pominięto pliki' in notification.get('message', '')
                                     for notification in notifications))
        for path in choices:
            assert sum(isinstance(v, dict) and v.get('text') == path.name for v in elements.values()) == 1
    finally:
        await sio.disconnect()


@pytest.mark.asyncio
async def test_picker_cancel_and_error_are_handled(monkeypatch):
    import app.ui as app_ui

    interface = app_ui.Interface.__new__(app_ui.Interface)
    interface.path_picker_open = False
    interface.add_paths = AsyncMock()
    notifications = []
    monkeypatch.setattr(app_ui.ui, 'notify', lambda message, **kwargs: notifications.append((message, kwargs)))
    monkeypatch.setattr(app_ui, 'choose_local_files', lambda: [])
    await interface.add_local_paths('')
    interface.add_paths.assert_not_awaited()
    assert not notifications and not interface.path_picker_open

    def fail_picker():
        raise RuntimeError('brak okna')
    monkeypatch.setattr(app_ui, 'choose_local_files', fail_picker)
    await interface.add_local_paths('  \n')
    assert 'Nie można otworzyć okna wyboru plików' in notifications[-1][0]
    assert notifications[-1][1]['type'] == 'negative' and not interface.path_picker_open


@pytest.mark.asyncio
async def test_same_path_is_not_added_twice_during_probe(tmp_path, monkeypatch):
    import app.ui as app_ui

    source = tmp_path / 'plik.mp4'
    source.touch()
    started, release = threading.Event(), threading.Event()

    def slow_probe(path, ffprobe):
        started.set()
        if not release.wait(timeout=10): raise TimeoutError('Odczyt pliku nie został zwolniony.')
        return {'streams': [], 'size': 0}

    monkeypatch.setattr(app_ui, 'probe', slow_probe)
    monkeypatch.setattr(app_ui.ui, 'notify', lambda *args, **kwargs: None)
    interface = app_ui.Interface.__new__(app_ui.Interface)
    interface.files = []
    interface.pending_paths = set()
    interface.manager = SimpleNamespace(ffprobe='ffprobe', ffmpeg=None)
    interface.page = 'dashboard'
    first = asyncio.create_task(interface.add_paths([str(source)]))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        second = asyncio.create_task(interface.add_paths([str(source)]))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        assert [item['path'] for item in interface.files] == [str(source.resolve())]
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        for item in interface.files:
            app_ui.PREVIEWS.pop(item['token'], None)
