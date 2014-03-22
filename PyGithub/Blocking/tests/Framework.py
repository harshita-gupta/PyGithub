# -*- coding: utf-8 -*-

# Copyright 2013-2014 Vincent Jacques <vincent@vincent-jacques.net>

from __future__ import print_function

import logging
import unittest
import os.path
import io
import zlib
import json
import inspect
import urlparse
import sys

import requests
import MockMockMock

import PyGithub.Blocking.Builder

try:
    import GithubCredentials
    login = GithubCredentials.login
    password = GithubCredentials.password
    # @todoBeta When PyGithub supports creating authorizations, use it :)
    token_without_scopes = GithubCredentials.token_without_scopes
    token_with_scopes = GithubCredentials.token_with_scopes
except ImportError:
    login = "login"
    password = "password"
    token_without_scopes = "token_without_scopes"
    token_with_scopes = "token_with_scopes"


def UsesForgedData(c):
    c.dataSuffix = "ForgedData"
    return c


def UsesSpecificData(c):
    c.dataSuffix = "SpecificData"
    return c


def SharesDataWith(source):
    def f(target):
        target.dataSource = source
        return target
    return f


def _parseUrl(url):
    parse = urlparse.urlparse(url)
    return {
        "scheme": parse.scheme,
        "netloc": parse.netloc,
        "path": parse.path,
        "query": dict(urlparse.parse_qsl(parse.query)),
    }


def sanitizeRequest(request):
    if "Authorization" in request.headers:
        auth = request.headers["Authorization"]
        if auth.startswith("Basic ") and auth.endswith("="):
            request.headers["Authorization"] = "Basic removed="
        elif auth.startswith("token "):
            request.headers["Authorization"] = "token removed"
        else:
            raise Exception("Unexpected auth method: " + auth)


def createTestCase(builder):
    class TestCase(unittest.TestCase):
        dataSuffix = "RecordedData"

        def setUp(self):
            unittest.TestCase.setUp(self)

            self.manualMocks = MockMockMock.Engine()
            self.mocksForRequests = MockMockMock.Engine()

            self.__setUpRecordMode()
            self.__setUpLogging()

            self.g = builder.UserAgent("jacquev6/PyGithub/2; UnitTests recorder").Build()

            self.__hackRequestsSession(self.g.Session._Session__requestsSession)

        def __setUpRecordMode(self):
            method = getattr(self, self._testMethodName)
            methodForData = getattr(method, "dataSource", method)
            pythonFileName = inspect.getfile(self.__class__)
            directory = os.path.join(os.path.dirname(pythonFileName), os.path.splitext(os.path.basename(pythonFileName))[0] + "_" + self.dataSuffix)
            self.fileName = os.path.join(directory, self.__class__.__name__ + "." + methodForData.__name__ + ".json")
            self.recordMode = not os.path.exists(self.fileName)

        def __setUpLogging(self):
            self.__log = logging.getLogger("PyGithub")

            for handler in self.__log.handlers:
                self.__log.removeHandler(handler)

            if self.recordMode:
                self.__log.setLevel(logging.DEBUG)
                handler = logging.StreamHandler()
            else:
                self.__log.setLevel(logging.INFO)
                self.__logHandlerMock = self.manualMocks.create("log")
                handler = self.__logHandlerMock.object

            self.__log.addHandler(handler)

        def tearDown(self):
            unittest.TestCase.tearDown(self)

            self.manualMocks.tearDown()
            self.mocksForRequests.tearDown()

            self.__tearDownRecordMode()

        def __tearDownRecordMode(self):
            if self.recordMode:
                interractions = []
                for record in self.mocksForRequests.records:
                    response = record["return"]
                    request = response.request
                    sanitizeRequest(request)
                    requestBody = request.body
                    try:
                        requestBody = json.loads(requestBody)
                    except:
                        pass
                    responseBody = response.content
                    try:
                        responseBody = json.loads(responseBody)
                    except:
                        pass
                    interractions.append({
                        "request": {
                            'body': requestBody,
                            'headers': dict(request.headers),
                            'verb': request.method,
                            'url': _parseUrl(request.url),
                        },
                        "response": {
                            'body': responseBody,
                            'headers': dict(response.headers),
                            'status': response.status_code,
                        },
                    })

                try:
                    if not os.path.exists(os.path.dirname(self.fileName)):
                        os.makedirs(os.path.dirname(self.fileName))
                    with open(self.fileName, "w") as f:
                        json.dump(interractions, f, sort_keys=True, indent=4, separators=(',', ': '))
                except Exception:
                    if os.path.exists(self.fileName):
                        os.unlink(self.fileName)
                    raise

        def __hackRequestsSession(self, session):
            adapter = self.mocksForRequests.create("adapter")

            if self.recordMode:
                for k, v in session.adapters.iteritems():
                    session.mount(k, adapter.record(v))
            else:
                with open(self.fileName) as f:
                    records = json.load(f)
                for record in records:
                    adapter.expect.send.withArguments(
                        self.RequestMatcher(**record["request"])
                    ).andReturn(
                        self.__rebuildResponse(**record["response"])
                    )

                for k, v in session.adapters.iteritems():
                    session.mount(k, adapter.object)

        def __rebuildResponse(self, status, headers, body):
            response = requests.Response()
            response.status_code = status
            response.headers = requests.structures.CaseInsensitiveDict(headers)
            if not isinstance(body, str):
                body = json.dumps(body, sort_keys=True)
            if sys.hexversion >= 0x03000000:
                body = bytes(body, encoding="utf8")
            if "content-encoding" in headers:
                assert headers["content-encoding"] == "gzip"
                c = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
                body = c.compress(body) + c.flush()
            response.raw = requests.packages.urllib3.HTTPResponse(
                io.BytesIO(body),
                status=status,
                headers=headers,
                preload_content=False
            )
            return response

        class RequestMatcher:
            def __init__(self, verb, url, headers, body):
                self.__verb = verb
                self.__url = url
                self.__headers = headers
                self.__body = body

            def __call__(self, args, kwds):
                request = args[0]
                sanitizeRequest(request)
                return self.check(request)

            def check(self, request):
                requestBody = request.body
                try:
                    requestBody = json.loads(requestBody)
                except:
                    pass
                return (
                    request.method == self.__verb
                    and _parseUrl(request.url) == self.__url
                    and request.headers == self.__headers
                    and requestBody == self.__body
                )

        def expectLog(self, level, *messages):
            if not self.recordMode:
                def checkLogRecord(args, kwds):
                    (logRecord,) = args
                    if logRecord.levelno == level and str(logRecord.msg) in messages:
                        return True
                    else:
                        print()
                        print("checkLogRecord received")
                        print(logRecord.levelno)
                        print(logRecord.msg)
                        print("instead of")
                        print(level)
                        print("\n".join(messages))
                        print()
                self.__logHandlerMock.expect.level.andReturn(logging.DEBUG)
                self.__logHandlerMock.expect.handle.withArguments(checkLogRecord)

    return TestCase


class SimpleLoginTestCase(createTestCase(PyGithub.Blocking.Builder.Builder().Login(login, password).PerPage(4))):
    pass


class SimpleAnonymousTestCase(createTestCase(PyGithub.Blocking.Builder.Builder().PerPage(4))):
    pass


class SimpleOAuthWithoutScopesTestCase(createTestCase(PyGithub.Blocking.Builder.Builder().OAuth(token_without_scopes).PerPage(4))):
    pass


class SimpleOAuthWithScopesTestCase(createTestCase(PyGithub.Blocking.Builder.Builder().OAuth(token_with_scopes).PerPage(4))):
    pass