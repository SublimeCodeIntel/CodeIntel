#!/usr/bin/env python

"""C/C++ support for codeintel.
This file will be imported by the codeintel system on startup and the
register() function called to register this language with the system. All
Code Intelligence for this language is controlled through this module.
"""

import os
import sys
import logging
from collections import defaultdict

import SilverCity
from SilverCity.Lexer import Lexer
from SilverCity import ScintillaConstants
from codeintel2 import util
from codeintel2.common import Trigger, TRG_FORM_CPLN, TRG_FORM_CALLTIP, TRG_FORM_DEFN, CILEDriver, Definition
from codeintel2.citadel import CitadelLangIntel, CitadelBuffer
from codeintel2.langintel import (ParenStyleCalltipIntelMixin,
                                  ProgLangTriggerIntelMixin)
from codeintel2.tree import tree_from_cix

# ---- globals

lang = "C++"
log = logging.getLogger("codeintel.cpp")
# log.setLevel(logging.DEBUG)

try:
    import ycm_core
    completer = ycm_core.ClangCompleter()
except ImportError:
    completer = None
    log.warning("Module ycm_core not available. C++ requires ycmd generated ycm_core and libclang in %s", "codeintel moduel path")


PRAGMA_DIAG_TEXT_TO_IGNORE = '#pragma once in main file'
TOO_MANY_ERRORS_DIAG_TEXT_TO_IGNORE = 'too many errors emitted, stopping now'


def to_utf8(value):
    if isinstance(value, unicode):
        return value.encode('utf-8')
    if isinstance(value, str):
        return value
    return str(value)


def get_unsaved_files_vector(files):
    files_vector = ycm_core.UnsavedFileVector()
    for f in files:
        unsaved_file = ycm_core.UnsavedFile()
        contents = open(f).read()
        unsaved_file.contents_ = contents
        unsaved_file.length_ = len(contents)
        unsaved_file.filename_ = to_utf8(f)
        files_vector.append(unsaved_file)
    return files_vector


def get_flags_vector(flags):
    flags_vector = ycm_core.StringVector()
    for f in flags:
        flags_vector.append(to_utf8(f))
    return flags_vector


def candidates_for_location_in_file(filename, line, col, flags, files=None):
    files = set() if files is None else files
    files.add(filename)
    filename = to_utf8(filename)
    files_vector = get_unsaved_files_vector(files)
    flags_vector = get_flags_vector(flags)
    results = completer.CandidatesForLocationInFile(
        filename,
        line,
        col,
        files_vector,
        flags_vector,
    )
    return [
        dict(
            insertion_text=completion_data.TextToInsertInBuffer(),
            menu_text=completion_data.MainCompletionText(),
            extra_menu_info=completion_data.ExtraMenuInfo(),
            kind=completion_data.kind_.name,
            detailed_info=completion_data.DetailedInfoForPreviewWindow(),
            extra_data={'doc_string': completion_data.DocString()} if completion_data.DocString() else None)
        for completion_data in results]


def run_op(op, filename, line, col, flags, files=None, reparse=True):
    files = set() if files is None else files
    files.add(filename)
    filename = to_utf8(filename)
    files_vector = get_unsaved_files_vector(files)
    flags_vector = get_flags_vector(flags)
    results = getattr(completer, op)(
        filename,
        line,
        col,
        files_vector,
        flags_vector,
        reparse,
    )
    if results:
        if isinstance(results, (basestring, int, float, long, complex, list, tuple, bool)):
            return results
        if getattr(results, 'IsValid', lambda: True)():
            return dict((attr.rstrip('_'), getattr(results, attr)) for attr in dir(results) if attr[0] != '_' and attr.islower())

get_declaration_location = lambda *args: run_op('GetDeclarationLocation', *args)
get_definition_location = lambda *args: run_op('GetDefinitionLocation', *args)
get_docs_for_location_in_file = lambda *args: run_op('GetDocsForLocationInFile', *args)
get_type_at_location = lambda *args: run_op('GetTypeAtLocation', *args)
get_enclosing_function_at_location = lambda *args: run_op('GetEnclosingFunctionAtLocation', *args)
get_fix_its_for_location_in_file = lambda *args: run_op('GetFixItsForLocationInFile', *args)


