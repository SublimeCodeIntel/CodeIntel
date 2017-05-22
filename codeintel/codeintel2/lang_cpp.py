#!/usr/bin/env python

"""C/C++ support for codeintel.
This file will be imported by the codeintel system on startup and the
register() function called to register this language with the system. All
Code Intelligence for this language is controlled through this module.
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import re
import logging
try:
    from string import maketrans
except ImportError:
    maketrans = str.maketrans

import SilverCity
from SilverCity.Lexer import Lexer
from SilverCity import ScintillaConstants
from codeintel2.common import Trigger, TRG_FORM_CPLN, TRG_FORM_CALLTIP, TRG_FORM_DEFN, CILEDriver, Definition
from codeintel2.citadel import CitadelLangIntel, CitadelBuffer
from codeintel2.langintel import (ParenStyleCalltipIntelMixin,
                                  ProgLangTriggerIntelMixin)
from codeintel2.tree import tree_from_cix
from codeintel2.libclang import ClangCompleter

# ---- globals

lang = "C++"
extraPathsPrefName = "cppExtraPaths"

log = logging.getLogger("codeintel.cpp")
# log.setLevel(logging.DEBUG)


completer = ClangCompleter(log=log)


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
    extraPathsPrefName = extraPathsPrefName

    # Used by ProgLangTriggerIntelMixin.preceding_trg_from_pos()
    calltip_trg_chars = tuple('(')
    trg_chars = tuple(' .>:"')

    ##
    # Implicit triggering event, i.e. when typing in the editor.
    #
    def trg_from_pos(self, buf, pos, implicit=True, DEBUG=False, ac=None):
        print("trg_from_pos")
        print(pos, implicit, ac)

        DEBUG = True
        if pos < 1:
            return None

        accessor = buf.accessor
        last_pos = pos - 1
        char = accessor.char_at_pos(last_pos)
        style = accessor.style_at_pos(last_pos)
        if DEBUG:
            print("trg_from_pos: char: %r, style: %d" % (char, style))

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
        print("preceding_trg_from_pos")
        print(pos, curr_pos, preceding_trg_terminators)

        DEBUG = True
        if pos < 1:
            return

        accessor = buf.accessor
        last_pos = pos - 1
        char = accessor.char_at_pos(last_pos)
        style = accessor.style_at_pos(last_pos)
        if DEBUG:
            print("pos: %d, curr_pos: %d" % (pos, curr_pos))
            print("char: %r, style: %d" % (char, style))

    ##
    # Provide the list of completions or the calltip string.
    # Completions are a list of tuple (type, name) items.
    #
    # Note: This example is *not* asynchronous.
    def async_eval_at_trg(self, buf, trg, ctlr):
        print("async_eval_at_trg")
        print(trg, ctlr)

        ctlr.start(buf, trg)

        filename = buf.path
        line, col = buf.accessor.line_and_col_at_pos(trg.pos)
        line += 1
        col += 1

        print("line:%s, col:%s" % (line, col))

        env = buf.env
        flags = env.get_pref('cppFlags', [])
        extra_dirs = []
        for pref in env.get_all_prefs(self.extraPathsPrefName):
            if not pref:
                continue
            extra_dirs.extend(d.strip() for d in pref.split(os.pathsep) if os.path.exists(d.strip()) and d.strip() not in extra_dirs)
        if extra_dirs:
            flags = flags + ['-I{}'.format(extra_dir) for extra_dir in extra_dirs]
        flags = flags + ['-I{}'.format(os.path.dirname(filename)), '-I.']

        content = buf.accessor.text

        if trg.form == TRG_FORM_CPLN:
            candidates = completer.getCurrentCompletions(filename, line, col, fileBuffer=content, flags=flags, include_macros=True, include_code_patterns=True, include_brief_comments=True)

            if not candidates:
                ctlr.error("couldn't determine leading expression")
                ctlr.done("error")
                return

            cplns = []
            for candidate in candidates:
                cplns.append((candidate['kind'], candidate['abbr'], candidate['info']))

            ctlr.set_cplns(cplns)

        elif trg.form == TRG_FORM_DEFN:
            defn = completer.gotoDeclaration(filename, line, col, fileBuffer=content, flags=flags)
            print('defn', defn)

            if not defn:
                ctlr.error("couldn't determine leading expression")
                ctlr.done("error")
                return

            # doc = get_docs_for_location_in_file(filename, line, col, flags, files)
            # print('doc', doc)

            line = defn['line']
            # col = defn['column']
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
                doc=None,
                signature=signature,
            )
            ctlr.set_defns([d])

        elif trg.form == TRG_FORM_CALLTIP:
            prefix = self._prefix(buf.accessor.text_range(max(0, trg.pos - 200), trg.pos))
            print('prefix', prefix)

            candidates = completer.getCurrentCompletions(filename, line, col, fileBuffer=content, flags=flags, prefix=prefix, include_macros=True, include_code_patterns=True, include_brief_comments=True)

            if not candidates:
                ctlr.error("couldn't determine leading expression")
                ctlr.done("error")
                return

            ctlr.set_calltips([candidate['menu'] for candidate in candidates])
            ctlr.done("success")

        ctlr.done("success")

    def _last_logical_line(self, text):
        lines = text.splitlines(0) or ['']
        logicalline = lines.pop()
        while lines and lines[-1].endswith('\\'):
            logicalline = lines.pop()[:-1] + ' ' + logicalline
        return logicalline

    cpln_prefix_re = re.compile(r'''[-~`!@#$%^&*()=+{}[\]|\\;:'",.<>? \t]+''')

    def _prefix(self, text):
        line = self._last_logical_line(text)
        return self.cpln_prefix_re.sub(' ', line).rstrip().rpartition(' ')[-1]


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
    extraPathsPrefName = extraPathsPrefName

    def scan_purelang(self, buf):
        print("scan_purelang(%s)" % buf.path)
        log.info("scan '%s'", buf.path)

        filename = buf.path

        env = buf.env
        flags = env.get_pref('cppFlags', [])
        extra_dirs = []
        for pref in env.get_all_prefs(self.extraPathsPrefName):
            if not pref:
                continue
            extra_dirs.extend(d.strip() for d in pref.split(os.pathsep) if os.path.exists(d.strip()) and d.strip() not in extra_dirs)
        if extra_dirs:
            flags = flags + ['-I{}'.format(extra_dir) for extra_dir in extra_dirs]
        flags = flags + ['-I{}'.format(os.path.dirname(filename)), '-I.']

        content = buf.accessor.text
        completer.warmupCache(buf.path, fileBuffer=content, flags=flags)

        output = '<file path="%s" lang="%s"></file>' % (filename, lang)

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
