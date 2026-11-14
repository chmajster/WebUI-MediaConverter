from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


def local_file(value: str) -> Path:
    path = Path(value.strip().strip('"')).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Podaj pełną ścieżkę lokalną bez elementów „..”.')
    if os.name == 'nt' and str(path).startswith('\\\\'):
        raise ValueError('Ścieżki sieciowe UNC nie są obsługiwane w trybie lokalnym.')
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'Plik nie istnieje: {path}')
    return path


def output_directory(value: str) -> Path:
    path = Path(value.strip().strip('"')).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Folder wynikowy musi być pełną ścieżką bez elementów „..”.')
    if os.name == 'nt' and str(path).startswith('\\\\'):
        raise ValueError('Folder wynikowy musi znajdować się na dysku lokalnym.')
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Folder wynikowy nie istnieje.')
    return path


def safe_stem(name: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Path(name).stem).strip(' .')
    if stem.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{x}' for x in range(1, 10)), *(f'LPT{x}' for x in range(1, 10))}:
        stem = '_' + stem
    return (stem[:120] or 'plik')


def available_output(folder: Path, stem: str, extension: str) -> Path:
    for n in range(10000):
        suffix = '' if n == 0 else f' ({n})'
        target = folder / f'{stem}{suffix}.{extension}'
        if not target.exists():
            return target
    raise ValueError('Nie można znaleźć wolnej nazwy pliku wynikowego.')


def publish(temp: Path, target: Path) -> Path:
    """Publish on the same volume without ever replacing a file created concurrently."""
    for _ in range(10000):
        try:
            if os.name == 'nt':
                # Windows rename is atomic and fails rather than replacing an existing target.
                os.rename(temp, target)
                return target
            # Hard links provide an atomic no-replace operation on NTFS and most local filesystems.
            os.link(temp, target)
            temp.unlink()
            return target
        except FileExistsError:
            target = available_output(target.parent, target.stem, target.suffix.lstrip('.'))
        except OSError:
            # Exclusive creation is also safe on filesystems without hard-link support.
            try:
                with target.open('xb') as dst, temp.open('rb') as src:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                temp.unlink()
                return target
            except FileExistsError:
                target = available_output(target.parent, target.stem, target.suffix.lstrip('.'))
            except BaseException:
                target.unlink(missing_ok=True)
                raise
    raise ValueError('Nie można zapisać pliku wynikowego.')
