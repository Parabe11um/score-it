"""Exercise synthetic files with real production parser limits, without a DB."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from django.conf import settings
    settings.configure(INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'poker'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.dummy'}}, SECRET_KEY='parser',
        USE_I18N=False, DEFAULT_AUTO_FIELD='django.db.models.BigAutoField', LOGGING_CONFIG=None)
    import django
    django.setup()
    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile
    from poker.import_process import parse_import, ImportRejected
    from import_security_corpus import (
        ambiguous_padded_sheets, bad_shared_string, csv_file, duplicate_member,
        encrypted_member, expansion_bomb, unsupported_compression, xlsx_rows,
    )
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(), synthetic_only=True,
        database_engine=settings.DATABASES['default']['ENGINE'], ok=False, cases=[],
        packages={name: version(name) for name in ('Django', 'openpyxl', 'defusedxml')},
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            'poker/import_process.py', 'poker/import_worker.py', 'poker/task_import.py',
            'poker/sprint_import.py', 'tests/import_security_corpus.py')})
    cases = [
        ('csv_79', lambda: csv_file(79), 'csv', 79),
        ('xlsx_79', lambda: xlsx_rows(79), 'xlsx', 79),
        ('csv_5000', lambda: csv_file(5000), 'csv', 5000),
        ('xlsx_5000', lambda: xlsx_rows(5000), 'xlsx', 5000),
        ('unsupported_compression', unsupported_compression, 'xlsx', None),
        ('encrypted_member', encrypted_member, 'xlsx', None),
        ('invalid_shared_string', bad_shared_string, 'xlsx', None),
        ('duplicate_member', duplicate_member, 'xlsx', None),
        ('padded_ambiguous_sheets', ambiguous_padded_sheets, 'xlsx', None),
        ('zip_expansion_over_64MiB', expansion_bomb, 'xlsx', None),
    ]
    try:
        for name, generate, extension, expected in cases:
            data = generate()
            started = time.monotonic()
            item = dict(name=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            try:
                parsed, metrics = parse_import(SimpleUploadedFile('synthetic.' + extension, data), with_metrics=True)
                if expected is None or parsed.total_rows != expected:
                    raise AssertionError('Unexpected accepted result for ' + name)
                item.update(result='accepted', rows=parsed.total_rows, metrics=metrics)
            except ImportRejected as error:
                if expected is not None:
                    raise AssertionError('Normal export was rejected: ' + name) from error
                item.update(result='rejected', metrics=error.metrics)
            except ValidationError as error:
                raise AssertionError('Resource process did not return a bounded result for ' + name) from error
            item['wall_seconds'] = time.monotonic() - started
            metrics = item['metrics']
            assert metrics['address_space_limit'] == 256 * 1024 * 1024
            assert metrics['cpu_limit'] == 10 and metrics['output_limit'] == 16 * 1024 * 1024
            assert metrics['database_engine'] == 'django.db.backends.dummy'
            assert not metrics['sensitive_environment_present']
            report['cases'].append(item)
        report['ok'] = True
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('IMPORT_RESOURCE_LIMITS_OK')
    print('IMPORT_NORMAL_EXPORTS_OK')
    print('IMPORT_ADVERSARIAL_CORPUS_OK')


if __name__ == '__main__':
    main()
