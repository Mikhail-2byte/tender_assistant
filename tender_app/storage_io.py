"""Atomic replacement of local text files; discard incomplete writes on failure."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def atomic_text(path, *, encoding='utf-8', newline=None):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding=encoding, newline=newline,
                                     prefix=path.name + '.', suffix='.tmp',
                                     dir=path.parent, delete=False) as stream:
        staging = Path(stream.name)
        try:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(staging, path)
        finally:
            stream.close()
            staging.unlink(missing_ok=True)
