import fcntl
import csv
from io import BytesIO
from io import StringIO
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from config.upload_limits import MAX_REQUEST_BYTES, MAX_UPLOAD_BYTES, UploadLimitMiddleware
from poker.import_process import import_slot, parse_import
from poker.models import Project, Sprint, Task, VotingSession
from poker.task_import import parse_task_file
from tests.import_security_corpus import (
    HEADERS, ROW, ambiguous_padded_sheets, bad_shared_string, csv_file,
    duplicate_member, encrypted_member, expansion_bomb, rewrite_xlsx,
    unsupported_compression, xlsx_file,
)


def uploaded(data, extension='xlsx'):
    return SimpleUploadedFile('synthetic.' + extension, data)


class ImportParserSecurityTests(SimpleTestCase):
    def test_library_exceptions_and_duplicate_members_become_validation_errors(self):
        for source in (unsupported_compression, encrypted_member, bad_shared_string, duplicate_member):
            with self.subTest(case=source.__name__), self.assertRaises(ValidationError):
                parse_task_file(uploaded(source()))

    def test_small_multi_sheet_padding_attack_is_rejected_normally(self):
        data = ambiguous_padded_sheets()
        self.assertLess(len(data), 400_000)
        with self.assertRaisesMessage(ValidationError, 'несколько листов'):
            parse_task_file(uploaded(data))

    def test_actual_rows_and_columns_are_bounded_despite_false_dimensions(self):
        for kind in ('rows', 'columns'):
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet()
            sheet.append(HEADERS)
            if kind == 'rows':
                for _ in range(5001):
                    sheet.append(ROW)
            else:
                sheet.append([*ROW, *([None] * (513 - len(ROW) - 1)), 'over-limit'])
            stream = BytesIO()
            workbook.save(stream)
            workbook.close()
            source = stream.getvalue()
            with ZipFile(BytesIO(source)) as archive:
                xml = archive.read('xl/worksheets/sheet1.xml')
            data = rewrite_xlsx({'xl/worksheets/sheet1.xml': xml.replace(b'<sheetData>', b'<dimension ref="A1:G2"/><sheetData>')})
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                parse_task_file(uploaded(data))

    def test_declared_padding_does_not_hide_valid_trailing_empty_cells(self):
        with ZipFile(BytesIO(xlsx_file())) as archive:
            xml = archive.read('xl/worksheets/sheet1.xml')
        import re
        data = rewrite_xlsx({'xl/worksheets/sheet1.xml': re.sub(br'<dimension [^>]+/>', b'<dimension ref="A1:SR1048576"/>', xml)})
        self.assertEqual(parse_task_file(uploaded(data)).rows[0].task.number, ROW[1])

    def test_xml_dtd_and_local_external_entities_are_rejected(self):
        # No actual secret path or external service is used.
        with tempfile.NamedTemporaryFile() as sentinel:
            sentinel.write(b'SYNTHETIC_NOT_FOR_IMPORT')
            sentinel.flush()
            xml = (f'<!DOCTYPE workbook [<!ENTITY xxe SYSTEM "file://{sentinel.name}">]>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<sheets>&xxe;</sheets></workbook>').encode()
            with self.assertRaises(ValidationError):
                parse_task_file(uploaded(rewrite_xlsx({'xl/workbook.xml': xml})))

    def test_zip_expansion_and_entry_count_limits(self):
        for data in (expansion_bomb(), rewrite_xlsx({f'unused/{index}': b'' for index in range(1025)})):
            with self.assertRaises(ValidationError):
                parse_task_file(uploaded(data))

    def test_child_has_real_limits_and_no_database_or_secret_environment(self):
        with patch.dict(os.environ, {'POSTGRES_PASSWORD': 'synthetic-not-for-child', 'DJANGO_SECRET_KEY': 'synthetic-parent-key'}):
            parsed, metrics = parse_import(uploaded(csv_file(79), 'csv'), with_metrics=True)
        self.assertEqual(parsed.total_rows, 79)
        self.assertNotEqual(metrics['pid'], os.getpid())
        self.assertEqual(metrics['address_space_limit'], 256 * 1024 * 1024)
        self.assertEqual(metrics['cpu_limit'], 10)
        self.assertEqual(metrics['output_limit'], 16 * 1024 * 1024)
        self.assertEqual(metrics['database_engine'], 'django.db.backends.dummy')
        self.assertFalse(metrics['sensitive_environment_present'])

    def test_timeout_kills_and_reaps_child_and_releases_admission(self):
        real_popen = subprocess.Popen
        children = []
        def start(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with patch('poker.import_process.WALL_SECONDS', 0.001), patch('subprocess.Popen', side_effect=start):
            with self.assertRaisesMessage(ValidationError, 'предел ресурсов'):
                parse_task_file(uploaded(csv_file(), 'csv'))
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(parse_task_file(uploaded(csv_file(), 'csv')).total_rows, 1)

    def test_cpu_and_address_space_are_enforced_by_the_operating_system(self):
        setup = 'from poker.import_worker import apply_limits; apply_limits(memory_bytes=64*1024*1024,cpu_seconds=1); '
        cpu = subprocess.run([sys.executable, '-c', setup + 'exec("while True: pass")'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        self.assertIn(cpu.returncode, (-signal.SIGXCPU, -signal.SIGKILL))
        memory = subprocess.run([sys.executable, '-c', setup +
            '\ntry:\n data=bytearray(256*1024*1024)\nexcept MemoryError:\n print("MEMORY_BOUND")'],
            capture_output=True, text=True, timeout=8)
        self.assertEqual(memory.returncode, 0, memory.stderr)
        self.assertIn('MEMORY_BOUND', memory.stdout)

    def test_result_file_limit_is_enforced_by_the_operating_system(self):
        program = ('import os; from poker.import_worker import apply_limits; '
                   'apply_limits(result_bytes=4096); os.write(1,b"x"*8192); os.write(1,b"x")')
        with os.fdopen(os.memfd_create('synthetic-output-limit', os.MFD_CLOEXEC), 'w+b') as output:
            child = subprocess.run([sys.executable, '-c', program], stdout=output,
                                   stderr=subprocess.DEVNULL, timeout=8)
            self.assertLessEqual(os.fstat(output.fileno()).st_size, 4096)
            self.assertIn(child.returncode, (1, -signal.SIGXFSZ))


class ImportHttpSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('resource-import-owner')
        cls.project = Project.objects.create(owner=cls.owner, name='Synthetic imports')
        cls.room = VotingSession.objects.create(project=cls.project, name='Synthetic room')
        cls.sprint = Sprint.objects.create(project=cls.project, name='Synthetic sprint')

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.owner)
        self.client.get(self.project.get_absolute_url(), secure=True)
        self.urls = [reverse('poker:task_import_file', args=[self.project.pk]),
                     reverse('poker:session_import_file', args=[self.room.pk]),
                     reverse('poker:sprint_import', args=[self.sprint.pk])]

    def post(self, url, data):
        return self.client.post(url, {'csrfmiddlewaretoken': self.client.cookies['csrftoken'].value, **data},
                                secure=True, HTTP_ORIGIN='https://testserver')

    def test_malformed_files_return_400_without_partial_writes_on_all_three_forms(self):
        for url in self.urls:
            response = self.post(url, {'task_file': uploaded(bad_shared_string())})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(Task.objects.exists())
            self.assertFalse(self.room.queue_items.exists())
            self.assertFalse(self.sprint.sprint_tasks.exists())
        self.assertEqual(self.post(self.urls[0], {'task_file': uploaded(csv_file(), 'csv')}).status_code, 302)
        self.assertEqual(Task.objects.count(), 1)

    def test_deep_sprint_list_is_a_row_conflict_instead_of_a_server_error(self):
        stream = StringIO(newline='')
        writer = csv.writer(stream, delimiter=';')
        writer.writerow([*HEADERS, 'Спринты'])
        writer.writerow([*ROW, '[' * 1100 + '0' + ']' * 1100])
        response = self.post(self.urls[0], {'task_file': uploaded(stream.getvalue().encode('utf-8-sig'), 'csv')})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Task.objects.exists())

    def test_streaming_upload_limit_returns_413_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
                FILE_UPLOAD_MAX_MEMORY_SIZE=0, FILE_UPLOAD_TEMP_DIR=directory):
            response = self.post(self.urls[0], {'task_file': uploaded(b'x' * (MAX_UPLOAD_BYTES + 1))})
            self.assertEqual(response.status_code, 413)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(Task.objects.exists())

    def test_multiple_files_are_rejected_before_csrf_and_application_parsing(self):
        response = self.client.post(self.urls[0], {'task_file': [uploaded(b'a'), uploaded(b'b')]}, secure=True)
        self.assertEqual(response.status_code, 413)
        self.assertFalse(Task.objects.exists())

    def test_content_length_limit_does_not_consume_body(self):
        request = RequestFactory().post('/login/', data=b'synthetic', content_type='application/octet-stream')
        request.META['CONTENT_LENGTH'] = str(MAX_REQUEST_BYTES + 1)
        with patch.object(request, 'read', side_effect=AssertionError('body must not be read')):
            response = UploadLimitMiddleware(lambda r: None).process_request(request)
        self.assertEqual(response.status_code, 413)

    def test_busy_import_does_not_queue_and_health_and_subsequent_import_work(self):
        with import_slot():
            pass  # Create the private lock directory.
        path = Path(tempfile.gettempdir()) / f'score-it-imports-{os.geteuid()}' / 'active.lock'
        with path.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for url in self.urls:
                response = self.post(url, {'task_file': uploaded(csv_file(), 'csv')})
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response['Retry-After'], '3')
            self.assertEqual(self.client.get('/healthz/').status_code, 200)
            self.assertFalse(Task.objects.exists())
        self.assertEqual(self.post(self.urls[0], {'task_file': uploaded(csv_file(), 'csv')}).status_code, 302)

    def test_valid_size_upload_still_requires_csrf(self):
        self.assertEqual(self.client.post(self.urls[0], {'task_file': uploaded(csv_file(), 'csv')}, secure=True).status_code, 403)
        self.assertFalse(Task.objects.exists())
