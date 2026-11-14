from __future__ import annotations

import argparse
import asyncio
import atexit
import os
import socket
from pathlib import Path

# The application never exports telemetry, even if the parent shell configures OpenTelemetry.
os.environ['FASTAPI_OTEL_AUTO_CONFIGURE'] = 'false'
os.environ['OTEL_SDK_DISABLED'] = 'true'

from nicegui import app, ui

from app.jobs import JobManager
from app.media import detect_hardware, encoder_names, locate_binary
from app.store import Store
from app.single_instance import SingleInstance
from app.ui import Interface


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('MEDIACONVERTER_DATA_DIR', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
try:
    instance = SingleInstance(DATA / '.instance.lock')
except RuntimeError as exc:
    raise SystemExit(str(exc)) from exc
atexit.register(instance.close)
store = Store(DATA / 'mediaconverter.sqlite3')
settings = store.settings()
ffmpeg = locate_binary(settings.ffmpeg, 'ffmpeg')
ffprobe = locate_binary(settings.ffprobe, 'ffprobe')
manager = JobManager(store, DATA, ffmpeg, ffprobe, encoder_names(ffmpeg), {})
PORT = 0


class LocalOriginMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get('headers', [])}
        expected = f'127.0.0.1:{PORT}'.encode()
        origin = headers.get(b'origin')
        if headers.get(b'host') != expected or (origin and origin != b'http://' + expected):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await send({'type': 'http.response.start', 'status': 403, 'headers': [(b'content-type', b'text/plain; charset=utf-8')]})
                await send({'type': 'http.response.body', 'body': 'Niedozwolone pochodzenie.'.encode()})
            return
        async def secured_send(message):
            if message['type'] == 'http.response.start':
                message.setdefault('headers', []).extend([(b'x-frame-options', b'DENY'),
                                                         (b'content-security-policy', b"frame-ancestors 'none'")])
            await send(message)
        await self.app(scope, receive, secured_send)


app.add_middleware(LocalOriginMiddleware)


@ui.page('/')
def index(page: str = 'dashboard'):
    interface = Interface(manager, DATA)
    if page != 'dashboard':
        interface.navigate(page)


async def startup():
    manager.start()
    if manager.ffmpeg:
        async def check_gpu():
            manager.hardware = await asyncio.to_thread(detect_hardware, manager.ffmpeg, manager.encoders)
        asyncio.create_task(check_gpu())


app.on_startup(startup)
app.on_shutdown(manager.shutdown)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    global PORT
    parser = argparse.ArgumentParser(description='MediaConverter GUI — lokalny panel FFmpeg')
    parser.add_argument('--port', type=int, default=0, help='Port lokalny (0 = automatyczny)')
    parser.add_argument('--no-browser', action='store_true', help='Nie otwieraj przeglądarki automatycznie')
    args = parser.parse_args()
    PORT = args.port or free_port()
    if not (1 <= PORT <= 65535): parser.error('Port musi być liczbą z zakresu 1–65535.')
    print(f'MediaConverter GUI: http://127.0.0.1:{PORT}', flush=True)
    if not ffmpeg or not ffprobe:
        print('Nie znaleziono FFmpeg lub FFprobe. Skonfiguruj ścieżki w ustawieniach aplikacji.', flush=True)
    ui.run(host='127.0.0.1', port=PORT, title='MediaConverter GUI', language='pl-PL',
           show=not args.no_browser, reload=False, favicon='🎬', show_welcome_message=False)


if __name__ == '__main__':
    main()
