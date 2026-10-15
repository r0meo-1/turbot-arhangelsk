"""Transfer compression for explicitly public Mini App code and styles only."""
import gzip

from flask import request


def compress_public_asset(response):
    """Negotiate gzip without touching HTML, launch parameters or API responses."""
    response.vary.add('Accept-Encoding')
    # A weak validator refers to the same source across wire encodings.
    etag, _ = response.get_etag()
    if etag:
        response.set_etag(etag, weak=True)
    if (response.status_code != 200 or request.method == 'HEAD'
            or request.accept_encodings['gzip'] <= 0):
        return response
    response.direct_passthrough = False
    source = response.get_data()
    packed = gzip.compress(source, compresslevel=6, mtime=0)
    if len(packed) < len(source):
        response.set_data(packed)
        response.headers['Content-Encoding'] = 'gzip'
        response.headers.pop('Accept-Ranges', None)
    return response
