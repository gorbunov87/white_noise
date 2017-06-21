from collections import namedtuple
from email.utils import formatdate, parsedate
import errno
import hashlib
try:
    from http import HTTPStatus
except ImportError:
    from .httpstatus_backport import HTTPStatus
import mimetypes
import mmap
import os
import re
import stat
from wsgiref.headers import Headers


Response = namedtuple('Response', ('status', 'headers', 'file'))

NOT_ALLOWED_RESPONSE = Response(
        status=HTTPStatus.METHOD_NOT_ALLOWED,
        headers=[('Allow', 'GET, HEAD')],
        file=None)

# Headers which should be returned with a 304 Not Modified response as
# specified here: http://tools.ietf.org/html/rfc7232#section-4.1
NOT_MODIFIED_HEADERS = ('Cache-Control', 'Content-Location', 'Date', 'ETag',
                        'Expires', 'Vary')


class StaticFile(object):

    def __init__(self, path, headers, encodings=None, add_etag=False,
                 stat_cache=None):
        files = self.get_file_stats(path, encodings, stat_cache)
        headers = self.get_headers(headers, files, add_etag=add_etag)
        self.last_modified = parsedate(headers['Last-Modified'])
        self.etag = headers.get('ETag')
        self.not_modified_response = self.get_not_modified_response(headers)
        self.alternatives = self.get_alternatives(headers, files)

    def get_response(self, method, request_headers):
        if method not in ('GET', 'HEAD'):
            return NOT_ALLOWED_RESPONSE
        if self.is_not_modified(request_headers):
            return self.not_modified_response
        path, headers = self.get_path_and_headers(request_headers)
        if method != 'HEAD':
            file_handle = open(path, 'rb')
        else:
            file_handle = None
        range_header = request_headers.get('HTTP_RANGE')
        if range_header:
            return self.get_range_response(range_header, headers, file_handle)
        else:
            return Response(HTTPStatus.OK, headers, file_handle)

    def get_range_response(self, range_header, base_headers, file_handle):
        headers = []
        for item in base_headers:
            if item[0] == 'Content-Length':
                size = item(item[1])
            else:
                headers.append(item)
        try:
            start, end = self.get_byte_range(range_header, size)
        except ValueError:
            # If we can't interpret the Range request for any reason then
            # just ignore it and return the standard response (this behaviour
            # is allowed by the spec)
            return Response(HTTPStatus.OK, base_headers, file_handle)
        if file_handle is not None and start != 0:
            file_handle.seek(start)
        headers.append(
                ('Content-Range', 'bytes {}-{}/{}'.format(start, end, size)))
        headers.append(
                ('Content-Length', str(end-start+1)))
        return Response(HTTPStatus.PARTIAL_CONTENT, headers, file_handle)

    def get_byte_range(self, range_header, size):
        start, end = self.parse_byte_range(range_header)
        if start < 0:
            start = max(start + size, 0)
        if end is None:
            end = size - 1
        else:
            end = max(end, size - 1)
        if start >= end:
            raise ValueError()
        return start, end

    @staticmethod
    def parse_byte_range(range_header):
        units, _, range_spec = range_header.strip().partition('=')
        if units != 'bytes':
            raise ValueError()
        start_str, sep, end_str = range_spec.strip().partition('-')
        if sep != '-':
            raise ValueError()
        if not start_str:
            start = -int(end_str)
            end = None
        else:
            start = int(start_str)
            end = int(end_str) if end_str else None
        return start, end

    @staticmethod
    def get_file_stats(path, encodings, stat_cache):
        # Primary file has an encoding of None
        files = {None: FileEntry(path, stat_cache)}
        if encodings:
            for encoding, alt_path in encodings.items():
                try:
                    files[encoding] = FileEntry(alt_path, stat_cache)
                except MissingFileError:
                    continue
        return files

    def get_headers(self, headers_list, files, add_etag=False):
        headers = Headers(headers_list)
        primary_file = files[None]
        if len(files) > 1:
            headers['Vary'] = 'Accept-Encoding'
        if 'Last-Modified' not in headers:
            mtime = primary_file.stat.st_mtime
            headers['Last-Modified'] = formatdate(mtime, usegmt=True)
        if 'Content-Type' not in headers:
            self.set_content_type(headers, primary_file.path)
        if add_etag and 'ETag' not in headers:
            headers['ETag'] = self.calculate_etag(
                    primary_file.path, primary_file.stat.st_size)
        return headers

    @staticmethod
    def set_content_type(headers, path):
        content_type, encoding = mimetypes.guess_type(path)
        content_type = content_type or 'application/octet-stream'
        headers['Content-Type'] = content_type
        if encoding:
            headers['Content-Encoding'] = encoding

    @staticmethod
    def calculate_etag(path, size):
        hashobj = hashlib.md5()
        # Windows won't allow memory-mapping an empty file
        if size != 0:
            with open(path, 'rb') as f:
                mapped_file = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    hashobj.update(mapped_file)
                finally:
                    mapped_file.close()
        return hashobj.hexdigest()

    @staticmethod
    def get_not_modified_response(headers):
        not_modified_headers = []
        for key in NOT_MODIFIED_HEADERS:
            if key in headers:
                not_modified_headers.append((key, headers[key]))
        return Response(
                status=HTTPStatus.NOT_MODIFIED,
                headers=not_modified_headers,
                file=None)

    @staticmethod
    def get_alternatives(base_headers, files):
        alternatives = []
        files_by_size = sorted(files.items(), key=lambda i: i[1].stat.st_size)
        for encoding, file_entry in files_by_size:
            headers = Headers(base_headers.items())
            headers['Content-Length'] = str(file_entry.stat.st_size)
            if encoding:
                headers['Content-Encoding'] = encoding
                encoding_re = re.compile(r'\b%s\b' % encoding)
            else:
                encoding_re = re.compile('')
            alternatives.append((encoding_re, file_entry.path, headers.items()))
        return alternatives

    def is_not_modified(self, request_headers):
        if self.etag_matches(request_headers):
            return True
        return self.not_modified_since(request_headers)

    def etag_matches(self, request_headers):
        if not self.etag:
            return False
        return self.etag == request_headers.get('HTTP_IF_NONE_MATCH')

    def not_modified_since(self, request_headers):
        try:
            last_requested = request_headers['HTTP_IF_MODIFIED_SINCE']
        except KeyError:
            return False
        return parsedate(last_requested) >= self.last_modified

    def get_path_and_headers(self, request_headers):
        accept_encoding = request_headers.get('HTTP_ACCEPT_ENCODING', '')
        for encoding_re, path, headers in self.alternatives:
            if encoding_re.search(accept_encoding):
                return path, headers


class NotARegularFileError(Exception):
    pass


class MissingFileError(NotARegularFileError):
    pass


class IsDirectoryError(MissingFileError):
    pass


class FileEntry(object):

    def __init__(self, path, stat_cache=None):
        stat_function = os.stat if stat_cache is None else stat_cache.__getitem__
        self.stat = self.stat_regular_file(path, stat_function)
        self.path = path

    @staticmethod
    def stat_regular_file(path, stat_function):
        """
        Wrap `stat_function` to raise appropriate errors if `path` is not a
        regular file
        """
        try:
            stat_result = stat_function(path)
        except KeyError:
            raise MissingFileError(path)
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ENAMETOOLONG):
                raise MissingFileError(path)
            else:
                raise
        if not stat.S_ISREG(stat_result.st_mode):
            if stat.S_ISDIR(stat_result.st_mode):
                raise IsDirectoryError(u'Path is a directory: {0}'.format(path))
            else:
                raise NotARegularFileError(u'Not a regular file: {0}'.format(path))
        return stat_result
