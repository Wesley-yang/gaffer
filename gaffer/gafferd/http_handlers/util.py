# -*- coding: utf-8 -
#
# this file is part of gaffer. see the notice for more information.

try:
    import httplib
except ImportError:
    import http.client as httplib
import json

import pyuv
from tornado.web import RequestHandler, asynchronous, HTTPError
from ..keys import DummyKey, Key, KeyNotFound

ACCESS_CONTROL_HEADERS = ['X-Requested-With',
            'X-HTTP-Method-Override', 'Content-Type', 'Accept',
            'Authorization']

CORS_HEADERS = {
    'Access-Control-Allow-Methods' : 'POST, GET, PUT, DELETE, OPTIONS',
    'Access-Control-Max-Age'       : '86400', # 24 hours
    'Access-Control-Allow-Headers' : ", ".join(ACCESS_CONTROL_HEADERS),
    'Access-Control-Allow-Credentials': 'true'
}


class CorsHandler(RequestHandler):

    @asynchronous
    def options(self, *args, **kwargs):
        self.preflight()
        self.set_status(204)
        self.finish()

    def preflight(self):
        origin = self.request.headers.get('Origin', '*')

        if origin == 'null':
            origin = '*'

        self.set_header('Access-Control-Allow-Origin', origin)
        for k, v in CORS_HEADERS.items():
            self.set_header(k, v)

    def get_error_html(self, status_code, **kwargs):
        self.set_header("Content-Type", "application/json")

        if status_code == 404:
            resp = {"error": 404, "reason": "not_found"}
        elif status_code == 401:
            resp = {"error": 401, "reason": "unauthorized"}
        elif status_code == 403:
            resp = {"error": 403, "reason": "forbidden"}
        else:
            resp = {"error": status_code,
                    "reason": httplib.responses[status_code]}


        if self.settings.get("debug") and "exc_info" in kwargs:
            exc_info = traceback.format_exception(*kwargs["exc_info"])
            resp['exc_info'] = exc_info

        return json.dumps(resp)


class CorsHandlerWithAuth(CorsHandler):

    def prepare(self):
        api_key = self.request.headers.get('X-Api-Key', None)
        require_key = self.settings.get('require_key', False)
        key_mgr = self.settings.get('key_mgr')
        self.api_key = DummyKey()

        # if the key API is enable start to use it
        if require_key:
            if api_key is not None:
                try:
                    self.api_key = Key.load(key_mgr.get_key(api_key))
                except KeyNotFound:
                    raise HTTPError(403, "key %s doesn't exist",api_key)
                self._check_auth()
            else:
                raise HTTPError(401)

    def _check_auth(self):
        return


class AsyncHandler(CorsHandler):

    def initialize(self, *args, **kwargs):
        self._heartbeat = None
        self._feed = None
        self._closed = False
        self._source = None
        self._pattern = None

    def setup_stream(self, feed, m, heartbeat):
        self._feed = feed

        self.setup_heartbeat(heartbeat, m)
        if feed == "eventsource":
            self.set_header("Content-Type", "text/event-stream")
        else:
            self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-cache")

    def setup_heartbeat(self, heartbeat, m):
        # set heartbeta
        if heartbeat.lower() == "true":
            heartbeat = 60
        else:
            try:
                heartbeat = int(heartbeat)
            except TypeError:
                heartbeat = False

        if heartbeat:
            self._heartbeat = pyuv.Timer(m.loop)
            self._heartbeat.start(self._on_heartbeat, heartbeat,
                    heartbeat)
            self._heartbeat.unref()

    def write_chunk(self, data):
        chunk = "".join(("%X\r\n" % len(data), data, "\r\n"))
        self.write(chunk)

    def send_not_found(self):
        self.set_status(404)
        self.write({"error": "not_found"})
        self.finish()

    def _on_heartbeat(self, handle):
        self.write("\n")

    def _on_event(self, evtype, msg):
        if self._feed == "eventsource":
            event = ["event: %s" % evtype,
                    "data: %s" % json.dumps(msg), ""]
            self.write("\r\n".join(event))
            self.flush()
        else:
            self.write("%s\r\n" % json.dumps(msg))
            self.flush()
            self.finish()

    def handle_disconnect(self):
        self._closed = True
        self._handle_disconnect()
        if self._heartbeat is not None:
            self._heartbeat.close()

    def on_finish(self):
        self.handle_disconnect()

    def on_close_connection(self):
        self.handle_disconnect()