def update_translation_unit(filename, flags, files=None):
    files = set() if files is None else files
    files.add(filename)
    filename = to_utf8(filename)
    files_vector = get_unsaved_files_vector(files)
    flags_vector = get_flags_vector(flags)
    diagnostics = completer.UpdateTranslationUnit(
        filename,
        files_vector,
        flags_vector)
    structure = defaultdict(lambda: defaultdict(list))
    for diagnostic in diagnostics:
        if diagnostic.text_ not in (PRAGMA_DIAG_TEXT_TO_IGNORE, TOO_MANY_ERRORS_DIAG_TEXT_TO_IGNORE):
            structure[diagnostic.location_.filename_][diagnostic.location_.line_number_].append(diagnostic)
    return structure


def gen_files_under_dirs(dirs, max_depth, interesting_file_patterns=None,
                         skip_scc_control_dirs=True):
    from os.path import normpath, abspath, expanduser
    from fnmatch import fnmatch

    dirs_to_skip = (skip_scc_control_dirs and ["CVS", ".svn", ".hg", ".git", ".bzr"] or [])
    walked_these_dirs = {}
    for dir in dirs:
        norm_dir = normpath(abspath(expanduser(dir)))
        LEN_DIR = len(norm_dir)
        for dirpath, dirnames, filenames in util.walk2(norm_dir):
            if dirpath in walked_these_dirs:
                dirnames[:] = []  # Already walked - no need to do it again.
                continue
            if dirpath[LEN_DIR:].count(os.sep) >= max_depth:
                dirnames[:] = []  # hit max_depth
            else:
                walked_these_dirs[dirpath] = True
                for dir_to_skip in dirs_to_skip:
                    if dir_to_skip in dirnames:
                        dirnames.remove(dir_to_skip)
            if interesting_file_patterns:
                for pat, filename in ((p, f) for p in interesting_file_patterns for f in filenames):
                    if fnmatch(filename, pat):
                        yield os.path.join(dirpath, filename)

# ---- Lexer class

# Dev Notes:
# Komodo's editing component is based on scintilla (scintilla.org). This
# project provides C++-based lexers for a number of languages -- these
# lexers are used for syntax coloring and folding in Komodo. Komodo also
# has a UDL system for writing UDL-based lexers that is simpler than
# writing C++-based lexers and has support for multi-language files.
#
# The codeintel system has a Lexer class that is a wrapper around these
# lexers. You must define a Lexer class for lang Cpp. If Komodo's
# scintilla lexer for Cpp is UDL-based, then this is simply:
#
#   from codeintel2.udl import UDLLexer
#   class CppLexer(UDLLexer):
#       lang = lang
#
# Otherwise (the lexer for Cpp is one of Komodo's existing C++ lexers
# then this is something like the following. See lang_python.py or
# lang_perl.py in your Komodo installation for an example. "SilverCity"
# is the name of a package that provides Python module APIs for Scintilla
# lexers.
#
#   import SilverCity
#   from SilverCity.Lexer import Lexer
#   from SilverCity import ScintillaConstants
#   class CppLexer(Lexer):
#       lang = lang
#       def __init__(self):
#           self._properties = SilverCity.PropertySet()
#           self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_CPP)
#           self._keyword_lists = [
#               # Dev Notes: What goes here depends on the C++ lexer
#               # implementation.
#           ]

class CppLexer(Lexer):
    lang = lang

    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_CPP)
        self._keyword_lists = [
            # Dev Notes: What goes here depends on the C++ lexer
            # implementation.
        ]


# ---- LangIntel class

# Dev Notes:
# All language should define a LangIntel class. (In some rare cases it
# isn't needed but there is little reason not to have the empty subclass.)
#
# One instance of the LangIntel class will be created for each codeintel
# language. Code browser functionality and some buffer functionality
# often defers to the LangIntel singleton.
#
# This is especially important for multi-lang files. For example, an
# HTML buffer uses the JavaScriptLangIntel and the CSSLangIntel for
# handling codeintel functionality in <script> and <style> tags.
#
# See other lang_*.py files in your Komodo installation for examples of
# usage.

