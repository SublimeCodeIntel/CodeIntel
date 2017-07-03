#!/usr/bin/env python

"""Generic out-of-process codeintel test"""

from __future__ import absolute_import
import hashlib
import json
import logging
import os
import os.path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import six

log = logging.getLogger("test.codeintel.oop")

class OOPTestSuite(unittest.TestSuite):
    """
    TestSuite wrapper to use one codeintel database for all tests
    """
    _db_dir = None

    def __init__(self, *args, **kwargs):
        self._db_dir = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                    "tmp-db-base-dir"))
        unittest.TestSuite.__init__(self, *args, **kwargs)

    def run(self, *args, **kwargs):
        for test in self:
            if isinstance(test, OOPTestCase):
                test._db_dir = self._db_dir
        return unittest.TestSuite.run(self, *args, **kwargs)

test_suite_class = OOPTestSuite

class OOPTestCase(unittest.TestCase):
    maxDiff = 4096
    _db_dir = None

    def __init__(self, *args, **kwargs):
        unittest.TestCase.__init__(self, *args, **kwargs)
        self.conn = None
        self.stdi = None
        self.stdo = None
        self.proc = None

    def _fixup_for_dbgp(self, argv, env):
        """
        This function is used to fix things up for when running the tests under
        DBGP; we force the remote process to connect to the same DBGP server
        (so we can debug things with Komodo).
        """
        if not "DBGP_COOKIE" in os.environ:
            return [] # not running under DBGP
        import dbgp.client
        client = dbgp.client.getClientForThread()
        dbgpbin = os.path.join(sys.modules["dbgp"].__path__[0],
                               "../../bin/pydbgp.py")
        argv[0:0] = ["-u", os.path.normpath(dbgpbin), "-d",
                     "%s:%s" % (client.socket.hostname, client.socket.port),
                     "-l", "WARN"]
        del env["DBGP_COOKIE"]
        # We seem to loose the PYTHONPATH here...
        komodo_dir = os.path.join(os.path.dirname(__file__),
                                  "../../../..")
        pythonpath = [os.path.normpath(os.path.join(komodo_dir, s))
                        for s in ("src/codeintel/lib",
                                  "src/codeintel/support",
                                  "src/python-sitelib",
                                  "src/find",
                                  "util",
                                  "contrib/smallstuff",
                                  "src/dbgp/PyDBGP")]
        pythonpath[0:0] = [_f for _f in env.get("PYTHONPATH", "").split(os.pathsep) if _f]
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    def setUp(self):
        self.conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.conn.bind(("localhost", 0))
        self.conn.listen(1)

        bin = os.path.join(os.path.dirname(__file__),
                            "../oop.py")
        log.debug("bin: %s", bin)
        argv = [os.path.abspath(os.path.normpath(bin)),
                "--connect", "%s:%s" % self.conn.getsockname()]
        for log_name in logging.Logger.manager.loggerDict.keys():
            if logging.getLogger(log_name).level is logging.NOTSET:
                continue
            argv.extend(["--log-level", "%s:%s" % (log_name, logging.getLogger(log_name).getEffectiveLevel())])

        if self._db_dir is not None:
            argv.extend(["--database-dir", self._db_dir])
        env = dict(os.environ)
        self._fixup_for_dbgp(argv, env)
        argv.insert(0, sys.executable)

        try:
            bkconfig = sys.modules.get('bkconfig')
            if bkconfig is None:
                import imp
                abspath, dirname, join = os.path.abspath, os.path.dirname, os.path.join
                src_root = abspath(join(dirname(__file__), "..", "..", ".."))
                iinfo = imp.find_module("bkconfig", [src_root])
                bkconfig = imp.load_module("bkconfig", *iinfo)
            argv.extend(["--import-path", bkconfig.komodoPythonUtilsDir])
        except ImportError:
            pass

        log.debug("Running %s", " ".join(argv))
        self.proc = subprocess.Popen(argv, env=env)

        self.socket = self.conn.accept()[0]
        self.stdo = self.socket.makefile('wb', 0)
        self.stdi = self.socket.makefile('rb', 0)

        self.proc.poll()
        self.assertIsNone(self.proc.returncode,
                          "Child process died prematurely")
        buf = b''
        while len(buf) < 3:
            r = self.stdi.read(3 - len(buf))
            if not r:
                break
            buf += r
        self.assertEqual(buf, b'2{}')

    def tearDown(self):
        if self.stdi:
            self.stdi.close()
        if self.stdo:
            self.stdo.close()
        if self.socket:
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        if self.proc is not None:
            if self.proc.returncode is None:
                def timeout():
                    import time
                    time.sleep(10)
                    try:
                        self.proc.kill()
                    except:
                        pass
                # Set up a timeout thread in case self.proc.wait() hangs
                thread = threading.Thread(target=timeout)
                thread.daemon = True
                thread.start()
                # Try to let the child exit nicely
                self.proc.wait()
            self.assertEqual(self.proc.returncode, 0,
                             "Child process returned an error: %s" %
                                (self.proc.returncode,))

    def send(self, **kwargs):
        data = json.dumps(kwargs, separators=(",", ":"))
        log.debug("writing: [%s]", data)
        self.stdo.write(b"%d%s" % (len(data), data.encode('utf-8')))

    def _test_with_commands(self, commands, ignore_unsolicited=True):
        """
        A serial test where we run a bunch of commands serially and check the
        results.  The commands should be an iterable, where each item is a tuple
        of two dicts; the first dict is the command to send, and the second dict
        is the expected response.  Request IDs are checked automatically and do
        not need to be specified.  Responses are assumed to expect success
        unless explicitly marked.  If no response checking is desired, each
        element of the iterable should just be a dict of the request, instead of
        a tuple.
        @param commands {list of tuple} Commands to send; each entry is a tuple
            of (command to send), (expected response)
        @param ignore_unsolicited {bool} If true, ignore unsolicited responses
            (i.e. responses with no request id)
        """
        for i, command in enumerate(commands):
            try:
                command, expected = command
            except ValueError:
                expected = None
            command = dict(command, req_id=str(i))
            self.send(**command)
            buf = b''
            while True:
                ch = self.stdi.read(1)
                if ch == b'{':
                    length = int(buf, 10)
                    buf = ch + self.stdi.read(length - 1)
                    result = json.loads(buf)
                    log.debug("receive: %r", result)
                    if ignore_unsolicited:
                        if "req_id" not in result:
                            buf = b''
                            continue
                    break
                else:
                    self.assertIn(ch, b'0123456789',
                                  "Invalid data: %s" % (buf + ch,))
                    buf += ch
            if expected is not None:
                expected = dict(expected)
                expected[u"req_id"] = u"%d" % i
                if not u"success" in expected:
                    expected[u"success"] = True
                self.assertEqual(expected, result)

