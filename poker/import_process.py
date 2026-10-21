"""Bound the complete untrusted-file parser, including HTML/decimal handling.

Linux/WSL is required. The child has no application credentials or database and
uses JSON, never pickle. This is a resource boundary, not an OS security sandbox.
"""

from collections import Counter
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from decimal import Decimal
from functools import wraps
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from django.core.exceptions import ValidationError
from django.http import HttpResponse


MEMORY_BYTES = 256 * 1024 * 1024
CPU_SECONDS = 10
WALL_SECONDS = 15
RESULT_BYTES = 16 * 1024 * 1024
BUSY_MESSAGE = 'Импорт уже выполняется. Повторите отправку через несколько секунд.'
COMPLEX_MESSAGE = 'Файл не импортирован: превышен предел ресурсов разбора. Разделите выгрузку на несколько файлов.'
_admitted = ContextVar('score_it_import_admitted', default=False)


class ImportBusy(ValidationError):
    def __init__(self):
        super().__init__(BUSY_MESSAGE, code='import_busy')


class ImportRejected(ValidationError):
    def __init__(self, messages, metrics):
        super().__init__(messages)
        self.metrics = metrics


@contextmanager
def import_slot():
    if _admitted.get():
        yield
        return
    if sys.platform != 'linux' or not hasattr(os, 'memfd_create'):
        raise ValidationError('Безопасный импорт требует Linux, WSL или Docker.')
    import fcntl
    directory = Path(tempfile.gettempdir()) / ('score-it-imports-' + str(os.geteuid()))
    descriptor = None
    token = None
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError('Unsafe import lock directory')
        descriptor = os.open(directory / 'active.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise OSError('Unsafe import lock file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportBusy() from None
        token = _admitted.set(True)
        yield
    except OSError:
        raise ValidationError('Импорт временно недоступен. Повторите отправку позже.') from None
    finally:
        if token is not None:
            _admitted.reset(token)
        if descriptor is not None:
            os.close(descriptor)


def bounded_import_view(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        with ExitStack() as stack:
            try:
                stack.enter_context(import_slot())
            except ImportBusy:
                response = HttpResponse(BUSY_MESSAGE, status=429, content_type='text/plain; charset=utf-8')
                response['Retry-After'] = '3'
                response['Cache-Control'] = 'no-store'
                return response
            except ValidationError:
                return HttpResponse('Импорт временно недоступен. Повторите позже.', status=503)
            return view(request, *args, **kwargs)
    return wrapped


def parse_import(upload, *, require_status=True, with_metrics=False):
    from . import task_import
    from .sprint_import import PlanningImport, PlanningTask
    extension = Path(upload.name).suffix.lower()
    if extension not in ('.csv', '.xlsx'):
        raise ValidationError('Выберите файл CSV или XLSX.')
    if upload.size > task_import.MAX_FILE_BYTES:
        raise ValidationError('Размер файла не должен превышать 10 МБ.')
    with import_slot():
        data = upload.read(task_import.MAX_FILE_BYTES + 1)
        if len(data) > task_import.MAX_FILE_BYTES:
            raise ValidationError('Размер файла не должен превышать 10 МБ.')
        request = dict(extension=extension, require_status=require_status,
                       max_file_bytes=task_import.MAX_FILE_BYTES, max_rows=task_import.MAX_ROWS,
                       max_columns=task_import.MAX_COLUMNS)
        source = json.dumps(request).encode() + b'\n' + data
        environment = dict(PATH=os.defpath, LC_ALL='C.UTF-8', PYTHONNOUSERSITE='1',
                           OPENPYXL_LXML='False', OPENPYXL_DEFUSEDXML='True')
        try:
            # Anonymous memory-backed output does not compete with uploaded files
            # for the production /tmp tmpfs. RLIMIT_FSIZE also bounds this fd.
            with os.fdopen(os.memfd_create('score-it-import-result', os.MFD_CLOEXEC), 'w+b') as output:
                result = subprocess.run([sys.executable, '-m', 'poker.import_worker'],
                    input=source, stdout=output, stderr=subprocess.DEVNULL,
                    cwd=Path(__file__).resolve().parents[1], env=environment,
                    timeout=WALL_SECONDS, check=False)
                if result.returncode or not 0 < os.fstat(output.fileno()).st_size <= RESULT_BYTES:
                    raise ValidationError(COMPLEX_MESSAGE)
                output.seek(0)
                response = json.load(output)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            raise ValidationError(COMPLEX_MESSAGE) from None
        if not response.get('ok'):
            raise ImportRejected(response.get('errors') or [COMPLEX_MESSAGE], response['metrics'])
        value = response['value']
        parsed = PlanningImport(
            rows=[PlanningTask(task_import.ImportedTask(**item['task']),
                   None if item['estimate'] is None else Decimal(item['estimate']),
                   item['eva_status'], item['eva_sprints'], item['skip_reason']) for item in value['rows']],
            counts=Counter(value['counts']), has_sprints_column=value['has_sprints_column'],
            has_quota_column=value['has_quota_column'], has_status_column=value['has_status_column'],
            relationship_columns=tuple(value['relationship_columns']), issues=value['issues'])
        return (parsed, response['metrics']) if with_metrics else parsed