class CppLangIntel(CitadelLangIntel, ParenStyleCalltipIntelMixin,
                   ProgLangTriggerIntelMixin):
    lang = lang
    extraPathsPrefName = "cppExtraPaths"

    # Used by ProgLangTriggerIntelMixin.preceding_trg_from_pos()
    calltip_trg_chars = tuple('(')
    trg_chars = tuple(' .>:"')

    ##
    # Implicit triggering event, i.e. when typing in the editor.
    #
    def trg_from_pos(self, buf, pos, implicit=True, DEBUG=False, ac=None):
        print "trg_from_pos"
        print pos, implicit, ac

        DEBUG = True
        if pos < 1:
            return None

        accessor = buf.accessor
        last_pos = pos - 1
        char = accessor.char_at_pos(last_pos)
        style = accessor.style_at_pos(last_pos)
        if DEBUG:
            print "trg_from_pos: char: %r, style: %d" % (char, style)

        if char in self.trg_chars:  # must be "complete-object-members" or None
            log.debug("  triggered 'complete-object-members'")
            return Trigger(self.lang, TRG_FORM_CPLN, "object-members", pos, implicit)
        elif char in self.calltip_trg_chars:
            log.debug("  triggered 'calltip-call-signature'")
            return Trigger(self.lang, TRG_FORM_CALLTIP, "call-signature", pos, implicit)

        log.debug("  triggered 'complete-any'")
        return Trigger(self.lang, TRG_FORM_CPLN, "any", pos, implicit)

    ##
    # Explicit triggering event, i.e. Ctrl+J.
    #
    def preceding_trg_from_pos(self, buf, pos, curr_pos,
                               preceding_trg_terminators=None, DEBUG=False):
        print "preceding_trg_from_pos"
        print pos, curr_pos, preceding_trg_terminators

        DEBUG = True
        if pos < 1:
            return

        accessor = buf.accessor
        last_pos = pos - 1
        char = accessor.char_at_pos(last_pos)
        style = accessor.style_at_pos(last_pos)
        if DEBUG:
            print "pos: %d, curr_pos: %d" % (pos, curr_pos)
            print "char: %r, style: %d" % (char, style)

    ##
    # Provide the list of completions or the calltip string.
    # Completions are a list of tuple (type, name) items.
    #
    # Note: This example is *not* asynchronous.
    def async_eval_at_trg(self, buf, trg, ctlr):
        print "async_eval_at_trg"
        print trg, ctlr

        ctlr.start(buf, trg)

        filename = buf.path
        line, col = buf.accessor.line_and_col_at_pos(trg.pos)
        line += 1
        col += 1

        print "line:%s, col:%s" % (line, col)

        env = buf.env

        flags = env.get_pref('cppFlags', ())

        extra_dirs = set()
        for pref in env.get_all_prefs(self.extraPathsPrefName):
            if not pref:
                continue
            extra_dirs.update(d.strip() for d in pref.split(os.pathsep)
                              if os.path.exists(d.strip()))

        files = None
        if extra_dirs:
            log.debug("%s extra lib dirs: %r", self.lang, extra_dirs)
            max_depth = env.get_pref("codeintel_max_recursive_dir_depth", 10)
            files = set(gen_files_under_dirs(extra_dirs, max_depth,
                interesting_file_patterns=['*.c', '*.cpp', '*.cc', '*.objc', '*.objcpp', '*.m', '*.mm'],
                skip_scc_control_dirs=True))

        if trg.form == TRG_FORM_CPLN:
            candidates = candidates_for_location_in_file(filename, line, col, flags, files)
            if not candidates:
                ctlr.error("couldn't determine leading expression")
                ctlr.done("error")
                return

            cplns = []
            for candidate in candidates:
                cplns.append((candidate['kind'].lower(), candidate['insertion_text']))

            ctlr.set_cplns(cplns)

        elif trg.form == TRG_FORM_DEFN:
            defn = get_definition_location(filename, line, col, flags, files)
            if defn and filename == defn['filename'] and line == defn['line_number']:
                defn = None

            if not defn:
                defn = get_declaration_location(filename, line, col, flags, files)
                if defn and filename == defn['filename'] and line == defn['line_number']:
                    ctlr.done("success")
                    return

            if not defn:
                ctlr.error("couldn't determine leading expression")
                ctlr.done("error")
                return

            doc = get_docs_for_location_in_file(filename, line, col, flags, files)

            print 'defn', defn
            print 'doc', doc

            # col = defn['column_number']
            line = defn['line_number']
            path = defn['filename']
            name = None
            ilk = None
            signature = None

            d = Definition(
                self.lang,
                path,
                blobname=None,
                lpath=None,
                name=name,
                line=line,
                ilk=ilk,
                citdl=None,
                signature=signature,
                doc=doc,
            )
            ctlr.set_defns([d])

        elif trg.form == TRG_FORM_CALLTIP:
            typ = get_type_at_location(filename, line, col, flags)
            print 'typ', typ

            defn = get_definition_location(filename, line, col, flags)
            if not defn:
                defn = get_declaration_location(filename, line, col, flags)
            if not defn:
                ctlr.error("couldn't determine leading expression")
                ctlr.done("error")
                return

            print 'defn', defn

            ctlr.set_calltips(['%s %s' % ('name', 'type')])
            ctlr.done("success")

        ctlr.done("success")


