"""Reject oversized uploads while streaming, before CSRF/form processing."""

from django.core.exceptions import RequestDataTooBig, TooManyFilesSent
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 11 * 1024 * 1024


def rejected():
    response = HttpResponse('Слишком большой запрос. Загрузите один файл до 10 МБ.',
                            status=413, content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    return response


class BoundedUploadHandler(FileUploadHandler):
    chunk_size = 64 * 1024

    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0
        self.files = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.files += 1
        if self.files > 1:
            self.request._upload_limit_exceeded = True
            raise StopUpload(connection_reset=True)

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > MAX_UPLOAD_BYTES:
            self.request._upload_limit_exceeded = True
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None


class UploadLimitMiddleware(MiddlewareMixin):
    def process_request(self, request):
        length = request.META.get('CONTENT_LENGTH', '')
        try:
            if length and int(length) > MAX_REQUEST_BYTES:
                return rejected()
        except (TypeError, ValueError):
            return HttpResponse('Некорректная длина запроса.', status=400)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method == 'POST' and request.content_type == 'multipart/form-data':
            try:
                request.POST  # Streaming handler runs before CSRF consumes POST.
            except (RequestDataTooBig, TooManyFilesSent):
                return rejected()
            if getattr(request, '_upload_limit_exceeded', False):
                return rejected()
