from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import psutil
from nicegui import app, ui
from fastapi.responses import FileResponse

from .dialogs import choose_local_files
from .engine import display_command, select_video_encoder
from .files import local_file, output_directory, safe_stem
from .jobs import JobManager
from .media import NO_WINDOW, duration, encoder_capabilities, encoder_names, detect_hardware, gpu_load, locate_binary, probe, streams
from .models import AUDIO_ENCODERS, AUDIO_FORMATS, LOSSLESS_AUDIO, VIDEO_AUDIO, VIDEO_FORMATS, AppSettings, ConversionSpec
from .presets import AUDIO_PRESETS, VIDEO_PRESETS


PAGES = [
    ('dashboard', 'Dashboard', 'grid_view'), ('video', 'Konwerter wideo', 'movie'),
    ('audio', 'Konwerter audio', 'headphones'), ('extract', 'Wideo na audio', 'graphic_eq'),
    ('tools', 'Narzędzia multimedialne', 'auto_fix_high'), ('batch', 'Konwersja wsadowa', 'collections'),
    ('queue', 'Kolejka zadań', 'playlist_play'), ('history', 'Historia', 'history'),
    ('settings', 'Ustawienia', 'settings'),
]
STATUS = {'waiting': 'Oczekuje', 'running': 'Przetwarzanie', 'completed': 'Zakończono',
          'failed': 'Błąd', 'cancelled': 'Anulowano', 'interrupted': 'Przerwano'}
NAMES = {key: label for key, label, _ in PAGES}
PREVIEWS: dict[str, str] = {}
THUMBNAILS: dict[str, tuple[str, Path]] = {}
THUMBNAIL_LOCK = asyncio.Lock()


@app.get('/preview/{token}')
def preview_file(token: str):
    path = PREVIEWS.get(token)
    if not path or not Path(path).is_file():
        from fastapi import HTTPException
        raise HTTPException(404)
    return FileResponse(path)


@app.get('/thumbnail/{token}')
async def preview_thumbnail(token: str):
    from fastapi import HTTPException
    path = PREVIEWS.get(token)
    info = THUMBNAILS.get(token)
    if not path or not info or not Path(path).is_file():
        raise HTTPException(404)
    ffmpeg, target = info
    async with THUMBNAIL_LOCK:
        if not target.is_file():
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-protocol_whitelist', 'file,pipe,crypto,data', '-i', path,
                '-map', '0:v:0', '-frames:v', '1', '-vf', 'scale=480:-2', '-q:v', '4', str(target),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=NO_WINDOW)
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if proc.returncode is None:
                    try: proc.kill()
                    except ProcessLookupError: pass
                await proc.wait()
                target.unlink(missing_ok=True)
                raise
            if proc.returncode or not target.is_file():
                target.unlink(missing_ok=True)
                raise HTTPException(404, 'Nie można przygotować miniatury.')
    return FileResponse(target, media_type='image/jpeg')


def bytes_text(size: int | float | None) -> str:
    if size is None: return '—'
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB': return f'{value:.1f} {unit}' if unit != 'B' else f'{int(value)} B'
        value /= 1024
    return '—'


def time_text(seconds: int | float | None) -> str:
    if seconds is None: return '—'
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02}:{m:02}:{s:02}' if h else f'{m:02}:{s:02}'


def notify_error(exc: Exception):
    ui.notify(str(exc), type='negative', timeout=8000, multi_line=True)


