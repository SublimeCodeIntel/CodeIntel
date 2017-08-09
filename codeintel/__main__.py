# -*- coding: utf-8 -*-
# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License
# Version 1.1 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS"
# basis, WITHOUT WARRANTY OF ANY KIND, either express or implied. See the
# License for the specific language governing rights and limitations
# under the License.
#
# The Original Code is ActiveState Software Inc code.
# Portions created by German M. Bravo (Kronuz) are Copyright (C) 2015.
#
# Contributor(s):
#   German M. Bravo (Kronuz)
#   ActiveState Software Inc
#
# Portions created by ActiveState Software Inc are Copyright (C) 2000-2007
# ActiveState Software Inc. All Rights Reserved.
#
# Mostly based in Komodo Editor's oop-driver.py and ci2.py
# at commit 40ccb140ac73935a63e6455ec39f2b976e33024d
#
from __future__ import absolute_import, unicode_literals, print_function

import os
import sys

__file__ = os.path.normpath(os.path.abspath(__file__))
__path__ = os.path.dirname(__file__)
python_sitelib_path = os.path.normpath(__path__)
if python_sitelib_path not in sys.path:
    sys.path.insert(0, python_sitelib_path)

import traceback
import logging
import socket

import six
import cmdln

from __init__ import __version__


class DummyStream(object):
    def write(self, message):
        pass

    def flush(self):
        pass


class BinaryStream(object):
    def __init__(self, stream):
        self.stream = stream

    def encode(self, message):
        if isinstance(message, six.text_type):
            message = message.encode('utf-8')
        return message

    def write(self, message):
        message = self.encode(message)
        written = self.stream.write(message)
        self.stream.flush()
        return written

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class TextStream(BinaryStream):
    def encode(self, message):
        if isinstance(message, six.binary_type):
            message = message.decode('utf-8')
        return message