class BasicTestCase(OOPTestCase):
    def test_object_members(self):
        text = u"import array\narray."
        self._test_with_commands([
            ({u"command": u"trg-from-pos",
              u"path": u"<Unsaved>/1",
              u"language": u"Python",
              u"text": text,
              u"pos": 1},
             {u"trg": None}),
            ({u"command": u"trg-from-pos",
              u"path": u"<Unsaved>/1",
              u"language": u"Python",
              u"text": text,
              u"pos": 19},
             {u"trg": {u"form": 0,
                       u"type": u"object-members",
                       u"lang": u"Python",
                       u"pos": 19,
                       u"implicit": True,
                       u"length": 1,
                       u"extentLength": 0,
                       u"retriggerOnCompletion": False,
                       u"path": u"<Unsaved>/1",
                      }}),
            ({u"command": u"eval",
              u"text": text,
              u"trg": {u"form": 0,
                       u"type": u"object-members",
                       u"lang": u"Python",
                       u"pos": 19,
                       u"implicit": True,
                       u"length": 1,
                       u"extentLength": 0,
                       u"retriggerOnCompletion": False,
                       u"path": u"<Unsaved>/1",
                      }},
             {u"cplns": [[u"class", u"array"],
                         [u"class", u"ArrayType"],
                         [u"variable", u"__doc__"]],
              u"retrigger": False}),
            {u"command": u"quit"},
        ])

class CommandExtensionTestCase(OOPTestCase):
    """Registering command extensions"""
    def test_commandExtensionRegistation(self):
        self._test_with_commands([
            ({"command": "load-extension",
              "module-path": os.path.dirname(os.path.abspath(__file__)),
              "module-name": __name__},
             {}), # must succeed
            ({"command": "extension-command"},
             {"extension-result": True}),
            ])

def registerExtension():
    from codeintel2.oop.driver import CommandHandler, Driver
    class DummyHandler(CommandHandler):
        supportedCommands = ["extension-command"]
        def __init__(self):
            self._askedRequest = None
        def canHandleRequest(self, request):
            if self._askedRequest is not None:
                raise AssertionError("Duplicate canHandleRequest call")
            if request.command == "extension-command":
                self._askedRequest = request
                return True
            raise AssertionError("Invalid command %s" % request.command)
        def handleRequest(self, request, driver):
            if self._askedRequest is not request:
                raise AssertionError("Unexpected request %r (expected %r)" %
                                     (request, self._askedRequest))
            self._askedRequest = {} # unique thing to fail future comparisons
            log.debug("Extension handling request!!!")
            driver.send(request=request, **{"extension-result": True})
    Driver.registerCommandHandler(DummyHandler())
