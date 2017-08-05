# -*- coding: utf-8 -*-
"""
Stack tracer for multi-threaded applications.

Copyright (C) 2017 German M. Bravo (Kronuz)

Based on code and ideas from:
[http://code.activestate.com/recipes/577334-how-to-debug-deadlocked-multi-threaded-programs/]
[https://www.nylas.com/blog/performance]

Usage:
    from stacktracer import Stacktracer
    tracer = Stacktracer('/tmp/stacktracer{ext}', traceback_interval=5, stats_interval=10)
    tracer.start()

    ....

    tracer.stop()
"""
from __future__ import unicode_literals

import os
import sys
import time
import atexit
import platform
import datetime
import threading
import traceback
import collections


MAIN_IDENT = threading.current_thread().ident


class Stacktracer(threading.Thread):
    """
    traceback_path and stats_path can either be a directory (ending with '/')
    or a full path of a pattern: '/traceback/path/{prefix}{pid}__{host}-{time}{ext}'
    """
    def __init__(self,
            path=None,
            traceback_path=None, traceback_interval=10,
            stats_path=None, stats_interval=600,
            granularity=0.005,
            prefix='',
            traceback='html', stats=True):

        self._prefix = prefix
        if traceback and stats:
            self._granularity = granularity
        elif traceback:
            self._granularity = traceback_interval
        elif stats:
            self._granularity = stats_interval

        self._stop_requested = threading.Event()

        self._started_time = None

        self._threads = None
        self._threads_time = None
        self._threads_delta = datetime.timedelta(seconds=20)

        traceback_path = traceback_path or path
        self._traceback = None
        if traceback and traceback_path:
            self._traceback = getattr(self, 'traceback_%s' % traceback, None)
            self._traceback_time = None
            if traceback_path.endswith(os.sep):
                self._traceback_path, self._traceback_pattern = traceback_path, "{prefix}{pid}__{host}-{time}{ext}"
            else:
                self._traceback_path, self._traceback_pattern = os.path.dirname(traceback_path), os.path.basename(traceback_path)
            self._traceback_delta = datetime.timedelta(seconds=traceback_interval)
            try:
                os.makedirs(self._traceback_path)
            except OSError:
                pass

        stats_path = stats_path or path
        self._stats = None
        if stats and stats_path:
            self._stats = self.stats
            self._stats_time = None
            if stats_path.endswith(os.sep):
                self._stats_path, self._stats_pattern = stats_path, "{prefix}{pid}__{host}-{time}{ext}"
            else:
                self._stats_path, self._stats_pattern = os.path.dirname(stats_path), os.path.basename(stats_path)
            self._stats_delta = datetime.timedelta(seconds=stats_interval)
            try:
                os.makedirs(self._stats_path)
            except OSError:
                pass

        self._code = []
        self._stack_counts = collections.defaultdict(int)

        threading.Thread.__init__(self, name="StacktracerThread")

    def run(self):
        while not self._stop_requested.isSet():
            try:
                self._stacktracer()
            except Exception:
                traceback.print_exc()
            time.sleep(self._granularity)

    def start(self):
        if any(True for t in threading.enumerate() if t.name == 'StacktracerThread'):
            raise RuntimeWarning("Thread already exists!")
        if self._started_time is None:
            self._started_time = datetime.datetime.now()
            self.setDaemon(True)
            super(Stacktracer, self).start()
            atexit.register(self._python_exit)
        else:
            raise RuntimeWarning("Already tracing.")

    def _python_exit(self):
        self.stop(timeout=5)

    def stop(self, timeout=None):
        if self._started_time is None:
            raise RuntimeWarning("Not tracing, cannot stop.")
        else:
            self._stop_requested.set()
            self.join(timeout)

    def _thread_name(self, ident):
        if self._threads is None or self._now > self._threads_time:
            self._threads_time = self._now + self._threads_delta
            self._threads = {t.ident: t for t in threading.enumerate()}
        thread = self._threads.get(ident)
        return thread.name if thread else ""

    def _stacktracer(self):
        self._now = datetime.datetime.now()

        traceback = False
        if self._traceback and (self._traceback_time is None or self._now >= self._traceback_time):
            self._traceback_time = self._now + self._traceback_delta
            traceback = True

        stats = False
        if self._stats and (self._stats_time is None or self._now >= self._stats_time):
            self._stats_time = self._now + self._stats_delta
            stats = True

        if traceback:
            code = ["# %d" % int(time.time())]

        for ident, frame in sys._current_frames().items():
            if ident != self.ident:
                self._sample(frame)
            if traceback:
                self._traceback_fn(code, ident, frame)

        if traceback:
            self._code = code
            if self._traceback_path is not None:
                filename, traceback = self._traceback()
                with open(filename + '.tmp', 'w') as fout:
                    fout.write(traceback)
                os.rename(filename + '.tmp', filename)

        if stats:
            if self._stats_path is not None:
                filename, stats = self._stats(True)
                with open(filename + '.tmp', 'w') as fout:
                    fout.write(stats)
                os.rename(filename + '.tmp', filename)

    def _get_filename(self, path, pattern, ext):
        filename = os.path.join(path, pattern)
        return filename.format(prefix=self._prefix, pid=os.getpid(), host=platform.node(), time=time.time(), ext=ext)

    def _sample(self, frame):
        stack = []
        while frame is not None:
            stack.append(self._format_frame(frame))
            frame = frame.f_back
        stack = ';'.join(reversed(stack))
        self._stack_counts[stack] += 1

    def _format_frame(self, frame):
        return '{}({})'.format(frame.f_code.co_name, frame.f_globals.get('__name__'))

    def _traceback_fn(self, code, ident, frame):
        name = self._thread_name(ident)
        code.append("\n# ThreadID: %s%s" % (ident, " (%s)" % name if name else ""))
        for filename, lineno, name, line in traceback.extract_stack(frame):
            code.append("File: \"%s\", line %d, in %s" % (filename, lineno, name))
            if line:
                code.append("  %s" % line.strip())

    def traceback_text(self):
        traceback = "\n".join(self._code)
        filename = self._get_filename(self._traceback_path, self._traceback_pattern, '.txt')
        return filename, traceback

    def traceback_html(self):
        from pygments import highlight
        from pygments.lexers import PythonLexer
        from pygments.formatters import HtmlFormatter
        traceback = "\n".join(self._code)
        filename = self._get_filename(self._traceback_path, self._traceback_pattern, '.html')
        return filename, "<!--\n" + traceback + "\n\n-->" + highlight(traceback, PythonLexer(), HtmlFormatter(
            full=False,
            noclasses=True,
            # style="native",
        ))

    def stats(self, reset=False):
        if self._started_time is None:
            return ''
        now = datetime.datetime.now()
        elapsed = now - self._started_time
        lines = [
            'now {}'.format(now.strftime('%s')),
            'elapsed {}'.format(elapsed),
            'granularity {}'.format(self._granularity),
        ]
        ordered_stacks = sorted(self._stack_counts.items(), key=lambda kv: kv[1], reverse=True)
        lines.extend(['{} {}'.format(frame, count) for frame, count in ordered_stacks])
        stats = '\n'.join(lines) + '\n'
        if reset:
            self.reset()
        filename = self._get_filename(self._stats_path, self._stats_pattern, '.stacktracer')
        return filename, stats

    def reset(self):
        self._started_time = datetime.datetime.now()
        self._stack_counts = collections.defaultdict(int)
