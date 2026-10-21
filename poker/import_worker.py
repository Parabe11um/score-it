"""Private stdin/stdout parser process; never exposes a network endpoint."""

import json
import os
import resource
import sys


def apply_limits(*, memory_bytes=256 * 1024 * 1024, cpu_seconds=10, result_bytes=16 * 1024 * 1024):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (result_bytes, result_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def main():
    apply_limits()
    request = json.loads(sys.stdin.buffer.readline(4096))
    from django.conf import settings
    settings.configure(INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'poker'],
                       DATABASES={'default': {'ENGINE': 'django.db.backends.dummy'}},
                       SECRET_KEY='parser', USE_I18N=False,
                       DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
                       LOGGING_CONFIG=None)
    import django
    django.setup()
    from dataclasses import asdict
    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile
    from . import task_import
    from .sprint_import import _parse_sprint_file
    try:
        for key, field in [('max_file_bytes', 'MAX_FILE_BYTES'), ('max_rows', 'MAX_ROWS'), ('max_columns', 'MAX_COLUMNS')]:
            value = request[key]
            if not isinstance(value, int) or not 0 < value <= getattr(task_import, field):
                raise ValueError('Invalid private parser limits')
            setattr(task_import, field, value)
        if request['extension'] not in ('.csv', '.xlsx') or not isinstance(request['require_status'], bool):
            raise ValueError('Invalid private parser profile')
        data = sys.stdin.buffer.read(task_import.MAX_FILE_BYTES + 1)
        upload = SimpleUploadedFile('synthetic' + request['extension'], data)
        parsed = _parse_sprint_file(upload, require_status=request['require_status'])
        response = dict(ok=True, value=dict(
            rows=[dict(task=asdict(item.task), estimate=None if item.estimate is None else str(item.estimate),
                       eva_status=item.eva_status, eva_sprints=item.eva_sprints, skip_reason=item.skip_reason)
                  for item in parsed.rows], counts=dict(parsed.counts),
            has_sprints_column=parsed.has_sprints_column, has_quota_column=parsed.has_quota_column,
            has_status_column=parsed.has_status_column, relationship_columns=parsed.relationship_columns,
            issues=parsed.issues))
    except ValidationError as error:
        response = dict(ok=False, errors=error.messages[:20])
    except Exception:
        # Parser/library exceptions and file contents do not enter logs or HTML.
        response = dict(ok=False, errors=['Файл не импортирован. Проверьте формат или разделите выгрузку.'])
    usage = resource.getrusage(resource.RUSAGE_SELF)
    response['metrics'] = dict(pid=os.getpid(), uid=os.geteuid(), peak_rss_kib=usage.ru_maxrss,
        cpu_seconds=usage.ru_utime + usage.ru_stime,
        address_space_limit=resource.getrlimit(resource.RLIMIT_AS)[0],
        cpu_limit=resource.getrlimit(resource.RLIMIT_CPU)[0],
        output_limit=resource.getrlimit(resource.RLIMIT_FSIZE)[0],
        sensitive_environment_present=any(key in os.environ for key in
            ('POSTGRES_PASSWORD', 'DB_PASSWORD', 'DB_MIGRATION_PASSWORD', 'DJANGO_SECRET_KEY', 'DJANGO_SETTINGS_MODULE')),
        database_engine=settings.DATABASES['default']['ENGINE'])
    json.dump(response, sys.stdout, ensure_ascii=False, allow_nan=False)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