# ---- Buffer class

# Dev Notes:
# Every language must define a Buffer class. An instance of this class
# is created for every file of this language opened in Komodo. Most of
# that APIs for scanning, looking for autocomplete/calltip trigger points
# and determining the appropriate completions and calltips are called on
# this class.
#
# Currently a full explanation of these API is beyond the scope of this
# stub. Resources for more info are:
# - the base class definitions (Buffer, CitadelBuffer, UDLBuffer) for
#   descriptions of the APIs
# - lang_*.py files in your Komodo installation as examples
# - the upcoming "Anatomy of a Komodo Extension" tutorial
# - the Komodo community forums:
#   http://community.activestate.com/products/Komodo
# - the Komodo discussion lists:
#   http://listserv.activestate.com/mailman/listinfo/komodo-discuss
#   http://listserv.activestate.com/mailman/listinfo/komodo-beta
#

class CppBuffer(CitadelBuffer):
    lang = lang
    # '/' removed as import packages use that
    cpln_fillup_chars = "~`!@#$%^&()-=+{}[]|\\;:'\",.<>? "
    cpln_stop_chars = "~`!@#$%^&*()-=+{}[]|\\;:'\",.<>? "
    ssl_lang = lang
    # The ScintillaConstants all start with this prefix:
    sce_prefixes = ["SCE_C_"]


# ---- CILE Driver class

# Dev Notes:
# A CILE (Code Intelligence Language Engine) is the code that scans
# Cpp content and returns a description of the code in that file.
# See "cile_cpp.py" for more details.
#
# The CILE Driver is a class that calls this CILE. If Cpp is
# multi-lang (i.e. can contain sections of different language content,
# e.g. HTML can contain markup, JavaScript and CSS), then you will need
# to also implement "scan_multilang()".
class CppCILEDriver(CILEDriver):
    lang = lang
    ssl_lang = lang

    def scan_purelang(self, buf):
        print "scan_purelang(%s)" % buf.path
        log.info("scan '%s'", buf.path)

        if sys.platform.startswith("win"):
            path = buf.path.replace('\\', '/')
        else:
            path = buf.path

        # update_translation_unit(buf.path, flags)  # FIXME

        output = '<file path="%s" lang="%s"></file>' % (path, lang)

        xml = '<codeintel version="2.0">\n' + output + '</codeintel>'
        return tree_from_cix(xml)


# ---- registration

def register(mgr):
    """Register language support with the Manager."""
    mgr.set_lang_info(
        lang,
        silvercity_lexer=CppLexer(),
        buf_class=CppBuffer,
        langintel_class=CppLangIntel,
        import_handler_class=None,
        cile_driver_class=CppCILEDriver,
        # Dev Note: set to false if this language does not support
        # autocomplete/calltips.
        is_cpln_lang=True)
