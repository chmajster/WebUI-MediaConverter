from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .engine import build_command, display_command
from .files import available_output, local_file, output_directory, publish, safe_stem
from .media import duration, probe
from .models import VIDEO_AUDIO, AppSettings, ConversionSpec
from .store import Store


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expected_duration(spec: ConversionSpec, media: list[dict]) -> float:
    lengths = [duration(item) for item in media]
    total = sum(lengths) if all(length > 0 for length in lengths) else 0
    if spec.end is not None:
        total = min(total, spec.end) if total else spec.end
    return max(0.0, (total - spec.start) / spec.speed) if total else 0.0


class JobManager:
    def __init__(self, store: Store, data_dir: Path, ffmpeg: str | None, ffprobe: str | None,
                 encoders: set[str], hardware: dict):
        self.store, self.data_dir = store, data_dir
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.encoders, self.hardware = encoders, hardware
        self.settings = store.settings()
        self.items = {j['id']: j for j in store.jobs()}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.wake = asyncio.Event()
        self.scheduler: asyncio.Task | None = None
        self.shutting_down = False
        self.temp_dir = data_dir / 'temp'
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._clean_stale_temp()
        self._clean_interrupted_partials()
        self._clean_old_uploads()

    def _clean_stale_temp(self):
        # Only our private work directory is cleaned; source and output folders are untouched.
        for entry in self.temp_dir.iterdir():
            try:
                if entry.is_dir(): shutil.rmtree(entry)
                else: entry.unlink()
            except OSError:
                pass

    def _clean_interrupted_partials(self):
        for job in self.items.values():
            if job.get('status') != 'interrupted': continue
            try:
                uuid.UUID(hex=job['id'])
                spec = ConversionSpec.model_validate(job['spec'])
                if spec.keep_partial: continue
                folder = output_directory(spec.output_dir)
                stem = safe_stem(job['source']) + ('_połączone' if len(spec.sources) > 1 else '_konwersja')
                (folder / f'.{stem}.{job["id"]}.częściowy.{spec.output_extension}').unlink(missing_ok=True)
            except (OSError, ValueError, KeyError):
                pass

    def _clean_old_uploads(self):
        uploads = self.data_dir / 'uploads'
        if not uploads.is_dir(): return
        retained = set()
        for job in self.items.values():
            if job.get('status') in ('failed', 'cancelled', 'interrupted', 'waiting', 'running'):
                for source in job.get('spec', {}).get('sources', []):
                    retained.add(str(Path(source).parent.resolve()))
        for folder in uploads.iterdir():
            try:
                uuid.UUID(hex=folder.name)
                if (folder.is_dir() and not folder.is_symlink() and str(folder.resolve()) not in retained
                        and time.time() - folder.stat().st_mtime > 7 * 86400):
                    shutil.rmtree(folder)
            except (OSError, ValueError):
                pass

    def start(self):
        if not self.scheduler:
            self.scheduler = asyncio.create_task(self._schedule())

    async def shutdown(self):
        self.shutting_down = True
        for job in self.items.values():
            if job['status'] in ('waiting', 'running'):
                job['cancel_requested'] = True
                if job['status'] == 'waiting':
                    job['status'] = 'interrupted'
                    job['error'] = 'Zadanie przerwano po zamknięciu aplikacji.'
                    self._save(job)
        for process in list(self.processes.values()):
            if process.returncode is None:
                try: process.terminate()
                except ProcessLookupError: pass
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self.scheduler:
            self.scheduler.cancel()
            await asyncio.gather(self.scheduler, return_exceptions=True)

    async def prepare(self, spec: ConversionSpec) -> tuple[list[dict], list[str], str | None]:
        output_directory(spec.output_dir)
        media = await asyncio.gather(*(asyncio.to_thread(probe, path, self.ffprobe) for path in spec.sources))
        if spec.external_audio:
            external = await asyncio.to_thread(probe, spec.external_audio, self.ffprobe)
            audio = next((s for s in external.get('streams', []) if s.get('codec_type') == 'audio'), None)
            if not audio:
                raise ValueError('Dodatkowy plik nie zawiera ścieżki audio.')
            if spec.audio_codec == 'copy' and audio.get('codec_name') not in VIDEO_AUDIO.get(spec.format, ()):
                raise ValueError('Kodek dodatkowej ścieżki audio nie jest zgodny z wybranym kontenerem.')
        if spec.external_subtitle:
            external = await asyncio.to_thread(probe, spec.external_subtitle, self.ffprobe)
            if not any(s.get('codec_type') == 'subtitle' for s in external.get('streams', [])):
                raise ValueError('Dodatkowy plik nie zawiera ścieżki napisów.')
        dummy = Path(spec.output_dir) / f'podglad.{spec.output_extension}'
        if spec.operation == 'frames': dummy = self.temp_dir / 'frame_%06d.jpg'
        command, warning = build_command(self.ffmpeg, spec, media, dummy, self.encoders, self.hardware,
                                         concat_file=self.temp_dir / 'lista.txt')
        return media, command, warning

    async def add(self, spec: ConversionSpec, prepared=None) -> dict:
        media, command, warning = prepared if prepared is not None else await self.prepare(spec)
        job_id = uuid.uuid4().hex
        source_name = Path(spec.sources[0]).name
        job = {'id': job_id, 'created': now(), 'started': None, 'finished': None,
               'status': 'waiting', 'progress': 0.0 if expected_duration(spec, media) else None,
               'elapsed': 0.0, 'eta': None, 'speed': None,
               'source': source_name, 'input_format': Path(source_name).suffix.lstrip('.').lower(),
               'output_format': spec.output_extension, 'output': None, 'output_size': None,
               'error': None, 'logs': warning or '', 'command': display_command(command),
               'spec': spec.model_dump(), 'media': media, 'cancel_requested': False}
        self.items[job_id] = job
        self._save(job)
        self.start()
        self.wake.set()
        return job

    async def retry(self, job_id: str) -> dict:
        job = self.items[job_id]
        if job['status'] not in ('failed', 'cancelled', 'interrupted'):
            raise ValueError('Ponowić można zadanie błędne, anulowane lub przerwane.')
        return await self.add(ConversionSpec.model_validate(job['spec']))

    def cancel(self, job_id: str):
        job = self.items[job_id]
        if job['status'] == 'waiting':
            job['cancel_requested'] = True
            job['status'] = 'cancelled'
            job['finished'] = now()
            self._save(job)
        elif job['status'] == 'running':
            job['cancel_requested'] = True
            process = self.processes.get(job_id)
            if process and process.returncode is None:
                try: process.terminate()
                except ProcessLookupError: pass
        self.wake.set()

    def clear_finished(self):
        ids = [j['id'] for j in self.items.values() if j['status'] not in ('waiting', 'running')]
        self.store.delete_jobs(ids)
        for job_id in ids: self.items.pop(job_id, None)

    def _save(self, job: dict):
        self.store.save_job(job)

    async def _schedule(self):
        while not self.shutting_down:
            while len(self.tasks) < self.settings.concurrency:
                queued = sorted((j for j in self.items.values() if j['status'] == 'waiting'), key=lambda j: j['created'])
                if not queued: break
                job = queued[0]
                # Mark before creating the task so it cannot be scheduled twice.
                job['status'] = 'running'
                self._save(job)
                task = asyncio.create_task(self._run(job))
                self.tasks[job['id']] = task
                task.add_done_callback(lambda _, i=job['id']: self._finished(i))
            self.wake.clear()
            await self.wake.wait()

    def _finished(self, job_id: str):
        self.tasks.pop(job_id, None)
        self.wake.set()

    async def _run(self, job: dict):
        spec = ConversionSpec.model_validate(job['spec'])
        try:
            folder = output_directory(spec.output_dir)
            stem = safe_stem(job['source']) + ('_połączone' if len(spec.sources) > 1 else '_konwersja')
            target = available_output(folder, stem, spec.output_extension)
        except Exception as exc:
            job.update(status='failed', error=str(exc), finished=now())
            self._save(job)
            return
        partial = folder / f'.{stem}.{job["id"]}.częściowy.{spec.output_extension}'
        work = self.temp_dir / job['id']
        concat = work / 'lista.txt'
        job.update(started=now(), status='running')
        self._save(job)
        started = time.monotonic()
        try:
            work.mkdir(exist_ok=True)
            if len(spec.sources) > 1:
                # concat demuxer quoting: a literal single quote is represented by '\''.
                lines = ["file '" + Path(p).resolve().as_posix().replace("'", "'\\''") + "'" for p in spec.sources]
                concat.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            output = work / 'frame_%06d.jpg' if spec.operation == 'frames' else partial
            for attempt in (0, 1):
                if job['cancel_requested']: break
                command, warning = build_command(self.ffmpeg, spec, job['media'], output, self.encoders,
                                                 self.hardware, concat, force_cpu=bool(attempt))
                job['command'] = display_command(command)
                if warning: self._log(job, warning)
                code = await self._process(job, command, started)
                if job['cancel_requested']: break
                video_encoder = command[command.index('-c:v') + 1] if '-c:v' in command else ''
                is_hardware = any(x in video_encoder for x in ('_nvenc', '_qsv', '_amf'))
                if code == 0 or attempt or not is_hardware or not spec.fallback:
                    break
                partial.unlink(missing_ok=True)
                self._log(job, 'Enkoder sprzętowy zgłosił błąd. Ponawianie z użyciem CPU.')
            if job['cancel_requested']:
                job['status'] = 'interrupted' if self.shutting_down else 'cancelled'
                job['error'] = 'Zadanie przerwano po zamknięciu aplikacji.' if self.shutting_down else None
            elif code:
                job['status'] = 'failed'
                job['error'] = f'FFmpeg zakończył pracę z kodem {code}. Szczegóły znajdują się w logu.'
            else:
                if spec.operation == 'frames':
                    frames = sorted(work.glob('frame_*.jpg'))
                    if not frames: raise ValueError('Nie utworzono żadnych klatek.')
                    await asyncio.to_thread(self._zip_frames, frames, partial)
                if not partial.exists() or not partial.stat().st_size:
                    raise ValueError('FFmpeg nie utworzył pliku wynikowego.')
                final = await asyncio.to_thread(publish, partial, target)
                job.update(status='completed', progress=100.0, output=str(final), output_size=final.stat().st_size, eta=0)
        except Exception as exc:
            job['status'] = 'failed'
            job['error'] = str(exc)
            self._log(job, str(exc))
        finally:
            job['elapsed'] = round(time.monotonic() - started, 1)
            job['finished'] = now()
            if job['status'] != 'completed' and spec.keep_partial and spec.operation == 'frames':
                try:
                    frames = sorted(work.glob('frame_*.jpg'))
                    if frames: await asyncio.to_thread(self._zip_frames, frames, partial)
                except OSError as exc:
                    self._log(job, f'Nie udało się zachować klatek częściowych: {exc}')
            if job['status'] != 'completed' and not spec.keep_partial:
                partial.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)
            self._save(job)

    @staticmethod
    def _zip_frames(frames: list[Path], dest: Path):
        with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_STORED) as archive:
            for frame in frames: archive.write(frame, frame.name)

    def _log(self, job: dict, value: str):
        job['logs'] = (job.get('logs', '') + '\n' + value.strip())[-30000:]

    async def _process(self, job: dict, command: list[str], started: float) -> int:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
        self.processes[job['id']] = process
        if job['cancel_requested'] and process.returncode is None:
            try: process.terminate()
            except ProcessLookupError: pass
        async def read_errors():
            while line := await process.stderr.readline():
                self._log(job, line.decode('utf-8', errors='replace'))
        errors = asyncio.create_task(read_errors())
        spec = ConversionSpec.model_validate(job['spec'])
        expected = expected_duration(spec, job['media'])
        last_save = time.monotonic()
        try:
            while line := await process.stdout.readline():
                text = line.decode('utf-8', errors='replace').strip()
                key, _, value = text.partition('=')
                if key in ('out_time_us', 'out_time_ms'):
                    try:
                        seconds = int(value) / 1_000_000
                        if expected > 0: job['progress'] = min(99.9, round(seconds / expected * 100, 1))
                    except ValueError: pass
                elif key == 'speed':
                    job['speed'] = value
                    try:
                        speed = float(value.rstrip('x'))
                        if speed > 0 and expected > 0:
                            job['eta'] = max(0, round((expected - seconds) / speed))
                    except (ValueError, UnboundLocalError): pass
                job['elapsed'] = round(time.monotonic() - started, 1)
                if time.monotonic() - last_save >= 1:
                    self._save(job)
                    last_save = time.monotonic()
            code = await process.wait()
            await errors
            return code
        finally:
            self.processes.pop(job['id'], None)
