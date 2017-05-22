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
# Mostly based in Komodo Editor's oop-driver.py
# at commit 40ccb140ac73935a63e6455ec39f2b976e33024d
#
from __future__ import absolute_import, unicode_literals, print_function

import os
import sys
import argparse

__file__ = os.path.normpath(os.path.abspath(__file__))
__path__ = os.path.dirname(__file__)

python_sitelib_path = os.path.normpath(__path__)
if python_sitelib_path not in sys.path:
    sys.path.insert(0, python_sitelib_path)

import socket
import logging
import six

from codeintel import __version__


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


def oop_driver(database_dir, connect=None, log_levels=[], log_file=None, import_path=[]):
    """
    Starts OOP CodeIntel driver
    Args:
        :param database_dir:  Directory where CodeIntel database is.
        :param connect:      Connect using a socket to this 'IP:port'. It can
                             also be an output file, for example 'stdin'
        :param log_levels:   List of logger:LEVEL, where logger can be,
                             for example, "codeintel.db" and level "DEBUG":
                             ['codeintel:WARNING', 'codeintel.db:DEBUG']
        :param log_file:     File where logs will be written. It can be 'stdout'
                             or 'stderr', for example
    """
    # Don't redirect output
    os.environ["KOMODO_VERBOSE"] = "1"

    if log_file:
        if log_file in ('stdout', '/dev/stdout'):
            stream = sys.stdout
        elif log_file in ('stderr', '/dev/stderr'):
            stream = sys.stderr
        else:
            log_dir = os.path.dirname(log_file)
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            stream = open(log_file, 'wt')
        logging.basicConfig(stream=TextStream(stream))
        # XXX marky horrible ugly hack
        sys.stderr = stream
        sys.stdout = stream
    else:
        logging.basicConfig(stream=DummyStream())

    logging.getLogger('codeintel.oop.driver').setLevel(logging.INFO)

    for log_level in log_levels:
        name, _, level = log_level.rpartition(':')
        try:
            level = int(level)
        except ValueError:
            level = getattr(logging, level.upper(), logging.ERROR)
        logging.getLogger(name).setLevel(level)

    log = logging.getLogger('codeintel.oop.executable')

    try:
        set_process_limits(log)
    except:
        log.exception("Failed to set process memory/CPU limits")
    try:
        set_idle_priority(log)
    except:
        log.exception("Failed to set process CPU priority")

    old_sys_path = set(os.path.abspath(os.path.join(p)) for p in sys.path)
    for relpath in import_path:
        import_path = os.path.abspath(os.path.join(__path__, relpath))
        if import_path not in old_sys_path:
            sys.path.append(import_path)

    try:
        if connect and connect not in ('-', 'stdin', '/dev/stdin'):
            if connect.startswith('pipe:'):
                pipe_name = connect.split(':', 1)[1]
                log.debug("connecting to pipe: %s", pipe_name)
                if sys.platform.startswith('win'):
                    # using Win32 pipes
                    from win32_named_pipe import Win32Pipe
                    fd_out = fd_in = Win32Pipe(name=pipe_name, client=True)
                else:
                    # Open the write end first, so the parent doesn't hang
                    fd_out = open(os.path.join(pipe_name, 'out'), 'wb', 0)
                    fd_in = open(os.path.join(pipe_name, 'in'), 'rb', 0)
                log.debug("opened: %r", fd_in)
            else:
                host, _, port = connect.partition(':')
                port = int(port)
                log.debug("connecting to: %s:%s", host, port)
                conn = socket.create_connection((host, port))
                fd_in = conn.makefile('r+b', 0)
                fd_out = fd_in
        else:
            # force unbuffered stdout
            fd_in = sys.stdin
            fd_out = os.fdopen(sys.stdout.fileno(), 'wb', 0)
    except Exception as ex:
        log.exception("Failed to connect with client: %s", ex)
        raise

    if not os.path.exists(database_dir):
        os.makedirs(database_dir)

    from codeintel2.oop import Driver
    driver = Driver(db_base_dir=database_dir, fd_in=fd_in, fd_out=fd_out)
    try:
        driver.start()
    except KeyboardInterrupt:
        pass


def set_idle_priority(log):
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


def set_process_limits(log):
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
            log.debug("Successfullly allocated: %r", waste)
        else:
            log.debug("Failed to reduce address space: %s",
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


def main():
    parser = argparse.ArgumentParser()
    parser.description = "CodeIntel v%s out-of-process (OOP) driver" % __version__
    parser.add_argument("--database-dir", default=os.path.expanduser("~/.codeintel"),
                        help="The base directory for the codeintel database.")
    parser.add_argument("--log-level", action="append", default=[],
                        help="<log name>:<level> Set log level")
    parser.add_argument("--log-file", default=None,
                        help="The name of the file to log to")
    parser.add_argument("--connect", default=None,
                        help="Connect over TCP instead of using stdin/stdout")
    parser.add_argument("--import-path", action="append", default=[""],
                        help="Paths to add to the Python import path")
    args = parser.parse_args()

    oop_driver(
        database_dir=args.database_dir,
        connect=args.connect,
        log_levels=args.log_level,
        log_file=args.log_file,
        import_path=args.import_path
    )


if __name__ == '__main__':
    main()