class Shell(cmdln.Cmdln):
    name = "codeintel"
    description = "CodeIntel v%s" % __version__
    version = __version__

    profiling = False
    traceback = False

    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, *args, **kwargs)

        # Don't redirect output
        os.environ["KOMODO_VERBOSE"] = "1"

        self.verbosity = 0
        self.traceback = False
        self.log = logging.getLogger('codeintel.oop')

        try:
            self.set_process_limits()
        except:
            self.log.exception("Failed to set process memory/CPU limits")
        try:
            self.set_idle_priority()
        except:
            self.log.exception("Failed to set process CPU priority")

    def set_idle_priority(self):
        """Attempt to set the process priority to idle"""
        try:
            os.nice(5)
        except AttributeError:
            pass  # No os.nice on Windows
        if sys.platform.startswith('win'):
            import ctypes
            from ctypes import wintypes
            SetPriorityClass = ctypes.windll.kernel32.SetPriorityClass
            SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            SetPriorityClass.restype = wintypes.BOOL
            HANDLE_CURRENT_PROCESS = -1
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            SetPriorityClass(HANDLE_CURRENT_PROCESS, BELOW_NORMAL_PRIORITY_CLASS)

    def set_process_limits(self):
        import ctypes
        if sys.platform.startswith("win"):
            """Pre-allocate (but don't commit) a 1GB chunk of memory to prevent it
            from actually being used by codeintel; this acts as a limit on the
            amount of memory we can actually use.  It has no effects on performance
            (since we're only eating address space, not RAM/swap) but helps to
            prevent codeintel from blowing up the system.
            """
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            VirtualAlloc = kernel32.VirtualAlloc
            VirtualAlloc.argtypes = [wintypes.LPVOID, wintypes.ULONG, wintypes.DWORD, wintypes.DWORD]
            VirtualAlloc.restype = wintypes.LPVOID
            MEM_RESERVE = 0x00002000
            MEM_TOP_DOWN = 0x00100000
            PAGE_NOACCESS = 0x01
            # we can only eat about 1GB; trying for 2GB causes the allocation to
            # (harmlessly) fail, which doesn't accomplish our goals
            waste = VirtualAlloc(None, 1 << 30, MEM_RESERVE | MEM_TOP_DOWN, PAGE_NOACCESS)
            if waste:
                self.log.debug("Successfullly allocated: %r", waste)
            else:
                self.log.debug("Failed to reduce address space: %s",
                        ctypes.WinError(ctypes.get_last_error()).strerror)
        elif sys.platform.startswith("linux"):
            import resource
            # Limit the oop process to 2GB of memory.
            #
            # Note that setting to 1GB of memory cause "bk test" failures, showing
            # this error:
            #   Fatal Python error: Couldn't create autoTLSkey mapping
            GB = 1 << 30
            resource.setrlimit(resource.RLIMIT_AS, (2 * GB, -1))
        else:
            # TODO: What to do on the Mac?
            pass

    def set_log_level(self, option, opt_str, value, parser):
        # Optarg is of the form '<logname>:<levelname>', e.g.
        # "codeintel:DEBUG", "codeintel.db:INFO".
        name, _, level = value.rpartition(':')
        try:
            level = int(level)
        except ValueError:
            level = getattr(logging, level.upper(), logging.ERROR)
        logging.getLogger(name).setLevel(level)

    def set_profiling(self, option, opt_str, value, parser):
        self.profiling = True

    def set_traceback(self, option, opt_str, value, parser):
        self.traceback = True

    def set_stacktracer(self, option, opt_str, value, parser):
        from stacktracer import Stacktracer
        self.tracer = Stacktracer('stacktracer{ext}', traceback_interval=5, stats_interval=10)
        self.tracer.start()

    def set_verbosity(self, option, opt_str, value, parser):
        self.verbosity += 1
        if self.verbosity == 1:
            self.log.setLevel(logging.INFO)
            logging.getLogger('codeintel').setLevel(logging.INFO)
        elif self.verbosity > 1:
            self.log.setLevel(logging.DEBUG)
            logging.getLogger('codeintel').setLevel(logging.DEBUG)

    def set_log_file(self, option, opt_str, value, parser):
        if value in ('stdout', '/dev/stdout'):
            stream = sys.stdout
        elif value in ('stderr', '/dev/stderr'):
            stream = sys.stderr
        else:
            log_dir = os.path.dirname(value)
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            stream = open(value, 'wt')
        logging.getLogger().addHandler(logging.StreamHandler(stream=TextStream(stream)))
        # XXX marky horrible ugly hack
        sys.stderr = stream
        sys.stdout = stream

    def get_optparser(self):
        optparser = cmdln.Cmdln.get_optparser(self)
        optparser.add_option("-v", "--verbose",
            action="callback", callback=self.set_verbosity,
            help="More verbose output. Repeat for more and more output.")
        optparser.add_option("--log-file",
            action="callback", callback=self.set_log_file, nargs=1, type="str",
            help="The name of the file to log to")
        optparser.add_option("-L", "--log-level",
            action="callback", callback=self.set_log_level, nargs=1, type="str",
            help="Specify a logger level via '<log name>:<level>'")
        optparser.add_option("-p", "--profile",
            action="callback", callback=self.set_profiling,
            help="Enable code profiling, prints out a method summary.")
        optparser.add_option("--traceback",
            action="callback", callback=self.set_traceback,
            help="Show full traceback on error.")
        optparser.add_option("--stacktracer",
            action="callback", callback=self.set_stacktracer,
            help="Save stacktracer information for profiling.")
        return optparser

    #   ___   ___  _ __
    #  / _ \ / _ \| '_ \
    # | (_) | (_) | |_) |
    #  \___/ \___/| .__/
    #             |_|
    @cmdln.option("--database-dir", default=os.path.expanduser("~/.codeintel"),
                  help="The base directory for the codeintel database.")
    @cmdln.option("--import-path", action="append", default=[""],
                  help="Paths to add to the Python import path")
    @cmdln.option("--pipe", default=None,
                  help="Connect using unix socket. Pass unix socket file")
    @cmdln.option("--tcp", default=None,
                  help="Connect using TCP socket. Pass host:port")
    @cmdln.option("--server", default=None,
                  help="Start a server listening to a given port. Pass port")
    def do_oop(self, subcmd, opts):
        """Run the out-of-process (OOP) driver.

        ${cmd_usage}
        ${cmd_option_list}
        """
        import atexit
        from codeintel2.oop import Driver

        old_sys_path = set(os.path.abspath(os.path.join(p)) for p in sys.path)
        for relpath in opts.import_path:
            import_path = os.path.abspath(os.path.join(__path__, relpath))
            if import_path not in old_sys_path:
                sys.path.append(import_path)

        if opts.tcp:
            host, _, port = opts.tcp.partition(':')
            port = int(port)
            self.log.info("Connecting to: %s:%s", host, port)
            try:
                conn = socket.create_connection((host, port))
                fd_in = conn.makefile('rb', 0)
                fd_out = conn.makefile('wb', 0)
            except Exception as ex:
                self.log.exception("Failed to connect with client: %s", ex)
                return

        elif opts.server:
            host, _, port = opts.server.partition(':')
            if not port:
                host, port = '0.0.0.0', host
            port = int(port)
            self.log.info("Server listening on: %s:%s", host, port)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(0)
            conn = sock.accept()
            self.log.info("Client accepted!")
            fd_in = conn[0].makefile('rb', 0)
            fd_out = conn[0].makefile('wb', 0)

        elif opts.pipe and opts.pipe not in ('-', 'stdin', '/dev/stdin'):
            pipe = opts.pipe
            self.log.info("Connecting to pipe: %s", pipe)
            try:
                if sys.platform.startswith('win'):
                    # using Win32 pipes
                    from win32_named_pipe import Win32Pipe
                    fd_out = fd_in = Win32Pipe(name=pipe, client=True)
                else:
                    # Open the write end first, so the parent doesn't hang
                    fd_out = open(os.path.join(pipe, 'out'), 'wb', 0)
                    fd_in = open(os.path.join(pipe, 'in'), 'rb', 0)
            except Exception as ex:
                self.log.exception("Failed to pipe with client: %s", ex)
                return

        else:
            # force unbuffered stdout
            fd_in = sys.stdin
            fd_out = os.fdopen(sys.stdout.fileno(), 'wb', 0)

        if not os.path.exists(opts.database_dir):
            os.makedirs(opts.database_dir)

        driver = Driver(db_base_dir=opts.database_dir, fd_in=fd_in, fd_out=fd_out)
        atexit.register(driver.finalize)
        driver.start()

    #  _            _
    # | |_ ___  ___| |_
    # | __/ _ \/ __| __|
    # | ||  __/\__ \ |_
    #  \__\___||___/\__|
    #
    def do_test(self, argv):
        """Run the ci2 test suite.

        See `ci2 test -h' for more details.
        """
        import subprocess
        testdir = os.path.join(__path__, "test2")
        if self.profiling:
            cmd = '"%s" -m cProfile -s time test.py %s' % (sys.executable, ' '.join(argv[1:]))
        else:
            cmd = '"%s" test.py %s' % (sys.executable, ' '.join(argv[1:]))
        env = os.environ.copy()
        env["CODEINTEL_NO_PYXPCOM"] = "1"
        p = subprocess.Popen(cmd, cwd=testdir, env=env, shell=True)
        p.wait()
        return p.returncode

    #       _            _     _            _
    #   ___| | ___  __ _| |_  | |_ ___  ___| |_ ___
    #  / __| |/ _ \/ _` | __| | __/ _ \/ __| __/ __|
    # | (__| |  __/ (_| | |_  | ||  __/\__ \ |_\__ \
    #  \___|_|\___|\__,_|\__|  \__\___||___/\__|___/
    #
    def do_clean_tests(self, argv):
        """ Remove the unicode directories after running `ci2 test`."""
        import subprocess
        testdir = os.path.join(__path__, "test2")
        cmd = '"%s" clean_tests.py' % (sys.executable,)
        env = os.environ.copy()
        p = subprocess.Popen(cmd, cwd=testdir, env=env, shell=True)
        p.wait()
        return p.returncode

    #      _            _            _
    #   __| | ___   ___| |_ ___  ___| |_
    #  / _` |/ _ \ / __| __/ _ \/ __| __|
    # | (_| | (_) | (__| ||  __/\__ \ |_
    #  \__,_|\___/ \___|\__\___||___/\__|
    #
    def do_doctest(self, subcmd, opts):
        """Run the ci2 internal doctests.

        ${cmd_usage}
        ${cmd_option_list}
        I'd prefer these just be run as part of the 'test' command, but
        I don't know how to integrate that into unittest.main().
        """
        import doctest
        doctest.testmod()

    #                    _      _    _
    #  _   _ _ __  _ __ (_) ___| | _| | ___
    # | | | | '_ \| '_ \| |/ __| |/ / |/ _ \
    # | |_| | | | | |_) | | (__|   <| |  __/
    #  \__,_|_| |_| .__/|_|\___|_|\_\_|\___|
    #             |_|
    @cmdln.alias("up")
    def do_unpickle(self, subcmd, opts, *path_patterns):
        """Unpickle and dump the given paths.

        ${cmd_usage}
        ${cmd_option_list}
        """
        import pprint
        import cPickle as pickle
        from ci2 import _paths_from_path_patterns

        for path in _paths_from_path_patterns(path_patterns):
            fin = open(path, 'rb')
            try:
                obj = pickle.load(fin)
            finally:
                fin.close()
            pprint.pprint(obj)

    #      _ _          _               _
    #   __| | |__   ___| |__   ___  ___| | __
    #  / _` | '_ \ / __| '_ \ / _ \/ __| |/ /
    # | (_| | |_) | (__| | | |  __/ (__|   <
    #  \__,_|_.__/ \___|_| |_|\___|\___|_|\_\
    #
    @cmdln.option("-d", "--db-base-dir",
                  help="the database base dir to check (defaults to ~/.codeintel)")
    def do_dbcheck(self, subcmd, opts):
        """Run an internal consistency check on the database.

        ${cmd_usage}
        ${cmd_option_list}
        Any errors will be printed. Returns the number of errors (i.e.
        exit value is 0 if there are no consistency problems).
        """
        from codeintel2.manager import Manager

        mgr = Manager(opts.db_base_dir)
        try:
            errors = mgr.db.check()
        finally:
            mgr.finalize()
        for error in errors:
            print(error)
        return len(errors)

    #              _   _ _
    #   ___  _   _| |_| (_)_ __   ___
    #  / _ \| | | | __| | | '_ \ / _ \
    # | (_) | |_| | |_| | | | | |  __/
    #  \___/ \__,_|\__|_|_|_| |_|\___|
    #
    @cmdln.option("-l", "--language", dest="lang",
                  help="the language of the given path content")
    @cmdln.option("-b", "--brief", dest="brief", action="store_true",
                  help="just print the brief name outline")
    @cmdln.option("-s", "--sorted", dest="doSort", action="store_true",
                  help="sort child names alphabetically")
    def do_outline(self, subcmd, opts, path):
        """Print code outline of the given file.

        You can specify a lookup path into the file code outline to
        display via URL-anchor syntax, e.g.:
            ci2 outline path/to/foo.py#AClass.amethod

        ${cmd_usage}
        ${cmd_option_list}
        """
        import re
        from ci2 import _outline_ci_elem
        from codeintel2.manager import Manager
        from codeintel2.util import tree_from_cix

        mgr = Manager()
        mgr.upgrade()
        mgr.initialize()
        try:
            if '#' in path:
                path, anchor = path.rsplit('#', 1)
            else:
                anchor = None

            if path.endswith(".cix"):
                tree = tree_from_cix(open(path, 'r').read())
                # buf = mgr.buf_from_content("", tree[0].get("lang"), path=path)
            else:
                buf = mgr.buf_from_path(path, lang=opts.lang)
                tree = buf.tree

            if anchor is not None:
                # Lookup the anchor in the codeintel CIX tree.
                lpath = re.split(r'\.|::', anchor)

                def blobs_from_tree(tree):
                    for file_elem in tree:
                        for blob in file_elem:
                            yield blob

                for elem in blobs_from_tree(tree):
                    # Generally have 3 types of codeintel trees:
                    # 1. single-lang file: one <file>, one <blob>
                    # 2. multi-lang file: one <file>, one or two <blob>'s
                    # 3. CIX stdlib/catalog file: possibly multiple
                    #    <file>'s, likely multiple <blob>'s
                    # Allow the first token to be the blob name or lang.
                    # (This can sometimes be weird, but seems the most
                    # convenient solution.)
                    if lpath[0] in (elem.get("name"), elem.get("lang")):
                        remaining_lpath = lpath[1:]
                    else:
                        remaining_lpath = lpath
                    for name in remaining_lpath:
                        try:
                            elem = elem.names[name]
                        except KeyError:
                            elem = None
                            break  # try next lang blob
                    if elem is not None:
                        break  # found one
                else:
                    self.log.error("could not find `%s' definition (or blob) in `%s'",
                              anchor, path)
                    return 1
            else:
                elem = tree

            try:
                _outline_ci_elem(elem, brief=opts.brief, doSort=opts.doSort)
            except IOError as ex:
                if ex.errno == 0:
                    # Ignore this error from aborting 'less' of 'ci2 outline'
                    # output:
                    #    IOError: (0, 'Error')
                    pass
                else:
                    raise
        finally:
            mgr.finalize()

    #       _              _               _
    #   ___(_)_  __    ___| |__   ___  ___| | __
    #  / __| \ \/ /   / __| '_ \ / _ \/ __| |/ /
    # | (__| |>  <   | (__| | | |  __/ (__|   <
    #  \___|_/_/\_\___\___|_| |_|\___|\___|_|\_\
    #            |_____|
    def do_cix_check(self, subcmd, opts, *path_patterns):
        """Check the given CIX file(s) for warnings, errors.

        ${cmd_usage}
        ${cmd_option_list}
        Eventually this should include an XML validity check against the
        RelaxNG schema for CIX. However, currently it just checks for
        some common errors.

        Returns the number of warnings/errors generated.
        """
        from codeintel2.util import tree_from_cix, check_tree
        from ci2 import _paths_from_path_patterns

        num_results = 0
        for path in _paths_from_path_patterns(path_patterns):
            tree = None
            cix = open(path, 'r').read()
            tree = tree_from_cix(cix)
            for sev, msg in check_tree(tree):
                num_results += 1
                print("%s: %s: %s" % (path, sev, msg))
        return num_results

    #       _
    #   ___(_)_  __
    #  / __| \ \/ /
    # | (__| |>  <
    #  \___|_/_/\_\
    #
    @cmdln.option("-2", dest="convert", action="store_true",
                  help="convert to CIX 2.0 before printing")
    @cmdln.option("-p", "--pretty-print", action="store_true",
                  help="pretty-print the CIX output (presumes '-2')")
    def do_cix(self, subcmd, opts, *path_patterns):
        """Read in and print a CIX file (possibly converting to CIX 2.0
        and prettifying in the process.

        ${cmd_usage}
        ${cmd_option_list}
        """
        import ciElementTree as ET
        from codeintel2.util import tree_from_cix
        from codeintel2.tree import pretty_tree_from_tree
        from ci2 import _paths_from_path_patterns

        if opts.pretty_print:
            opts.convert = True
        for path in _paths_from_path_patterns(path_patterns):
            tree = None
            cix = open(path, 'r').read()
            if opts.convert:
                tree = tree_from_cix(cix)
                if opts.pretty_print:
                    tree = pretty_tree_from_tree(tree)
                ET.dump(tree)
            else:
                sys.stdout.write(cix)

    #  ___  ___ __ _ _ __
    # / __|/ __/ _` | '_ \
    # \__ \ (_| (_| | | | |
    # |___/\___\__,_|_| |_|
    #
    @cmdln.option("-l", "--language", dest="lang",
                  help="the language of the given path content")
    @cmdln.option("-q", "--quiet", dest="quiet", action="store_true",
                  help="suppress printing of output (useful when just timing)")
    @cmdln.option("-p", "--pretty-print", action="store_true",
                  help="pretty-print the CIX output (presumes '-2')")
    @cmdln.option("-f", "--force", action="store_true",
                  help="force a scan (rather than loading from DB)")
    @cmdln.option("-i", "--include", dest="includes", action="append",
                  help="specify include file patterns (e.g. \"*.pm\")")
    @cmdln.option("-t", dest="time_it", action="store_true",
                  help="dump a time summary (implies --force)")
    @cmdln.option("-T", dest="time_details", action="store_true",
                  help="dump timing info per file (implies --force)")
    @cmdln.option("-r", dest="recursive", action="store_true",
                  help="recursively find files")
    @cmdln.option("-n", dest="stripfuncvars", action="store_true",
                  help="Don't output variables inside of functions (for stdlib creation)")
    def do_scan(self, subcmd, opts, *path_patterns):
        """Scan and print the CIX for the given path(s).

        ${cmd_usage}
        ${cmd_option_list}
        """
        import time
        import ciElementTree as ET
        from ci2 import _paths_from_path_patterns
        from codeintel2.manager import Manager
        from codeintel2.citadel import CitadelBuffer
        from codeintel2.common import CodeIntelError
        from codeintel2.tree import pretty_tree_from_tree
        from codeintel2.util import guess_lang_from_path

        mgr = Manager()
        mgr.upgrade()
        mgr.initialize()
        try:
            if opts.time_it:
                start = time.time()
            quiet = opts.quiet
            if opts.time_it or opts.time_details:
                opts.force = True

            scan_count = 0
            lang_warnings = set()
            tree = None
            for path in _paths_from_path_patterns(path_patterns,
                                                  recursive=opts.recursive,
                                                  includes=opts.includes):
                if opts.time_it:
                    sys.stderr.write(path + "\n")
                if opts.time_details:
                    start1 = time.time()

                try:
                    lang = opts.lang or guess_lang_from_path(path)
                except CodeIntelError:
                    self.log.info("skip `%s': couldn't determine language", path)
                    continue
                try:
                    buf = mgr.buf_from_path(path, lang=lang)
                except OSError as ex:
                    # Couldn't access the file.
                    if not opts.recursive:
                        raise
                    # Ignore files we don't really care about.
                    self.log.warn("%r - %r", ex, path)
                    continue
                if not isinstance(buf, CitadelBuffer):
                    if opts.recursive:
                        # Ignore files that scanning isn't provided for.
                        continue
                    raise CodeIntelError("`%s' (%s) is not a language that "
                                         "uses CIX" % (path, buf.lang))

                scan_count += 1
                if scan_count % 10 == 0:
                    self.log.info("%d scanning %r", scan_count, path)

                try:
                    if opts.force:
                        buf.scan()
                    if tree is None:
                        tree = ET.Element("codeintel", version="2.0")
                    file_elem = ET.SubElement(tree, "file",
                                              lang=buf.lang,
                                              mtime=str(int(time.time())),
                                              path=os.path.basename(path))
                    for lang, blob in sorted(buf.blob_from_lang.items()):
                        blob = buf.blob_from_lang[lang]
                        file_elem.append(blob)
                except KeyError as ex:
                    # Unknown cile language.
                    if not opts.recursive:
                        raise
                    message = str(ex)
                    if message not in lang_warnings:
                        lang_warnings.add(message)
                        self.log.warn("Skipping unhandled language %s", message)

                if opts.time_details:
                    delta = time.time() - start1
                    sys.stderr.write("%.3f %s\n" % (delta, path))
                    sys.stderr.flush()

            if tree is not None:
                if opts.stripfuncvars:
                    # For stdlibs, we don't care about variables inside of
                    # functions and they take up a lot of space.
                    for function in tree.getiterator('scope'):
                        if function.get('ilk') == 'function':
                            function[:] = [child for child in function
                                           if child.tag != 'variable']
                if opts.pretty_print:
                    tree = pretty_tree_from_tree(tree)
                if not quiet:
                    sys.stdout.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                    ET.dump(tree)
                if opts.time_it:
                    end = time.time()
                    sys.stderr.write("scan took %.3fs\n" % (end - start))
        finally:
            mgr.finalize()

    #  _     _             _
    # | |__ | |_ _ __ ___ | |
    # | '_ \| __| '_ ` _ \| |
    # | | | | |_| | | | | | |
    # |_| |_|\__|_| |_| |_|_|
    #
    @cmdln.option("-o", "--output",
                  help="path to which to write HTML output (instead of "
                       "PATH.html, use '-' for stdout)")
    @cmdln.option("-b", "--browse", action="store_true",
                  help="open output file in browser")
    @cmdln.option("-f", "--force", action="store_true",
                  help="allow overwrite of existing file")
    @cmdln.option("-e", "--do-eval", action="store_true",
                  help="do (and show) completion evaluation")
    @cmdln.option("-t", "--do-trg", action="store_true",
                  help="do (and show) trigger handling (also implies -e)")
    @cmdln.option("-l", "--lang",
                  help="specify the language of the given path (if not "
                       "given it will be guessed)")
    def do_html(self, subcmd, opts, path):
        """Convert the given path to styled HTML.

        ${cmd_usage}
        ${cmd_option_list}

        The output includes trigger info and other stats. I.e. this is
        primarily a debugging tool.
        """
        from codeintel2.manager import Manager
        from codeintel2.common import Error
        from ci2 import _url_from_local_path

        mgr = Manager()
        try:
            if opts.browse:
                htmls = []
            buf = mgr.buf_from_path(path, lang=opts.lang)
            html = buf.to_html(True, True, title=path,
                               do_trg=opts.do_trg,
                               do_eval=opts.do_eval)
        finally:
            mgr.finalize()

        if opts.output == '-':
            output_path = None
            output_file = sys.stdout
        else:
            if opts.output:
                output_path = opts.output
            else:
                output_path = path + ".html"
            if os.path.exists(output_path):
                if opts.force:
                    os.remove(output_path)
                else:
                    raise Error("`%s' exists: use -f|--force option to allow overwrite" % output_path)
            output_file = open(output_path, 'w')
        # else:
        #    output_path = None
        #    output_file = sys.stdout
        #    #XXX Disable writing t
        #    output_file = None
        if output_file:
            output_file.write(html)
        if output_path:
            output_file.close()

        if opts.browse:
            if not output_path:
                raise Error("cannot open in browser if stdout used "
                            "for output")
            import webbrowser
            url = _url_from_local_path(output_path)
            webbrowser.open_new(url)

    #       _      ____  _     _             _
    #   ___(_)_  _|___ \| |__ | |_ _ __ ___ | |
    #  / __| \ \/ / __) | '_ \| __| '_ ` _ \| |
    # | (__| |>  < / __/| | | | |_| | | | | | |
    #  \___|_/_/\_\_____|_| |_|\__|_| |_| |_|_|
    #
    @cmdln.option("-c", "--css", dest="css_reference_files", action="append",
                  help="add css reference file for styling"
                       " (can be used more than once)")
    @cmdln.option("-o", "--output", dest="output",
                  help="filename for generated html output, defaults to stdout")
    @cmdln.option("-t", "--toc-file", dest="toc_file",
                  help="filename for generated toc xml file")
    @cmdln.option("-l", "--language", dest="lang",
                  help="only include docs for the supplied language")
    def do_cix2html(self, subcmd, opts, path):
        """Turn cix file into html API documentation.

        Example:
            ci2 cix2html path/to/foo.cix#AClass.amethod
            ci2 cix2html path/to/foo.cix -o htmldir

        ${cmd_usage}
        ${cmd_option_list}
        """
        from codeintel import cix2html

        cix2html.cix2html(opts, path)

    #    _
    #   (_)___  ___  _ __
    #   | / __|/ _ \| '_ \
    #   | \__ \ (_) | | | |
    #  _/ |___/\___/|_| |_|
    # |__/
    @cmdln.option("-o", "--output",
                  help="path to which to write JSON output (instead of "
                       "PATH.json, use '-' for stdout)")
    @cmdln.option("-f", "--force", action="store_true",
                  help="allow overwrite of existing file")
    def do_json(self, subcmd, opts, path):
        """Convert cix XML file into json format.

        ${cmd_usage}
        ${cmd_option_list}
        """
        import json
        from collections import defaultdict
        from codeintel2.manager import Manager
        from codeintel2.util import tree_from_cix
        from codeintel2.common import Error

        if opts.output == '-':
            output_path = None
            output_file = sys.stdout
        else:
            if opts.output:
                output_path = opts.output
            else:
                output_path = os.path.splitext(path)[0] + ".json"
            if os.path.exists(output_path):
                if opts.force:
                    os.remove(output_path)
                else:
                    raise Error("`%s' exists: use -f|--force option to "
                                "allow overwrite" % output_path)
            output_file = open(output_path, 'w')

        mgr = Manager()
        mgr.upgrade()
        mgr.initialize()

        try:
            if path.endswith(".cix"):
                tree = tree_from_cix(open(path, 'r').read())
            else:
                buf = mgr.buf_from_path(path, lang=opts.lang)
                tree = buf.tree

            result = {}
            ci = result["codeintel"] = defaultdict(list)

            def _elemToDict(parent, elem):
                data = defaultdict(list)
                name = elem.get("name")
                if name is not None:
                    data["name"] = name
                data["tag"] = elem.tag
                for attr_name, attr in elem.attrib.items():
                    data[attr_name] = attr
                parent["children"].append(data)
                for child in elem:
                    _elemToDict(data, child)

            for child in tree:
                _elemToDict(ci, child)

            json.dump(result, output_file, indent=2)

        finally:
            mgr.finalize()

    #        _
    #  _ __ | | __ _ _   _
    # | '_ \| |/ _` | | | |
    # | |_) | | (_| | |_| |
    # | .__/|_|\__,_|\__, |
    # |_|            |___/
    def do_play(self, subcmd, opts):
        """Run my current play/dev code.

        ${cmd_usage}
        ${cmd_option_list}
        """
        import pprint
        import random
        import ciElementTree as ET
        from codeintel2.manager import Manager
        from codeintel2.tree import pretty_tree_from_tree
        from codeintel2.common import LogEvalController, Error
        from codeintel2.util import tree_from_cix, dedent, unmark_text, banner
        from ci2 import _escaped_text_from_text

        if False:
            lang = "CSS"
            markedup_content = dedent("""
                /* http://www.w3.org/TR/REC-CSS2/fonts.html#propdef-font-weight */
                h1 {
                    border: 1px solid black;
                    font-weight /* hi */: <|> !important
                }
            """)
            content, data = unmark_text(markedup_content)
            pos = data["pos"]
            mgr = Manager()
            # mgr.upgrade() # Don't need it for just CSS usage.
            mgr.initialize()
            try:
                buf = mgr.buf_from_content(content, lang=lang, path="play.css")
                trg = buf.trg_from_pos(pos)
                if trg is None:
                    raise Error("unexpected trigger: %r" % trg)
                completions = buf.cplns_from_trg(trg)
                print("COMPLETIONS: %r" % completions)
            finally:
                mgr.finalize()

        elif False:
            lang = "Python"
            path = os.path.join("<Unsaved>", "rand%d.py" % random.randint(0, 100))
            markedup_content = dedent("""
                import sys, os

                class Foo:
                    def bar(self):
                        pass

                sys.<|>path    # should have path in completion list
                f = Foo()
                """)
            content, data = unmark_text(markedup_content)
            print(banner(path))
            print(_escaped_text_from_text(content, "whitespace"))
            pos = data["pos"]
            mgr = Manager()
            mgr.upgrade()
            mgr.initialize()
            try:
                buf = mgr.buf_from_content(content, lang=lang, path=path)
                print(banner("cix", '-'))
                print(buf.cix)

                trg = buf.trg_from_pos(pos)
                if trg is None:
                    raise Error("unexpected trigger: %r" % trg)
                print(banner("completions", '-'))
                ctlr = LogEvalController(self.log)
                buf.async_eval_at_trg(trg, ctlr)
                ctlr.wait(2)  # XXX
                if not ctlr.is_done():
                    ctlr.abort()
                    raise Error("XXX async eval timed out")
                pprint.pprint(ctlr.cplns)
                print(banner(None))
            finally:
                mgr.finalize()
        elif False:
            lang = "Ruby"
            path = os.path.join("<Unsaved>", "rand%d.py" % random.randint(0, 100))
            markedup_content = dedent("""\
            r<1>equire 'net/http'
            include Net
            req = HTTPRequest.new
            req.<2>get()
            """)
            content, data = unmark_text(markedup_content)
            print(banner(path))
            print(_escaped_text_from_text(content, "whitespace"))
            pos = data[1]
            mgr = Manager()
            mgr.upgrade()
            mgr.initialize()
            try:
                buf = mgr.buf_from_content(content, lang=lang, path=path)
                print(banner("cix", '-'))
                cix = buf.cix
                print(ET.tostring(pretty_tree_from_tree(tree_from_cix(cix))))

                trg = buf.trg_from_pos(pos, implicit=False)
                if trg is None:
                    raise Error("unexpected trigger: %r" % trg)
                print(banner("completions", '-'))
                ctlr = LogEvalController(self.log)
                buf.async_eval_at_trg(trg, ctlr)
                ctlr.wait(30)  # XXX
                if not ctlr.is_done():
                    ctlr.abort()
                    raise Error("XXX async eval timed out")
                pprint.pprint(ctlr.cplns)
                print(banner(None))
            finally:
                mgr.finalize()


def main():
    shell = Shell()
    try:
        retval = shell.main(sys.argv)
    except KeyboardInterrupt:
        sys.exit(1)
    except SystemExit:
        raise
    except:
        skip_it = False
        exc_info = sys.exc_info()
        if hasattr(exc_info[0], "__name__"):
            exc_class, exc, tb = exc_info
            if isinstance(exc, IOError) and exc.args[0] == 32:
                # Skip 'IOError: [Errno 32] Broken pipe'.
                skip_it = True
            if not skip_it:
                tb_path, tb_lineno, tb_func = traceback.extract_tb(tb)[-1][:3]
                shell.log.error("%s (%s:%s in %s)", exc_info[1], tb_path, tb_lineno, tb_func)
        else:  # string exception
            shell.log.error(exc_info[0])
        if not skip_it:
            if shell.log.isEnabledFor(logging.DEBUG) or shell.traceback:
                print()
                traceback.print_exception(*exc_info)
            sys.exit(1)
    else:
        sys.exit(retval)


if __name__ == "__main__":
    main()
