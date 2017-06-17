from __future__ import absolute_import

from django.http import FileResponse

from whitenoise.django import DjangoWhiteNoise

from .static_file import StaticFile


class WhiteNoiseMiddleware(DjangoWhiteNoise):
    """
    Wrap DjangoWhiteNoise to allow it to function as Django middleware, rather
    than WSGI middleware

    This functions as both old- and new-style middleware, so can be included in
    either MIDDLEWARE or MIDDLEWARE_CLASSES.
    """

    def __init__(self, get_response=None):
        self.get_response = get_response
        # Pass None for `application`
        super(WhiteNoiseMiddleware, self).__init__(None)

    def __call__(self, request):
        response = self.process_request(request)
        if response is None:
            response = self.get_response(request)
        return response

    def process_request(self, request):
        if self.autorefresh:
            static_file = self.find_file(request.path_info)
        else:
            static_file = self.files.get(request.path_info)
        if static_file is not None:
            return self.serve(static_file, request)

    @staticmethod
    def serve(static_file, request):
        response = static_file.get_response(request.method, request.META)
        status = int(response.status)
        http_response = FileResponse(response.file or (), status=status)
        # Remove default content-type
        del http_response['content-type']
        for key, value in response.headers:
            http_response[key] = value
        return http_response


class StaticFileView(StaticFile):
    """
    Wraps the StaticFile class so it behaves as a Django view callable
    """
    def __call__(self, request):
        return WhiteNoiseMiddleware.serve(self, request)
