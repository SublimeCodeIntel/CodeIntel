#!python
# Copyright (c) 2004-2011 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""Shared base class for LangDirsLib / MultiLangDirsLib
See langlib.py / multilanglib.py
"""

from __future__ import absolute_import
import logging
from os.path import join
from contextlib import contextmanager
from codeintel2.common import *

#---- globals
log = logging.getLogger("codeintel.db")
#log.setLevel(logging.DEBUG)

#---- Base lang lib implementation
class LangDirsLibBase(object):
    def __init__(self):
        self._have_ensured_scanned_from_dir_cache = set()

    def ensure_all_dirs_scanned(self, ctlr=None):
        """Ensure that all importables in this dir have been scanned
        into the db at least once.
        """
        # filter out directories we've already scanned, so that we don't need
        # to report them (this also filters out quite a few spurious
        # notifications)
        dirs = frozenset([d for d in self.dirs if d not in self._have_ensured_scanned_from_dir_cache])
        if not dirs:
            # all directories have already been scanned; nothing to do.
            log.debug("Skipping scanning dirs %r - all scanned",
                      self.dirs)
            return

        reporter = self.lang_zone.db.event_reporter

        if reporter and hasattr(reporter, "onScanStarted"):
            # TODO: i18n w/ PluralForms
            msg = "Scanning %r directories" % (len(dirs),)
            if len(dirs) == 1:
                msg = "Scanning one directory"
            reporter.onScanStarted(msg, dirs)

        log.debug("ensure_all_dirs_scanned: scanning %r directories",
                  len(dirs))
        scanned = set()
        try:
            for dir in dirs:
                if ctlr:
                    if ctlr.is_aborted():
                        log.debug("ctlr aborted")
                        break
                try:
                    if reporter and hasattr(reporter, "onScanDirectory"):
                        reporter.onScanDirectory("Scanning %s files in '%s'" % (self.lang, dir),
                                                 dir,
                                                 len(scanned),
                                                 len(dirs))
                except:
                    pass # eat any errors about reporting progress
                self.ensure_dir_scanned(dir, ctlr=ctlr)
                scanned.add(dir)
        finally:
            # report that we have stopped scanning
            log.debug("ensure_all_dirs_scanned: finished scanning %r/%r dirs",
                      len(scanned), len(dirs))
            if reporter and hasattr(reporter, "onScanComplete"):
                reporter.onScanComplete(dirs, scanned)

    def ensure_dir_scanned(self, dir, ctlr=None, reporter=None):
        """Ensure that all importables in this dir have been scanned
        into the db at least once.
        """
        #TODO: should "self.lang" in this function be "self.sublang" for
        # the MultiLangDirsLib case?
        if dir not in self._have_ensured_scanned_from_dir_cache:
            if reporter is None:
                reporter = self.lang_zone.db.event_reporter
            res_index = self.lang_zone.load_index(dir, "res_index", {})
            importables = self._importables_from_dir(dir)
            importable_values = [i[0] for i in importables.values()
                                 if i[0] is not None]
            removed_values = set(res_index.keys()).difference(importable_values)
            current = 0
            total = len(importable_values) + len(removed_values)
            for base in importable_values:
                if ctlr and ctlr.is_aborted():
                    log.debug("ctlr aborted")
                    return
                current += 1
                if base not in res_index:
                    def scan_reporter(msg):
                        if reporter and hasattr(reporter, "onScanFile"):
                            try:
                                reporter.onScanFile(msg, dir, current, total)
                            except:
                                pass  # eat any errors about reporting progress
                    try:
                        buf = self.mgr.buf_from_path(join(dir, base),
                                                     lang=self.lang)
                    except (EnvironmentError, CodeIntelError) as ex:
                        # This can occur if the path does not exist, such as a
                        # broken symlink, or we don't have permission to read
                        # the file, or the file does not contain text.
                        continue
                    if ctlr is not None:
                        ctlr.info("load %r", buf)
                    buf.scan_if_necessary(reporter=reporter and scan_reporter)

            # Remove scanned paths that don't exist anymore.
            for base in removed_values:
                if ctlr and ctlr.is_aborted():
                    log.debug("ctlr aborted")
                    return
                current += 1
                if reporter and hasattr(reporter, "onScanFile"):
                    try:
                        reporter.onScanFile("Scanning %s file '%s'" % (self.lang, join(dir, base)),
                                            dir,
                                            current,
                                            total)
                    except:
                        pass  # eat any errors about reporting progress
                basename = join(dir, base)
                self.lang_zone.remove_path(basename)

            self._have_ensured_scanned_from_dir_cache.add(dir)