class Interface:
    def __init__(self, manager: JobManager, data_dir: Path):
        self.manager, self.data_dir = manager, data_dir
        self.page = 'dashboard'
        self.files: list[dict] = []
        self.controls: dict = {}
        self.operation = 'gif'
        self.batch_operation = 'video'
        self.timer = None
        self.dialog_open = False
        self.updating_options = False
        self.path_picker_open = False
        self.pending_paths: set[str] = set()
        self.dark = ui.dark_mode(value=manager.settings.theme == 'dark')
        self.upload_dir = data_dir / 'uploads'
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        ui.add_head_html('<style>' + (Path(__file__).parent.parent / 'assets' / 'app.css').read_text(encoding='utf-8') + '</style>')
        ui.colors(primary='#8b6ff0', positive='#69d9af', negative='#e88191')
        with ui.row().classes('shell no-wrap'):
            with ui.column().classes('sidebar'):
                with ui.row().classes('brand no-wrap'):
                    with ui.element('div').classes('brand-mark'): ui.icon('play_arrow')
                    with ui.column().classes('gap-0'):
                        ui.label('MediaConverter').classes('brand-name')
                        ui.label('STUDIO LOKALNE').classes('brand-small')
                ui.label('PRZESTRZEŃ ROBOCZA').classes('nav-caption')
                self.nav_buttons = {}
                for key, label, icon in PAGES:
                    btn = ui.button(label, icon=icon, on_click=lambda k=key: self.navigate(k)).props('flat no-caps').classes('nav-button')
                    self.nav_buttons[key] = btn
                with ui.column().classes('sidebar-bottom gap-2 full'):
                    with ui.row().classes('items-center gap-2'):
                        ui.element('span').classes('status-dot')
                        ui.label('Praca lokalna').classes('bold small')
                    ui.label('Twoje pliki zostają na tym komputerze.').classes('muted small')
            with ui.column().classes('main'):
                with ui.row().classes('topbar full'):
                    self.crumb = ui.html('').classes('crumb')
                    self.mobile = ui.select(NAMES, value='dashboard', on_change=lambda e: self.navigate(e.value) if e.value != self.page else None).props('dense outlined').classes('mobile-nav')
                    with ui.row().classes('top-right'):
                        with ui.element('div').classes('pill'):
                            ui.element('span').classes('status-dot')
                            ui.label('TRYB LOKALNY')
                        ui.button(icon='dark_mode' if manager.settings.theme == 'dark' else 'light_mode', on_click=self.toggle_theme).props('flat round dense').classes('muted')
                self.content = ui.column().classes('full gap-0')
        self.navigate('dashboard')

    def toggle_theme(self):
        self.dark.toggle()
        settings = self.manager.settings
        settings.theme = 'light' if settings.theme == 'dark' else 'dark'
        self.manager.store.save_settings(settings)

    def navigate(self, page: str):
        if page not in NAMES: return
        self.page = page
        if self.timer:
            self.timer.cancel()
            self.timer = None
        for key, button in self.nav_buttons.items():
            button.classes(add='selected' if key == page else '', remove='' if key == page else 'selected')
        self.crumb.content = f'PRZESTRZEŃ ROBOCZA &nbsp; / &nbsp; <strong>{NAMES[page]}</strong>'
        if self.mobile.value != page: self.mobile.set_value(page)
        self.content.clear()
        with self.content:
            if page == 'dashboard': self.dashboard()
            elif page in ('video', 'audio', 'extract', 'tools', 'batch'): self.editor()
            elif page in ('queue', 'history'): self.queue(page == 'history')
            else: self.settings_page()

    def header(self, eyebrow: str, title: str, subtitle: str):
        with ui.row().classes('page-head full'):
            with ui.column().classes('gap-0'):
                ui.label(eyebrow).classes('eyebrow')
                ui.label(title).classes('page-title')
                ui.label(subtitle).classes('page-subtitle')
            return ui.row().classes('items-center')

    def stat(self, label: str, value: str, note: str, icon: str):
        with ui.column().classes('stat-card gap-0'):
            with ui.element('div').classes('stat-top full'):
                ui.label(label)
                with ui.element('div').classes('stat-icon'): ui.icon(icon, size='18px')
            ui.label(value).classes('stat-value')
            ui.label(note).classes('stat-note')

    def dashboard(self):
        with self.header('CENTRUM DOWODZENIA', 'Witaj w MediaConverter', 'Wszystkie Twoje multimedia, jedno wygodne miejsce.'):
            ui.button('Nowa konwersja', icon='add', on_click=lambda: self.navigate('video')).classes('primary-btn')
        @ui.refreshable
        def overview():
            jobs = list(self.manager.items.values())
            with ui.element('div').classes('stats full'):
                self.stat('Wszystkie zadania', str(len(jobs)), 'Zapisane w historii', 'layers')
                self.stat('W trakcie', str(sum(j['status'] == 'running' for j in jobs)), 'Aktywne konwersje', 'sync')
                self.stat('W kolejce', str(sum(j['status'] == 'waiting' for j in jobs)), 'Oczekujące zadania', 'schedule')
                self.stat('Zakończone', str(sum(j['status'] == 'completed' for j in jobs)), 'Gotowe do pobrania', 'task_alt')
            with ui.element('div').classes('dashboard-grid full'):
                with ui.column().classes('panel welcome gap-0'):
                    ui.label('Wszystko, czego potrzebują Twoje multimedia.').classes('welcome-title')
                    ui.label('Konwertuj wideo i dźwięk bez wysyłania plików do chmury. Zacznij od wybrania plików.').classes('welcome-copy')
                    ui.button('Rozpocznij konwersję', icon='arrow_forward', on_click=lambda: self.navigate('video')).classes('primary-btn self-start')
                    ui.icon('movie_filter').classes('welcome-icon')
                with ui.column().classes('panel gap-0'):
                    ui.label('Stan systemu').classes('panel-title')
                    ui.label('Zasoby dostępne na tym urządzeniu').classes('panel-subtitle')
                    with ui.element('div').classes('health-row full'):
                        ui.label('Wykorzystanie CPU'); ui.label(f'{psutil.cpu_percent():.0f}%').classes('bold')
                    ui.linear_progress(value=psutil.cpu_percent() / 100, color='primary').props('rounded size=6px').classes('full')
                    with ui.element('div').classes('health-row full'):
                        ui.label('Wykorzystanie GPU'); ui.label(gpu_load() or 'Niedostępne').classes('bold')
                    tested = sum(sum(codecs.values()) for codecs in self.manager.hardware.values())
                    ui.label(f'Przetestowane enkodery GPU: {tested}' if self.manager.hardware else 'Trwa sprawdzanie enkoderów GPU…').classes('small muted')
                    ui.separator().classes('q-my-md')
                    ui.label('FFmpeg gotowy do pracy' if self.manager.ffmpeg and self.manager.ffprobe else 'Skonfiguruj FFmpeg i FFprobe w ustawieniach').classes('small')
            with ui.column().classes('panel full gap-0'):
                with ui.element('div').classes('panel-head full'):
                    with ui.column().classes('gap-0'):
                        ui.label('Ostatnie operacje').classes('panel-title')
                        ui.label('Twoja aktywność w jednym miejscu').classes('panel-subtitle')
                    ui.button('Zobacz historię', on_click=lambda: self.navigate('history')).props('flat dense').classes('text-btn')
                recent = sorted(jobs, key=lambda j: j['created'], reverse=True)[:4]
                if not recent:
                    ui.label('Nie ma jeszcze żadnych zadań. Twoje konwersje pojawią się tutaj.').classes('muted small q-py-lg')
                for job in recent: self.activity(job)
        overview()
        self.timer = ui.timer(3, overview.refresh, immediate=False)

    def activity(self, job: dict):
        with ui.element('div').classes('activity-item full'):
            with ui.element('div').classes('file-icon'): ui.icon('description', size='20px')
            with ui.element('div').classes('activity-main'):
                ui.label(job['source']).classes('file-name')
                ui.label(f"{job['input_format'].upper() or 'PLIK'}  →  {job['output_format'].upper()}  ·  {job.get('created', '')[:10]}").classes('activity-meta')
            ui.label(STATUS.get(job['status'], job['status'])).classes(f"badge {job['status']}")

    async def add_paths(self, paths: list[str]):
        count = 0
        skipped = 0
        for raw in paths:
            if not raw.strip(): continue
            try:
                path = str(local_file(raw))
                key = os.path.normcase(path)
                if key in self.pending_paths or any(os.path.normcase(item['path']) == key for item in self.files):
                    skipped += 1
                    continue
                self.pending_paths.add(key)
                try:
                    info = await asyncio.to_thread(probe, path, self.manager.ffprobe)
                finally:
                    self.pending_paths.discard(key)
                token = uuid.uuid4().hex
                PREVIEWS[token] = path
                if self.manager.ffmpeg and streams(info, 'video'):
                    THUMBNAILS[token] = (self.manager.ffmpeg, self.manager.temp_dir / f'preview-{token}.jpg')
                self.files.append({'path': path, 'info': info, 'token': token})
                count += 1
            except Exception as exc:
                notify_error(exc)
        if count: ui.notify(f'Dodano pliki: {count}', type='positive')
        if skipped: ui.notify(f'Pominięto pliki już dodane lub dodawane: {skipped}', type='info')
        if self.page in ('video', 'audio', 'extract', 'batch', 'tools'): self.files_view.refresh()

    async def add_local_paths(self, value: str):
        paths = [line for line in value.splitlines() if line.strip()]
        if not paths:
            if self.path_picker_open:
                ui.notify('Okno wyboru plików jest już otwarte.', type='info')
                return
            self.path_picker_open = True
            try:
                paths = await asyncio.to_thread(choose_local_files)
            except Exception as exc:
                notify_error(ValueError(f'Nie można otworzyć okna wyboru plików: {exc}. Wpisz ścieżki ręcznie.'))
                return
            finally:
                self.path_picker_open = False
        if paths:
            await self.add_paths(paths)

    async def upload(self, event):
        folder = self.upload_dir / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        original = Path(event.name.replace('\\', '/')).name
        suffix = Path(original).suffix[:16].lower()
        destination = folder / (safe_stem(original) + suffix)
        try:
            def copy_upload():
                with destination.open('xb') as dst:
                    shutil.copyfileobj(event.content, dst, length=1024 * 1024)
            await asyncio.to_thread(copy_upload)
            await self.add_paths([str(destination)])
        except Exception as exc:
            shutil.rmtree(folder, ignore_errors=True)
            notify_error(exc)

    def remove_file(self, item: dict):
        if item in self.files:
            self.files.remove(item)
            PREVIEWS.pop(item['token'], None)
            thumbnail = THUMBNAILS.pop(item['token'], None)
            if thumbnail:
                try: thumbnail[1].unlink(missing_ok=True)
                except OSError: pass
        self.files_view.refresh()

    def file_detail(self, item: dict):
        info = item['info']; video = streams(info, 'video'); audio = streams(info, 'audio')
        with ui.dialog() as dialog, ui.card().classes('panel').style('width: 750px; max-width: 95vw'):
            with ui.row().classes('full items-center justify-between'):
                ui.label(Path(item['path']).name).classes('panel-title')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            if video:
                with ui.element('div').classes('preview-box full'):
                    ui.video(f"/preview/{item['token']}").props(f"poster=/thumbnail/{item['token']}").classes('full')
            elif audio:
                with ui.element('div').classes('preview-box full'):
                    ui.audio(f"/preview/{item['token']}").classes('full')
            ui.label('Odtwarzanie zależy od kodeków obsługiwanych przez przeglądarkę; nie wpływa na konwersję.').classes('muted small')
            fmt = info.get('format', {}); v = video[0] if video else {}; a = audio[0] if audio else {}
            details = {'Rozmiar': bytes_text(info['size']), 'Kontener': fmt.get('format_name', '—'),
                       'Czas trwania': time_text(duration(info)), 'Bitrate': str(fmt.get('bit_rate', '—')) + ' b/s',
                       'Kodek wideo': v.get('codec_name', '—'), 'Rozdzielczość': f"{v.get('width', '—')} × {v.get('height', '—')}",
                       'FPS': v.get('avg_frame_rate', '—'), 'Kodek audio': a.get('codec_name', '—'),
                       'Kanały': str(a.get('channels', '—')), 'Próbkowanie': str(a.get('sample_rate', '—')) + ' Hz',
                       'Ścieżka': item['path']}
            with ui.element('div').classes('meta-grid full'):
                for key, value in details.items():
                    with ui.column().classes('gap-0'):
                        ui.label(key).classes('muted small'); ui.label(value).classes('meta-value')
            ui.label('Dostępne ścieżki').classes('bold small q-mt-md')
            for stream in info.get('streams', []):
                if stream.get('codec_type') in ('video', 'audio', 'subtitle'):
                    tag = stream.get('tags') or {}
                    ui.label(f"#{stream.get('index')} · {stream.get('codec_type')} · {stream.get('codec_name', '—')} · {tag.get('language', '—')}").classes('small muted')
            ui.button('Zamknij', on_click=dialog.close).classes('quiet-btn self-end')
        dialog.open()

    def editor(self):
        page = self.page
        title = NAMES[page]
        subtitle = {'video': 'Dopasuj format i jakość filmu do swoich potrzeb.', 'audio': 'Konwertuj dźwięk z zachowaniem pełnej kontroli nad jakością.',
                    'extract': 'Wyodrębnij dźwięk z filmu do wybranego formatu.',
                    'tools': 'Kadry, miniatury, GIF-y i łączenie plików w jednym miejscu.',
                    'batch': 'Dodaj wiele plików i przetwórz je według wspólnych ustawień.'}[page]
        self.header('NOWE ZADANIE', title, subtitle)
        with ui.element('div').classes('editor-grid full'):
            with ui.element('div').classes('stack'):
                with ui.column().classes('panel full gap-0'):
                    ui.label('1. Dodaj pliki').classes('panel-title')
                    ui.label('Przeciągnij pliki lub wybierz je z urządzenia. Możesz dodać wiele plików.').classes('panel-subtitle q-mb-md')
                    with ui.element('div').classes('upload-wrap full'):
                        ui.upload(label='Przeciągnij pliki tutaj lub kliknij +', multiple=True, auto_upload=True, on_upload=self.upload).props('flat bordered').classes('full')
                    with ui.expansion('Dodaj pliki według ścieżek lokalnych', icon='folder_open').classes('full'):
                        paths = ui.textarea('Pełne ścieżki — każda w osobnym wierszu', placeholder='D:\\Filmy\\przyklad.mp4').props('outlined dense').classes('full')
                        ui.label('Zostaw pole puste, aby wybrać pliki w oknie systemowym.').classes('muted small q-mt-xs')
                        async def add_from_field():
                            path_button.disable()
                            try:
                                await self.add_local_paths(paths.value or '')
                            finally:
                                if not path_button.is_deleted: path_button.enable()
                        path_button = ui.button('Dodaj ścieżki', icon='add', on_click=add_from_field).classes('quiet-btn q-mt-sm')
                with ui.column().classes('panel full gap-0'):
                    with ui.element('div').classes('panel-head full'):
                        with ui.column().classes('gap-0'):
                            ui.label('Wybrane pliki').classes('panel-title')
                            ui.label('Kliknij plik, aby zobaczyć podgląd i szczegóły.').classes('panel-subtitle')
                        ui.button('Wyczyść', on_click=self.clear_files).props('flat dense').classes('text-btn')
                    @ui.refreshable
                    def files_view():
                        if not self.files:
                            ui.label('Lista jest jeszcze pusta.').classes('muted small q-py-md')
                        for item in self.files:
                            info = item['info']
                            with ui.element('div').classes('activity-item full'):
                                with ui.element('div').classes('file-icon'): ui.icon('movie' if streams(info, 'video') else 'audiotrack')
                                with ui.element('div').classes('activity-main'):
                                    ui.label(Path(item['path']).name).classes('file-name')
                                    ui.label(f"{bytes_text(info['size'])} · {time_text(duration(info))}").classes('activity-meta')
                                ui.button(icon='info', on_click=lambda i=item: self.file_detail(i)).props('flat round dense').tooltip('Podgląd i informacje')
                                ui.button(icon='close', on_click=lambda i=item: self.remove_file(i)).props('flat round dense').tooltip('Usuń z listy')
                    self.files_view = files_view
                    files_view()
            with ui.element('div').classes('stack'):
                self.editor_settings(page)

    def clear_files(self):
        for item in self.files:
            PREVIEWS.pop(item['token'], None)
            thumbnail = THUMBNAILS.pop(item['token'], None)
            if thumbnail:
                try: thumbnail[1].unlink(missing_ok=True)
                except OSError: pass
        self.files.clear()
        self.files_view.refresh()

    def editor_settings(self, page: str):
        self.controls = {}
        c = self.controls
        with ui.column().classes('panel full gap-0'):
            ui.label('2. Ustawienia konwersji').classes('panel-title')
            ui.label('Wybierz format wyjściowy i profil jakości.').classes('panel-subtitle q-mb-md')
            if page == 'tools':
                choices = {'gif': 'Wideo → GIF', 'merge_video': 'Połącz wideo', 'merge_audio': 'Połącz audio',
                           'frames': 'Wyodrębnij klatki (ZIP)', 'thumbnail': 'Utwórz miniaturę'}
                def switch_tool(e):
                    self.operation = e.value
                    self.navigate('tools')
                ui.select(choices, value=self.operation, label='Operacja', on_change=switch_tool).props('outlined dense').classes('full q-mb-md')
            if page == 'batch':
                def switch_batch(e):
                    self.batch_operation = e.value
                    self.navigate('batch')
                ui.select({'video': 'Wideo → wideo', 'audio': 'Audio → audio', 'extract': 'Wideo → audio'}, value=self.batch_operation,
                          label='Rodzaj konwersji', on_change=switch_batch).props('outlined dense').classes('full q-mb-md')
            op = self.current_operation()
            is_video = op in ('video', 'merge_video')
            is_audio = op in ('audio', 'extract', 'merge_audio')
            formats = VIDEO_FORMATS if is_video else AUDIO_FORMATS
            if is_video or is_audio:
                c['format'] = ui.select({k: k.upper() for k in formats}, value='mp4' if is_video else 'mp3', label='Format wynikowy', on_change=lambda: self.update_codec_options()).props('outlined dense').classes('full')
            else:
                c['format'] = None
                ui.label({'gif': 'Wynik: GIF', 'frames': 'Wynik: archiwum JPG', 'thumbnail': 'Wynik: obraz JPG'}[op]).classes('muted small q-mb-md')
            presets = VIDEO_PRESETS if is_video else AUDIO_PRESETS if is_audio else {}
            custom = self.manager.store.presets()
            options = {f'b:{name}': name for name in presets}
            options.update({f'u:{name}': f'{name} · własny' for name, value in custom.items() if value.get('_kind') == ('video' if is_video else 'audio')})
            if options:
                profile = ui.select(options, label='Profil jakości', on_change=lambda e: self.apply_preset(e.value, presets)).props('outlined dense clearable').classes('full q-mt-md')
                with ui.row().classes('full gap-2 q-mt-sm'):
                    name = ui.input(placeholder='Nazwa własnego profilu').props('outlined dense').classes('col')
                    ui.button(icon='save', on_click=lambda: self.save_preset(name.value, is_video)).props('flat round dense').tooltip('Zapisz profil')
                    ui.button(icon='delete_outline', on_click=lambda: self.delete_preset(profile.value)).props('flat round dense').tooltip('Usuń własny profil')
            default_dir = self.manager.settings.output_dir or str(Path.home() / 'Videos')
            if not Path(default_dir).is_dir(): default_dir = str(Path.home())
            c['output_dir'] = ui.input('Folder wynikowy', value=default_dir).props('outlined dense').classes('full q-mt-md')
            with ui.expansion('Ustawienia zaawansowane', icon='tune').classes('full'):
                with ui.element('div').classes('form-grid full'):
                    if is_video:
                        c['video_codec'] = ui.select({'auto': 'Automatycznie'}, value='auto', label='Kodek wideo', on_change=lambda: self.update_advanced()).props('outlined dense').classes('full')
                        c['audio_codec'] = ui.select({'auto': 'Automatycznie'}, value='auto', label='Kodek audio', on_change=lambda: self.update_advanced()).props('outlined dense').classes('full')
                        c['device'] = ui.select({'auto': 'Automatycznie', 'cpu': 'CPU', 'nvidia': 'NVIDIA', 'intel': 'Intel', 'amd': 'AMD'}, value='auto', label='Kodowanie', on_change=lambda: self.update_advanced()).props('outlined dense').classes('full')
                        c['preset'] = ui.select({x:x for x in ('ultrafast','fast','medium','slow','veryslow')}, value='medium', label='Preset CPU').props('outlined dense').classes('full')
                        c['crf'] = ui.number('Jakość CRF / CQ (mniej = lepiej)', value=23, min=0, max=51, step=1).props('outlined dense').classes('full')
                        c['video_bitrate'] = ui.number('Bitrate wideo kb/s (opcjonalnie)', min=50, max=200000, step=100).props('outlined dense').classes('full')
                        c['pixel_format'] = ui.select({x:x for x in ('auto','yuv420p','yuv422p','yuv444p','yuv420p10le')}, value='auto', label='Format pikseli').props('outlined dense').classes('full')
                        c['threads'] = ui.number('Wątki (0 = automatycznie)', value=0, min=0, max=64, step=1).props('outlined dense').classes('full')
                    elif is_audio:
                        c['audio_codec'] = ui.select({'auto':'Dopasuj do formatu', 'copy':'Kopiuj bez rekompresji'}, value='auto', label='Kodek audio', on_change=lambda: self.update_advanced()).props('outlined dense').classes('full')
                    if is_video or is_audio:
                        c['audio_bitrate'] = ui.number('Bitrate audio kb/s', value=192, min=16, max=1536, step=1).props('outlined dense').classes('full')
                        c['sample_rate'] = ui.select({0:'Oryginalne', 8000:'8000 Hz', 12000:'12000 Hz', 16000:'16000 Hz', 22050:'22050 Hz', 24000:'24000 Hz', 32000:'32000 Hz', 44100:'44100 Hz', 48000:'48000 Hz', 96000:'96000 Hz'}, value=0, label='Częstotliwość próbkowania').props('outlined dense').classes('full')
                        c['channels'] = ui.select({0:'Oryginalne',1:'Mono',2:'Stereo'}, value=0, label='Kanały').props('outlined dense').classes('full')
                        c['volume_db'] = ui.number('Głośność (dB)', value=0, min=-60, max=20, step=1).props('outlined dense').classes('full')
                    if is_video or op in ('gif', 'frames', 'thumbnail'):
                        c['width'] = ui.number('Szerokość (opcjonalnie)', min=16, max=7680, step=2).props('outlined dense').classes('full')
                        c['height'] = ui.number('Wysokość (opcjonalnie)', min=16, max=4320, step=2).props('outlined dense').classes('full')
                        c['fps'] = ui.number('FPS (opcjonalnie)', min=0.1, max=240, step=1).props('outlined dense').classes('full')
                        c['rotate'] = ui.select({0:'Bez obrotu',90:'90° w prawo',180:'180°',270:'90° w lewo'}, value=0, label='Obrót').props('outlined dense').classes('full')
                        c['aspect'] = ui.select({'auto':'Oryginalne','16:9':'16:9','4:3':'4:3','1:1':'1:1','9:16':'9:16'}, value='auto', label='Proporcje').props('outlined dense').classes('full')
                    if op not in ('merge_video', 'merge_audio'):
                        c['start'] = ui.number('Początek fragmentu (sekundy)', value=0, min=0, step=0.1).props('outlined dense').classes('full')
                        c['end'] = ui.number('Koniec fragmentu (sekundy)', min=0.1, step=0.1).props('outlined dense').classes('full')
                    c['speed'] = ui.number('Prędkość odtwarzania', value=1, min=0.25, max=4, step=0.25).props('outlined dense').classes('full')
                    if is_video or is_audio:
                        c['audio_stream'] = ui.number('Indeks ścieżki audio (opcjonalnie)', min=0, step=1).props('outlined dense').classes('full')
                    if is_video:
                        c['subtitle_stream'] = ui.number('Indeks ścieżki napisów (opcjonalnie)', min=0, step=1).props('outlined dense').classes('full')
                if is_video:
                    c['external_audio'] = ui.input('Ścieżka do dodatkowego pliku audio (opcjonalnie)').props('outlined dense').classes('full q-mt-md')
                    c['external_subtitle'] = ui.input('Ścieżka do pliku napisów (opcjonalnie)').props('outlined dense').classes('full q-mt-md')
                    c['fallback'] = ui.checkbox('W razie problemów z GPU użyj CPU', value=True)
                    c['cuda_decode'] = ui.checkbox('Dekodowanie NVIDIA CUDA, jeśli jest dostępne', value=False)
                if is_audio or is_video:
                    c['normalize'] = ui.checkbox('Normalizuj głośność', value=False)
                    with ui.expansion('Metadane wyniku', icon='sell').classes('full'):
                        ui.label('Puste pola zachowują metadane źródła.').classes('muted small q-mb-sm')
                        c['metadata_title'] = ui.input('Tytuł').props('outlined dense').classes('full q-mb-sm')
                        c['metadata_artist'] = ui.input('Wykonawca').props('outlined dense').classes('full q-mb-sm')
                        c['metadata_album'] = ui.input('Album').props('outlined dense').classes('full')
                c['keep_partial'] = ui.checkbox('Zachowaj niekompletny plik po błędzie lub anulowaniu', value=self.manager.settings.keep_partial)
                ui.label('Indeksy ścieżek sprawdzisz w szczegółach pliku. Tryb kopiowania wymaga zgodnych kodeków i nie obsługuje filtrów.').classes('muted small')
            if is_video: self.update_codec_options()
            else: self.update_advanced()
            ui.separator().classes('q-my-md')
            with ui.row().classes('full items-center justify-between'):
                ui.button('Podgląd komendy', icon='terminal', on_click=self.preview_command).props('flat').classes('text-btn')
                ui.button('Konwertuj', icon='arrow_forward', on_click=self.submit).classes('primary-btn')

    def current_operation(self):
        if self.page == 'tools': return self.operation
        if self.page == 'batch': return self.batch_operation
        return self.page

    def update_codec_options(self):
        c = self.controls
        if not c.get('video_codec') or not c.get('format'):
            self.update_advanced()
            return
        fmt = c['format'].value
        if fmt not in VIDEO_FORMATS: return
        videos = {'auto':'Automatycznie', 'copy':'Kopiuj bez rekompresji'}
        videos.update({codec:codec.upper() for codec in VIDEO_FORMATS[fmt]})
        audios = {'auto':'Automatycznie', 'copy':'Kopiuj bez rekompresji', 'none':'Usuń audio'}
        audios.update({codec:codec.upper() for codec in VIDEO_AUDIO[fmt]})
        for key, opts in [('video_codec', videos), ('audio_codec', audios)]:
            old = c[key].value
            c[key].set_options(opts, value=old if old in opts else 'auto')
        self.update_advanced()

    def update_advanced(self):
        c = self.controls
        if self.updating_options or 'speed' not in c or not c.get('format'):
            return
        self.updating_options = True
        try:
            op = self.current_operation()
            fmt = c['format'].value
            video = op in ('video', 'merge_video')
            acodec = c['audio_codec'].value if c.get('audio_codec') else 'auto'
            vcodec = c['video_codec'].value if c.get('video_codec') else 'auto'
            audio_copy = acodec in ('copy', 'none')
            video_copy = video and vcodec == 'copy'
            for key in ('sample_rate', 'channels', 'volume_db', 'normalize', 'audio_stream'):
                if c.get(key): c[key].set_enabled(not audio_copy)
            for key in ('width', 'height', 'fps', 'rotate', 'aspect', 'pixel_format', 'video_bitrate', 'crf', 'preset', 'device', 'threads', 'cuda_decode'):
                if c.get(key): c[key].set_enabled(not video_copy)
            c['speed'].set_enabled(not (video_copy or acodec == 'copy'))
            if c.get('cuda_decode'):
                c['cuda_decode'].set_enabled(not video_copy and c['device'].value in ('auto', 'nvidia'))
            if c.get('external_audio'): c['external_audio'].set_enabled(acodec != 'none')
            if not c.get('sample_rate'): return
            if video:
                selected_audio = VIDEO_AUDIO[fmt][0] if acodec == 'auto' else acodec
                audio_encoder = AUDIO_ENCODERS.get(selected_audio, '')
                lossless = selected_audio in ('alac', 'flac', 'pcm_s16le')
            else:
                audio_encoder = AUDIO_FORMATS[fmt][0]
                lossless = fmt in LOSSLESS_AUDIO
            c['audio_bitrate'].set_enabled(not audio_copy and not lossless)
            limits = {'libmp3lame': 320, 'libopus': 512, 'ac3': 640, 'wmav2': 384, 'mp2': 384}
            c['audio_bitrate'].max = limits.get(audio_encoder, 1536)
            rates = {0: 'Oryginalne', 8000: '8000 Hz', 12000: '12000 Hz', 16000: '16000 Hz',
                     22050: '22050 Hz', 24000: '24000 Hz', 32000: '32000 Hz', 44100: '44100 Hz',
                     48000: '48000 Hz', 96000: '96000 Hz'}
            if self.manager.ffmpeg and audio_encoder and not audio_copy:
                supported = encoder_capabilities(self.manager.ffmpeg, audio_encoder)['sample_rates']
                if supported: rates = {k: v for k, v in rates.items() if k == 0 or str(k) in supported}
            current = c['sample_rate'].value
            c['sample_rate'].set_options(rates, value=current if current in rates else 0)
            if video and not video_copy and self.manager.ffmpeg:
                try:
                    encoder, _ = select_video_encoder(vcodec, fmt, c['device'].value, c['fallback'].value,
                                                       self.manager.hardware, self.manager.encoders)
                    supported = encoder_capabilities(self.manager.ffmpeg, encoder)['pixel_formats']
                    pixels = ['auto', 'yuv420p', 'yuv422p', 'yuv444p', 'yuv420p10le']
                    options = {p: p for p in pixels if p == 'auto' or not supported or p in supported}
                    old = c['pixel_format'].value
                    c['pixel_format'].set_options(options, value=old if old in options else 'auto')
                except ValueError:
                    pass  # The command validator reports unavailable encoders before enqueueing.
        finally:
            self.updating_options = False

    def apply_preset(self, selected, builtins):
        if not selected: return
        data = builtins.get(selected[2:]) if selected.startswith('b:') else self.manager.store.presets().get(selected[2:])
        if not data: return
        # Clear resolution from a previous profile before applying another one.
        for key in ('width', 'height'):
            if key in self.controls: self.controls[key].set_value(data.get(key))
        for key, value in data.items():
            control = self.controls.get(key)
            if control is not None: control.set_value(value)
        self.update_codec_options()

    def save_preset(self, name: str, is_video: bool):
        try:
            data = {k:v.value for k,v in self.controls.items() if v is not None and k not in ('output_dir', 'external_audio', 'external_subtitle')}
            data['_kind'] = 'video' if is_video else 'audio'
            self.manager.store.save_preset(name or '', data)
            ui.notify('Profil zapisano.', type='positive')
            self.navigate(self.page)
        except Exception as exc: notify_error(exc)

    def delete_preset(self, value):
        if not value or not value.startswith('u:'):
            ui.notify('Wybierz własny profil do usunięcia.', type='warning'); return
        self.manager.store.delete_preset(value[2:])
        ui.notify('Profil usunięto.', type='positive')
        self.navigate(self.page)

    def specs(self) -> list[ConversionSpec]:
        if not self.files: raise ValueError('Najpierw dodaj co najmniej jeden plik.')
        op = self.current_operation()
        data = {key:control.value for key, control in self.controls.items() if control is not None and control.value is not None and getattr(control, 'enabled', True)}
        for name in ('external_audio', 'external_subtitle'):
            if not data.get(name): data.pop(name, None)
        data['operation'] = op
        if 'format' not in data: data['format'] = 'mp4'
        paths = [item['path'] for item in self.files]
        groups = [paths] if op in ('merge_video', 'merge_audio') else [[p] for p in paths]
        return [ConversionSpec.model_validate({**data, 'sources':group}) for group in groups]

    async def preview_command(self):
        try:
            specs = self.specs()
            _, command, warning = await self.manager.prepare(specs[0])
            with ui.dialog() as dialog, ui.card().classes('panel').style('width: 900px; max-width: 95vw'):
                ui.label('Podgląd polecenia FFmpeg').classes('panel-title')
                ui.label('Przykład dla pierwszego zadania; nazwy plików tymczasowych i wyniku są ustalane podczas wykonania.').classes('muted small')
                if warning: ui.label(warning).classes('small')
                ui.label(display_command(command)).classes('logs full')
                ui.button('Zamknij', on_click=dialog.close).classes('quiet-btn self-end')
            dialog.open()
        except Exception as exc: notify_error(exc)

    async def submit(self):
        try:
            specs = self.specs()
            # Validate the entire batch before starting any of it.
            prepared = [await self.manager.prepare(spec) for spec in specs]
            for spec, result in zip(specs, prepared): await self.manager.add(spec, result)
            ui.notify(f'Dodano zadań do kolejki: {len(specs)}', type='positive')
            self.navigate('queue')
        except Exception as exc: notify_error(exc)

    def queue(self, history: bool):
        title = 'Historia konwersji' if history else 'Kolejka zadań'
        subtitle = 'Wróć do wykonanych zadań i sprawdź ich szczegóły.' if history else 'Śledź postęp, zarządzaj zadaniami i pobieraj gotowe pliki.'
        with self.header('TWOJE ZADANIA', title, subtitle):
            ui.button('Nowa konwersja', icon='add', on_click=lambda: self.navigate('video')).classes('primary-btn')
        with ui.column().classes('panel full gap-0'):
            with ui.element('div').classes('panel-head full'):
                with ui.column().classes('gap-0'):
                    ui.label('Zakończone zadania' if history else 'Wszystkie zadania').classes('panel-title')
                    ui.label('Postęp jest odczytywany bezpośrednio z FFmpeg.').classes('panel-subtitle')
                ui.button('Wyczyść zakończone', on_click=self.confirm_clear).props('flat dense').classes('text-btn')
            @ui.refreshable
            def listing():
                jobs = sorted(self.manager.items.values(), key=lambda j: j['created'], reverse=True)
                if history: jobs = [j for j in jobs if j['status'] not in ('waiting', 'running')]
                if not jobs: ui.label('Brak zadań do wyświetlenia.').classes('muted small q-py-lg')
                for job in jobs:
                    progress = job.get('progress')
                    with ui.column().classes('queue-item full gap-2'):
                        with ui.row().classes('full items-center no-wrap'):
                            with ui.element('div').classes('file-icon'): ui.icon('description')
                            with ui.column().classes('activity-main gap-0'):
                                ui.label(job['source']).classes('file-name')
                                ui.label(f"{job['input_format'].upper()} → {job['output_format'].upper()} · {job['id'][:8]}").classes('activity-meta')
                            ui.label(STATUS.get(job['status'], job['status'])).classes(f"badge {job['status']}")
                        with ui.row().classes('full justify-between items-center small muted'):
                            percent = f'{progress:.1f}%' if progress is not None else 'nieznany'
                            ui.label(f"Postęp: {percent} · Czas: {time_text(job.get('elapsed'))} · Pozostało: {time_text(job.get('eta'))} · Prędkość: {job.get('speed') or '—'}")
                            if job.get('output_size') is not None: ui.label(bytes_text(job['output_size']))
                        bar = ui.linear_progress(value=max(0, min(1, (progress or 0) / 100)), color='positive' if job['status'] == 'completed' else 'primary').props('rounded size=6px').classes('full')
                        if progress is None and job['status'] == 'running': bar.props('indeterminate')
                        if job.get('error'): ui.label(job['error']).classes('small').style('color:#f2a0aa')
                        with ui.element('div').classes('queue-actions'):
                            ui.button('Szczegóły i log', icon='description', on_click=lambda j=job: self.job_detail(j)).props('flat dense').classes('text-btn')
                            if job['status'] in ('waiting', 'running'):
                                ui.button('Anuluj', icon='cancel', on_click=lambda j=job: self.cancel_job(j)).props('flat dense').classes('text-btn')
                            if job['status'] in ('failed', 'cancelled', 'interrupted'):
                                ui.button('Ponów', icon='replay', on_click=lambda j=job: self.retry_job(j)).props('flat dense').classes('text-btn')
                            if job['status'] == 'completed' and job.get('output'):
                                ui.button('Pobierz', icon='download', on_click=lambda j=job: self.download_job(j)).props('flat dense').classes('text-btn')
                                ui.button('Otwórz folder', icon='folder_open', on_click=lambda j=job: self.open_folder(j)).props('flat dense').classes('text-btn')
            listing()
        self.timer = ui.timer(2, lambda: None if self.dialog_open else listing.refresh(), immediate=False)

    def confirm_clear(self):
        with ui.dialog().on_value_change(lambda e: setattr(self, 'dialog_open', bool(e.value))) as dialog, ui.card().classes('panel'):
            ui.label('Wyczyścić zakończone zadania?').classes('panel-title')
            ui.label('Wpisy zostaną usunięte z historii. Pliki wynikowe pozostaną na dysku.').classes('muted small')
            with ui.row().classes('self-end'):
                ui.button('Anuluj', on_click=dialog.close).classes('quiet-btn')
                def clear():
                    self.manager.clear_finished(); dialog.close(); self.navigate(self.page)
                ui.button('Wyczyść', on_click=clear).classes('primary-btn')
        dialog.open()

    def job_detail(self, job: dict):
        with ui.dialog().on_value_change(lambda e: setattr(self, 'dialog_open', bool(e.value))) as dialog, ui.card().classes('panel').style('width: 950px; max-width: 95vw'):
            with ui.row().classes('full items-center justify-between'):
                ui.label('Szczegóły zadania').classes('panel-title')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            with ui.element('div').classes('meta-grid full'):
                details = {'ID': job['id'], 'Status': STATUS.get(job['status'], job['status']),
                           'Plik': job['source'], 'Utworzono (UTC)': job['created'],
                           'Rozpoczęto (UTC)': job.get('started') or '—', 'Zakończono (UTC)': job.get('finished') or '—',
                           'Czas pracy': time_text(job.get('elapsed')), 'Wynik': job.get('output') or '—'}
                for key, value in details.items():
                    with ui.column().classes('gap-0'):
                        ui.label(key).classes('muted small'); ui.label(value).classes('meta-value')
            ui.label('Polecenie FFmpeg').classes('bold small q-mt-md')
            ui.label(job.get('command') or '—').classes('logs full')
            ui.label('Log FFmpeg').classes('bold small q-mt-md')
            ui.label(job.get('logs') or 'Brak komunikatów.').classes('logs full')
            ui.button('Zamknij', on_click=dialog.close).classes('quiet-btn self-end')
        dialog.open()

    def cancel_job(self, job: dict):
        self.manager.cancel(job['id'])
        ui.notify('Wysłano żądanie anulowania.', type='info')

    async def retry_job(self, job: dict):
        try:
            await self.manager.retry(job['id'])
            ui.notify('Dodano ponowione zadanie.', type='positive')
        except Exception as exc: notify_error(exc)

    def download_job(self, job: dict):
        if job.get('output') and Path(job['output']).is_file():
            ui.download(job['output'])
        else:
            ui.notify('Plik wynikowy nie jest już dostępny.', type='warning')

    def open_folder(self, job: dict):
        try:
            folder = Path(job['output']).parent
            if os.name != 'nt': raise ValueError('Otwieranie folderu jest dostępne w systemie Windows.')
            os.startfile(str(folder))
        except Exception as exc: notify_error(exc)

    def settings_page(self):
        self.header('PREFERENCJE', 'Ustawienia', 'Dostosuj środowisko pracy i lokalizacje plików.')
        settings = self.manager.settings
        with ui.element('div').classes('editor-grid full'):
            with ui.column().classes('panel full gap-0'):
                ui.label('Środowisko i pliki').classes('panel-title')
                ui.label('Podaj pełną ścieżkę do programu albo folderu zawierającego plik EXE. Puste pole korzysta z PATH.').classes('panel-subtitle q-mb-md')
                ffmpeg = ui.input('Ścieżka FFmpeg', value=settings.ffmpeg).props('outlined dense').classes('full q-mb-md')
                ffprobe = ui.input('Ścieżka FFprobe', value=settings.ffprobe).props('outlined dense').classes('full q-mb-md')
                folder = ui.input('Domyślny folder wynikowy', value=settings.output_dir).props('outlined dense').classes('full q-mb-md')
                count = ui.number('Maksymalna liczba równoległych zadań', value=settings.concurrency, min=1, max=8, step=1).props('outlined dense').classes('full q-mb-md')
                keep = ui.checkbox('Domyślnie zachowuj pliki częściowe', value=settings.keep_partial)
                async def save():
                    try:
                        if folder.value: output_directory(folder.value)
                        ffm = locate_binary(ffmpeg.value or '', 'ffmpeg')
                        ffp = locate_binary(ffprobe.value or '', 'ffprobe')
                        if not ffm or not ffp: raise ValueError('Nie znaleziono FFmpeg lub FFprobe. Sprawdź ścieżki.')
                        value = AppSettings(output_dir=folder.value or '', ffmpeg=ffmpeg.value or '', ffprobe=ffprobe.value or '',
                                            concurrency=int(count.value), theme=self.manager.settings.theme, keep_partial=keep.value)
                        self.manager.store.save_settings(value)
                        self.manager.settings = value
                        self.manager.ffmpeg, self.manager.ffprobe = ffm, ffp
                        self.manager.encoders = await asyncio.to_thread(encoder_names, ffm)
                        self.manager.hardware = {}
                        self.manager.wake.set()
                        ui.notify('Ustawienia zapisano. Trwa test akceleracji GPU.', type='positive')
                        asyncio.create_task(self.test_gpu())
                    except Exception as exc: notify_error(exc)
                ui.button('Zapisz ustawienia', icon='save', on_click=save).classes('primary-btn self-start q-mt-md')
            with ui.column().classes('panel full gap-0'):
                ui.label('Informacje o środowisku').classes('panel-title')
                ui.label('Aplikacja działa lokalnie i nie wysyła multimediów do usług zewnętrznych.').classes('panel-subtitle q-mb-md')
                ui.label('FFmpeg').classes('muted small'); ui.label(self.manager.ffmpeg or 'Nie znaleziono').classes('small q-mb-md')
                ui.label('FFprobe').classes('muted small'); ui.label(self.manager.ffprobe or 'Nie znaleziono').classes('small q-mb-md')
                ui.label('Akceleracja sprzętowa').classes('muted small')
                for vendor in ('nvidia', 'intel', 'amd'):
                    available = [codec.upper() for codec, ok in self.manager.hardware.get(vendor, {}).items() if ok]
                    ui.label(f'{vendor.upper()}: {", ".join(available) if available else "brak / niesprawdzona"}').classes('small q-mt-sm')
                ui.label('Wyniki testów zależą od karty, sterownika i kompilacji FFmpeg. CPU pozostaje dostępne jako tryb awaryjny.').classes('muted small q-mt-md')
                ui.button('Sprawdź ponownie GPU', icon='refresh', on_click=self.test_gpu).classes('quiet-btn self-start q-mt-md')

    async def test_gpu(self):
        if not self.manager.ffmpeg:
            ui.notify('Najpierw skonfiguruj FFmpeg.', type='warning'); return
        self.manager.hardware = await asyncio.to_thread(detect_hardware, self.manager.ffmpeg, self.manager.encoders)
        ui.notify('Zakończono test akceleracji sprzętowej.', type='positive')
        if self.page == 'settings': self.navigate('settings')
