"""Atomic document writes and bounded extraction, independent of network clients."""
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import tempfile
import zipfile


MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_EXTRACT_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 10000


def filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')
    if not value:
        value = 'Документ'
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '_' + value
    path = Path(value)
    return path.stem[:110] + path.suffix[:12]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish_file(staging, folder, name):
    """Never replace an existing file, including one created during the download."""
    staging, folder = Path(staging), Path(folder)
    if not staging.is_file() or staging.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('Документ отсутствует или превышает 512 МиБ.')
    folder.mkdir(parents=True, exist_ok=True)
    name = filename(name)
    original = folder / name
    for index in range(10000):
        target = original if index == 0 else original.with_name(f'{original.stem} ({index}){original.suffix}')
        if target.is_symlink():
            continue
        if target.is_file() and target.stat().st_size == staging.stat().st_size and digest(target) == digest(staging):
            return target
        try:
            # Same-volume hard link publishes a complete file atomically with no clobber.
            os.link(staging, target)
            return target
        except FileExistsError:
            continue
    raise ValueError('Не удалось выбрать свободное имя документа.')


def _member_path(name):
    path = PureWindowsPath(name)
    if (not name or path.is_absolute() or path.drive or '..' in path.parts
            or any(':' in part or part.rstrip(' .') != part or filename(part) != part
                   for part in path.parts)):
        raise ValueError('Архив содержит небезопасный путь.')
    return Path(*path.parts)


def extract_archive(archive):
    """Extract to private staging, then publish into a new directory; keep original."""
    archive = Path(archive)
    extension = archive.suffix.lower()
    if extension not in ('.zip', '.7z'):
        if extension in ('.rar', '.tar', '.gz', '.bz2', '.xz'):
            return 'Архив сохранён; автоматическая распаковка этого формата не поддерживается.'
        return ''
    with tempfile.TemporaryDirectory(prefix='.extract-', dir=archive.parent) as temporary:
        destination = Path(temporary) / 'files'
        destination.mkdir()
        if extension == '.zip':
            with zipfile.ZipFile(archive, metadata_encoding='cp866') as source:
                entries = source.infolist()
                if len(entries) > MAX_MEMBERS or sum(e.file_size for e in entries) > MAX_EXTRACT_BYTES:
                    raise ValueError('Архив превышает предел распаковки (1 ГиБ / 10000 записей).')
                for entry in entries:
                    relative = _member_path(entry.filename.rstrip('/'))
                    if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                        raise ValueError('Архив содержит ссылки или защищён паролем.')
                    target = destination / relative
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with source.open(entry) as incoming, target.open('xb') as outgoing:
                            shutil.copyfileobj(incoming, outgoing)
        else:
            import py7zr
            with py7zr.SevenZipFile(archive, max_extract_size=MAX_EXTRACT_BYTES) as source:
                entries = source.list()
                if (source.needs_password() or len(entries) > MAX_MEMBERS
                        or sum(e.uncompressed or 0 for e in entries) > MAX_EXTRACT_BYTES):
                    raise ValueError('Архив защищён паролем или превышает предел распаковки.')
                for entry in entries:
                    _member_path(entry.filename.rstrip('/'))
                # Reject links before extraction, including junctions and hard links.
                for entry in source.files:
                    if entry.is_symlink or entry.is_junction:
                        raise ValueError('Архив содержит ссылки.')
                source.extractall(path=destination)
        base = filename(archive.stem) + f' ({extension[1:]})'
        for index in range(10000):
            target = archive.parent / (base if index == 0 else f'{base} ({index})')
            if target.exists() or target.is_symlink():
                continue
            # Windows rename refuses to replace an existing destination directory.
            destination.rename(target)
            return ''
    raise ValueError('Не удалось выбрать папку распаковки.')
