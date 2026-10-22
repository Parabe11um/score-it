"""Exercise synthetic files with real production parser limits, without a DB."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Materialize only these two test tools in a disposable container's tmpfs.
# /app remains the exact, read-only production payload under test.
IMAGE_PROGRAM = r"""
import json, pathlib, runpy, sys, tempfile
payload = json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix='score-it-import-check-') as directory:
    root = pathlib.Path(directory)
    tools = root / 'tests'
    tools.mkdir()
    for name in ('check_import_resources.py', 'import_security_corpus.py'):
        (tools / name).write_text(payload[name])
    sys.path.insert(0, str(tools))
    report = root / 'result.json'
    sys.argv = [str(tools / 'check_import_resources.py'), '--app-root', '/app', '--output', str(report)]
    runpy.run_path(sys.argv[0], run_name='__main__')
    print('IMPORT_RESOURCE_REPORT=' + json.dumps(json.loads(report.read_text())))
"""


def check_image(args):
    from check_caddy_runtime import docker, require
    require(bool(re.fullmatch(r'(?:score-it:[0-9a-f]{40}|sha256:[0-9a-f]{64})', args.image)),
            'Use a local release image with its full commit SHA or image ID')
    image = json.loads(docker('image', 'inspect', args.image))[0]
    sources = {name: (ROOT / 'tests' / name).read_text()
               for name in ('check_import_resources.py', 'import_security_corpus.py')}
    output = docker('run', '--rm', '-i', '--pull=never', '--network', 'none', '--read-only',
                    '--tmpfs', '/tmp:size=64m,mode=1777', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges:true', '--entrypoint', 'python',
                    image['Id'], '-c', IMAGE_PROGRAM, input=json.dumps(sources), timeout=180)
    markers, separator, raw = output.rpartition('IMPORT_RESOURCE_REPORT=')
    require(bool(separator), 'Import check returned no structured report')
    report = json.loads(raw)
    require(report.get('ok'), 'Import resource check failed')
    report.update(image_reference=args.image, runtime_image_id=image['Id'], network_mode='none',
                  tool_delivery='build-host sources in disposable container tmpfs')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(markers.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--image', help='Run against a local release image, without a database or network')
    parser.add_argument('--app-root', type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.image:
        check_image(args)
        return
    app_root = args.app_root.resolve()
    sys.path.insert(0, str(app_root))
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
        source_sha256={name: hashlib.sha256((app_root / name).read_bytes()).hexdigest() for name in (
            'poker/import_process.py', 'poker/import_worker.py', 'poker/task_import.py',
            'poker/sprint_import.py')})
    report['source_sha256']['tests/import_security_corpus.py'] = hashlib.sha256(
        (ROOT / 'tests/import_security_corpus.py').read_bytes()).hexdigest()
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
