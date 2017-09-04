#!/usr/bin/env python
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
# The Original Code is Komodo code.
#
# The Initial Developer of the Original Code is ActiveState Software Inc.
# Portions created by ActiveState Software Inc are Copyright (C) 2000-2007
# ActiveState Software Inc. All Rights Reserved.
#
# Portions created by German Mendez Bravo (Kronuz) are Copyright (C) 2017
# German Mendez Bravo (Kronuz). All Rights Reserved.
#
# Contributor(s):
#   ActiveState Software Inc
#   German Mendez Bravo (Kronuz) (german.mb@gmail.com)
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

"""ECMAScript support for CodeIntel"""

from __future__ import absolute_import, print_function, division

import os
from os.path import exists, dirname, join, normcase, basename
import sys
import logging
from glob import glob
import weakref
import re
from pprint import pformat
import json
from collections import namedtuple

from SilverCity import ScintillaConstants

from codeintel2.common import (_xpcom_, CILEDriver, Evaluator,
                               CodeIntelError, LazyClassAttribute,
                               Trigger, TRG_FORM_CPLN, TRG_FORM_DEFN, TRG_FORM_CALLTIP)
from codeintel2.citadel import ImportHandler, CitadelLangIntel
from codeintel2.indexer import PreloadLibRequest
from codeintel2 import ecmacile
from codeintel2.util import indent, isident, isdigit, makePerformantLogger
from codeintel2.tree_ecma import ECMAScriptTreeEvaluator, ECMAScriptImportLibGenerator
from codeintel2.langintel import (ParenStyleCalltipIntelMixin,
                                  ProgLangTriggerIntelMixin,
                                  PythonCITDLExtractorMixin)
from codeintel2.jsdoc import jsdoc_tags
from codeintel2.udl import UDLLexer, XMLParsingBufferMixin, styles

if _xpcom_:
    from xpcom.server import UnwrapObject


# ---- globals

_SCAN_BINARY_FILES = False

lang = "ECMAScript"
log = logging.getLogger("codeintel.ecmascript")
log.setLevel(logging.DEBUG)
makePerformantLogger(log)

_g_jsdoc_cplns = [("variable", t) for t in sorted(jsdoc_tags)]


# ---- language support

class ECMAScriptLexer(UDLLexer):
    lang = "JSX"  # Use JSX UDL lexer


class ECMAScriptImportsEvaluator(Evaluator):
    lang = lang

    def __str__(self):
        return "ECMAScript imports"

    def eval(self, mgr):
        try:
            imp_prefix = tuple(self.trg.extra["imp_prefix"])
            if imp_prefix:
                libs = self.buf.libs
                if not imp_prefix[0]:
                    if not imp_prefix[-1]:
                        # Deal with last item being empty, i.e. "from ."
                        imp_prefix = imp_prefix[:-1]
                    lookuppath = self.buf.path
                    while imp_prefix and not imp_prefix[0]:
                        lookuppath = dirname(lookuppath)
                        imp_prefix = imp_prefix[1:]
                    libs = [mgr.db.get_lang_lib(self.lang, "curdirlib", [lookuppath])]
                else:
                    # We use a special lib generator - that will lazily load
                    # additional directory libs when there are no matches found.
                    # This is a smart import facility - to detect imports from
                    # a parent directory when they are not explicitly on the
                    # included path list, quite common for Django and other
                    # ECMAScript frameworks that mangle the sys.path at runtime.
                    libs = ECMAScriptImportLibGenerator(mgr, self.lang, self.buf.path, imp_prefix, libs)
                self.ctlr.set_desc("subimports of '%s'" % '.'.join(imp_prefix))
                cplns = []
                for lib in libs:
                    imports = lib.get_blob_imports(imp_prefix)
                    if imports:
                        cplns.extend(
                            ((is_dir_import and "directory" or "module"), name)
                            for name, is_dir_import in imports
                        )

                    if self.trg.type == "module-members":
                        # Also add top-level members of the specified module.
                        dotted_prefix = '.'.join(imp_prefix)
                        if lib.has_blob(dotted_prefix):
                            blob = lib.get_blob(dotted_prefix)
                            for name in blob.names:
                                elem = blob.names[name]
                                cplns.append((elem.get("ilk") or elem.tag, name))

                            # TODO: Consider using the value of __all__
                            #      if defined.
                            for e in blob:
                                attrs = e.get("attributes", "").split()
                                if "__hidden__" not in attrs:
                                    try:
                                        cplns += self._members_from_elem(e, mgr)
                                    except CodeIntelError as ex:
                                        log.warn("%s (skipping members for %s)", ex, e)
                    if cplns:
                        break
                if cplns:
                    cplns = list(set(cplns))  # remove duplicates
            else:
                self.ctlr.set_desc("available imports")
                all_imports = set()
                for lib in self.buf.libs:
                    all_imports.update(lib.get_blob_imports(imp_prefix))
                cplns = [((is_dir_import and "directory" or "module"), name)
                         for name, is_dir_import in all_imports]
            if cplns:
                cplns.sort(key=lambda i: i[1].upper())
                self.ctlr.set_cplns(cplns)
        finally:
            self.ctlr.done("success")

    # XXX: This function is shamelessly copy/pasted from
    #     tree_ecma.py:ECMAScriptTreeEvaluator because there was no clear
    #     way to reuse this shared functionality. See another XXX below, though.
    def _members_from_elem(self, elem, mgr):
        """Return the appropriate set of autocomplete completions for
        the given element. Typically this is just one, but can be more for
        '*'-imports
        """
        members = set()
        if elem.tag == "import":
            alias = elem.get("alias")
            symbol_name = elem.get("symbol")
            module_name = elem.get("module")
            if symbol_name:
                import_handler = mgr.citadel.import_handler_from_lang(self.trg.lang)
                try:
                    blob = import_handler.import_blob_name(module_name, self.buf.libs, self.ctlr)
                except:
                    log.warn("limitation in handling imports in imported modules")
                    raise

                if symbol_name == "*":  # can it be so?
                    for m_name, m_elem in blob.names.items():
                        m_type = m_elem.get("ilk") or m_elem.tag
                        members.add((m_type, m_name))
                elif symbol_name in blob.names:
                    symbol = blob.names[symbol_name]
                    member_type = (symbol.get("ilk") or symbol.tag)
                    members.add((member_type, alias or symbol_name))
                else:
                    # To correctly determine the type, we'd need to
                    # examine all the imports of this blob, and then see
                    # if any of those imports match the name... which is
                    # better left to the tree evaluator (tree_ecma).
                    #
                    # For now, we just add it as an unknown type.
                    members.add(('unknown', alias or symbol_name))
                    log.info("could not resolve symbol %r on %r, added as 'unknown'",
                             symbol_name, module_name)
            else:
                cpln_name = alias or module_name.split('.', 1)[0]
                members.add(("module", cpln_name))
        else:
            members.add((elem.get("ilk") or elem.tag, elem.get("name")))
        return members


class ECMAScriptLangIntel(CitadelLangIntel,
                          ParenStyleCalltipIntelMixin,
                          ProgLangTriggerIntelMixin,
                          PythonCITDLExtractorMixin):
    lang = lang
    interpreterPrefName = "node"
    extraPathsPrefName = "ecmascriptExtraPaths"
    excludePathsPrefName = "ecmascriptExcludePaths"

    # Define the trigger chars we use, used by ProgLangTriggerIntelMixin
    trg_chars = tuple(".(,@'\" ")
    calltip_trg_chars = tuple('(')   # excluded ' ' for perf (bug 55497)
    # Define literal mapping to citdl member, used in PythonCITDLExtractorMixin
    citdl_from_literal_type = {"string": "String"}

    @LazyClassAttribute
    def keywords(self):
        from SilverCity.Keywords import python_keywords
        return python_keywords.split(" ")

    def citdl_expr_from_trg(self, buf, trg):
        citdl_expr = super(ECMAScriptLangIntel, self).citdl_expr_from_trg(buf, trg)
        citdl_expr = citdl_expr.lstrip('.')  # Remove leading dots from spreads
        return citdl_expr

    def async_eval_at_trg(self, buf, trg, ctlr):
        if _xpcom_:
            trg = UnwrapObject(trg)
            ctlr = UnwrapObject(ctlr)
        ctlr.start(buf, trg)

        # JSDoc completions
        if trg.id == (self.lang, TRG_FORM_CPLN, "jsdoc-tags"):
            # TODO: Would like a "javadoc tag" completion image name.
            ctlr.set_cplns(_g_jsdoc_cplns)
            ctlr.done("success")

        # JSDoc calltip
        elif trg.id == (self.lang, TRG_FORM_CALLTIP, "jsdoc-tags"):
            # TODO: Would like a "javadoc tag" completion image name.
            jsdoc_field = trg.extra.get("jsdoc_field")
            if jsdoc_field:
                # print("jsdoc_field: %r" % (jsdoc_field, ))
                calltip = jsdoc_tags.get(jsdoc_field)
                if calltip:
                    ctlr.set_calltips([calltip])
            ctlr.done("success")

        elif trg.type in ("object-members", "object-properties", "call-signature", "literal-members") or trg.form == TRG_FORM_DEFN:
            line = buf.accessor.line_from_pos(trg.pos)
            if trg.type == "literal-members":
                # We could leave this to citdl_expr_from_trg, but this is a
                # little bit faster, since we already know the citdl expr.
                citdl_expr = trg.extra.get("citdl_expr")
            else:
                try:
                    citdl_expr = self.citdl_expr_from_trg(buf, trg)
                    if citdl_expr.startswith("require()"):
                        # Ugly hack passing required_library_name with trg
                        # so it can resolve required defn
                        working_text = buf.accessor.text_range(trg.pos - 200, trg.pos)
                        needle = working_text.rfind("require(")
                        if needle > 0:
                            working_text = working_text[needle + 8:]
                            needle = working_text.find(working_text[0], 1)
                            if needle > 0:
                                working_text = working_text[1:needle]
                                if working_text:
                                    trg.required_library_name = working_text
                except CodeIntelError as ex:
                    ctlr.error(str(ex))
                    ctlr.done("error")
                    return
            evalr = ECMAScriptTreeEvaluator(ctlr, buf, trg, citdl_expr, line)
            buf.mgr.request_eval(evalr)

        elif trg.id == (self.lang, TRG_FORM_CPLN, "local-symbols"):
            line = buf.accessor.line_from_pos(trg.pos)
            citdl_expr = trg.extra.get("citdl_expr")
            evalr = ECMAScriptTreeEvaluator(ctlr, buf, trg, citdl_expr, line)
            buf.mgr.request_eval(evalr)

        elif trg.type == "available-exceptions":
            evalr = ECMAScriptTreeEvaluator(ctlr, buf, trg, None, -1)
            buf.mgr.request_eval(evalr)

        elif trg.type in ("available-imports", "module-members"):
            evalr = ECMAScriptImportsEvaluator(ctlr, buf, trg)
            buf.mgr.request_eval(evalr)

        else:
            raise NotImplementedError("not yet implemented: completion for "
                                      "ECMAScript '%s' trigger" % trg.name)

    info_cmd = (
        r"process.stdout.write(process.version + '\n');"
        r"process.stdout.write(process.config.variables.node_prefix + '\n');"
        r"module.paths.forEach(function(p){process.stdout.write(p + '\n')})")

    def _node_info_from_node(self, node, env):
        """Call the given ECMAScript and return:
            (<version>, <node_prefix>, <lib-dir>, <site-lib-dir>, <sys.path>)

        TODO: Unicode path issues?
        """
        import process
        argv = [node, "-e", self.info_cmd]
        log.debug("run `%s -e ...'", node)
        p = process.ProcessOpen(argv, env=env.get_all_envvars(), stdin=None)
        stdout, stderr = p.communicate()
        stdout_lines = stdout.splitlines(0)
        retval = p.returncode
        if retval:
            log.warn("failed to determine ECMAScript info:\n"
                     "  path: %s\n"
                     "  retval: %s\n"
                     "  stdout:\n%s\n"
                     "  stderr:\n%s\n",
                     node, retval, indent('\n'.join(stdout_lines)), indent(stderr))

        # We are only to rely on the first 2 digits being in the form x.y.
        ver_match = re.search(r"([0-9]+\.[0-9]+)", stdout_lines[0])
        if ver_match:
            ver = ver_match.group(1)
        else:
            ver = None
        prefix = stdout_lines[1]
        if sys.platform == "win32":
            libdir = join(prefix, "Lib")
        else:
            libdir = join(prefix, "lib", "node")
        sitelibdir = "/usr/local/lib/node_modules"
        sys_path = stdout_lines[2:]
        sys_path.append(os.path.expanduser("~/.node_modules"))
        sys_path.append(os.path.expanduser("~/.node_libraries"))
        return ver, prefix, libdir, sitelibdir, sys_path

    def _gen_es_import_paths_from_dirs(self, dirs):
        """Generate all ECMAScript import paths from a given list of dirs."""
        for dir in dirs:
            if not exists(dir):
                continue
            yield dir
            try:
                for pth_path in glob(join(dir, "*.pth")):
                    for p in self._gen_es_import_paths_from_pth_path(pth_path):
                        yield p
            except EnvironmentError as ex:
                log.warn("error analyzing .pth files in '%s': %s", dir, ex)

    def _gen_es_import_paths_from_pth_path(self, pth_path):
        pth_dir = dirname(pth_path)
        for line in open(pth_path, 'r'):
            line = line.strip()
            if line.startswith("#"):  # comment line
                continue
            path = join(pth_dir, line)
            if exists(path):
                yield path

    def _extra_dirs_from_env(self, env):
        extra_dirs = set()
        exclude_dirs = set()
        for pref in env.get_all_prefs(self.extraPathsPrefName):
            if not pref:
                continue
            extra_dirs.update(d.strip() for d in pref.split(os.pathsep) if exists(d.strip()))
        for pref in env.get_all_prefs(self.excludePathsPrefName):
            if not pref:
                continue
            exclude_dirs.update(d.strip() for d in pref.split(os.pathsep) if exists(d.strip()))
        if extra_dirs:
            extra_dirs = set(
                self._gen_es_import_paths_from_dirs(extra_dirs)
            )
            for exclude_dir in exclude_dirs:
                if exclude_dir in extra_dirs:
                    extra_dirs.remove(exclude_dir)
            log.debug("ECMAScript extra lib dirs: %r (minus %r)", extra_dirs, exclude_dirs)
        return tuple(extra_dirs)

    def interpreter_from_env(self, env):
        """Returns:
            - absolute path to either the preferred or
              default system interpreter
            - None if none of the above exists
        """
        # Gather information about the current node.
        node = None
        if env.has_pref(self.interpreterPrefName):
            node = env.get_pref(self.interpreterPrefName).strip() or None

        if not node or not exists(node):
            import which
            # Prefer the version-specific name, but we might need to use the
            # unversioned binary instead; for example, on Win32, ES3 only
            # ships with "node.exe"
            exe_names = ["node"]
            candidates = []
            for exe_name in exe_names:
                try:
                    candidates += which.whichall(exe_name)
                except which.WhichError:
                    pass
            for node in candidates:
                try:
                    if self._node_info_from_node(node, env)[0]:
                        break
                except:
                    pass
                    # log.debug("Failed to run %s", exe_name, exc_info=True)
            else:
                node = None

        if node:
            node = os.path.abspath(node)

        return node

    def ecmascript_info_from_env(self, env):
        cache_key = self.lang + "-info"
        info = env.cache.get(cache_key)
        if info is None:
            node = self.interpreter_from_env(env)
            if not node:
                log.warn("no ECMAScript was found from which to determine the "
                         "codeintel information")
                info = None, None, None, None, []
            else:
                info = self._node_info_from_node(node, env)
            env.cache[cache_key] = info
        return info

    def _get_stdlibs_from_env(self, env=None):
        libdir = os.path.join(os.path.dirname(__file__), "lib_srcs", "node.js")
        version = self.ecmascript_info_from_env(env)[0]
        if version:
            versioned_libdir = os.path.join(libdir, version)
        if version and os.path.isdir(versioned_libdir):
            # we have a lib matching the running version of Node.js
            libdir = versioned_libdir
        else:
            # No valid Node.js version, or no matching lib: use highest we have
            versions = [tuple(int(part or 0) for part in v.split("."))
                        for v in os.listdir(libdir)
                        if os.path.isdir(os.path.join(libdir, v)) and not v.strip("0123456789.")]
            if versions:
                max_version = sorted(versions, reverse=True)[0]
                version = ".".join(str(v) for v in max_version)
                libdir = os.path.join(libdir, version)
        db = self.mgr.db
        return db.get_lang_lib(lang="ECMAScript",
                               name="node.js stdlib",
                               dirs=(libdir,))

    def _buf_indep_libs_from_env(self, env):
        """Create the buffer-independent list of libs."""
        cache_key = self.lang + "-libs"
        libs = env.cache.get(cache_key)
        if libs is None:
            env.add_pref_observer(self.interpreterPrefName, self._invalidate_cache)
            env.add_pref_observer(self.extraPathsPrefName,
                                  self._invalidate_cache_and_rescan_extra_dirs)
            env.add_pref_observer(self.excludePathsPrefName,
                                  self._invalidate_cache_and_rescan_extra_dirs)
            env.add_pref_observer("codeintel_selected_catalogs",
                                  self._invalidate_cache)
            db = self.mgr.db

            ver, prefix, libdir, sitelibdir, sys_path = self.ecmascript_info_from_env(env)
            libs = []

            # - extradirslib
            extra_dirs = self._extra_dirs_from_env(env)
            if extra_dirs:
                libs.append(db.get_lang_lib(self.lang, "extradirslib", extra_dirs))

            # Figure out which sys.path dirs belong to which lib.
            paths_from_libname = {"sitelib": [], "envlib": [], "stdlib": []}
            canon_sitelibdir = sitelibdir and normcase(sitelibdir) or None
            canon_prefix = prefix and normcase(prefix) or None
            canon_libdir = libdir and normcase(libdir) or None
            canon_libdir_plat_prefix = libdir and normcase(join(libdir, "plat-")) or None
            canon_libdir_lib_prefix = libdir and normcase(join(libdir, "lib-")) or None
            for dir in sys_path:
                STATE = "envlib"
                canon_dir = normcase(dir)
                if dir == "":  # -> curdirlib (already handled)
                    continue
                elif canon_dir.startswith(canon_sitelibdir):
                    STATE = "sitelib"
                # Check against the known list of standard library locations.
                elif (
                    canon_dir == canon_libdir or
                    canon_dir.startswith(canon_libdir_plat_prefix) or
                    canon_dir.startswith(canon_libdir_lib_prefix)
                ):
                    STATE = "stdlib"
                if not exists(dir):
                    continue
                paths_from_libname[STATE].append(dir)
            log.debug("ECMAScript %s paths for each lib:\n%s", ver, indent(pformat(paths_from_libname)))

            # - envlib, sitelib, cataloglib, nodelib, stdlib
            if paths_from_libname["envlib"]:
                libs.append(db.get_lang_lib(self.lang, "envlib", paths_from_libname["envlib"]))
            if paths_from_libname["sitelib"]:
                libs.append(db.get_lang_lib(self.lang, "sitelib", paths_from_libname["sitelib"]))
            catalog_selections = env.get_pref("codeintel_selected_catalogs")
            cataloglib = db.get_catalog_lib(self.lang, catalog_selections)
            nodelib = self._get_stdlibs_from_env(env)
            stdlib = db.get_stdlib(self.lang, ver)
            libs += [
                cataloglib,
                nodelib,
                stdlib,
            ]
            env.cache[cache_key] = libs

        return libs

    def _importables_from_dir(self, imp_dir):
        yield imp_dir

        cur_dir = None
        while cur_dir != imp_dir:
            if cur_dir and os.path.exists(os.path.join(cur_dir, "package.json")):
                yield cur_dir
            cur_dir = imp_dir
            path = os.path.join(cur_dir, "node_modules")
            if os.path.exists(path):
                yield path
            imp_dir = dirname(cur_dir)

    def libs_from_buf(self, buf):
        env = buf.env

        # A buffer's libs depend on its env and the buf itself so
        # we cache it on the env and key off the buffer.
        cache_key = self.lang + "-buf-libs"
        cache = env.cache.get(cache_key)  # <buf-weak-ref> -> <libs>
        if cache is None:
            cache = weakref.WeakKeyDictionary()
            env.cache[cache_key] = cache

        if buf not in cache:
            # - curdirlib
            # Using the dirname of this buffer isn't always right, but
            # hopefully is a good first approximation.
            libs = []
            if buf.path:
                cwd = dirname(buf.path)
                if cwd != "<Unsaved>":
                    dirs = list(self._importables_from_dir(cwd))
                    libs = [self.mgr.db.get_lang_lib(self.lang, "curdirlib", dirs)]

            libs += self._buf_indep_libs_from_env(env)
            cache[buf] = libs
        return cache[buf]

    def _invalidate_cache(self, env, pref_name):
        for key in (self.lang + "-buf-libs", self.lang + "-libs"):
            if key in env.cache:
                log.debug("invalidate '%s' cache on %r", key, env)
                del env.cache[key]

    def _invalidate_cache_and_rescan_extra_dirs(self, env, pref_name):
        self._invalidate_cache(env, pref_name)
        extra_dirs = self._extra_dirs_from_env(env)
        if extra_dirs:
            extradirslib = self.mgr.db.get_lang_lib(
                self.lang, "extradirslib", extra_dirs)
            request = PreloadLibRequest(extradirslib)
            self.mgr.idxr.stage_request(request, 1.0)


# class ESCitadelEvaluator(CitadelEvaluator):
#    def post_process_cplns(self, cplns):
#        """Drop special __FOO__ methods.

#        Note: Eventually for some ECMAScript completions we might want to leave
#        these in. For example:

#            class Bar(Foo):
#                def __init__(self):
#                    Foo.<|>    # completions should include "__init__" here
#        """
#        for i in range(len(cplns)-1, -1, -1):
#            value = cplns[i][1]
#            if value.startswith("__") and value.endswith("__"):
#                del cplns[i]
#        return CitadelEvaluator.post_process_cplns(self, cplns)


# ---- internal support stuff


def _isident(char):
    return "a" <= char <= "z" or "A" <= char <= "Z" or char == "_"


def _isdigit(char):
    return "0" <= char <= "9"


class ScintillaMixin(object):
    sce_lang = None

    # ---- Scintilla style helpers.
    def style_names_from_style_num(self, style_num):
        # XXX Would like to have python-foo instead of p_foo or SCE_P_FOO, but
        #    that requires a more comprehensive solution for all langs and
        #    multi-langs.
        style_names = []

        # Get the constant name from ScintillaConstants.
        if self.sce_lang not in self._style_name_from_style_num_from_lang:
            name_from_num \
                = self._style_name_from_style_num_from_lang[self.sce_lang] = {}
            sce_prefixes = self.sce_prefixes
            if sce_prefixes is None:
                # Try and guess the prefix then.
                log.warn("Guessing sce_prefix as 'SCE_%s_' - if that's not "
                         "correct then define 'sce_prefixes' on your buffer"
                         "class", self.sce_lang.upper())
                sce_prefixes = ["SCE_%s_" % (self.sce_lang.upper())]
            for attr in dir(ScintillaConstants):
                for sce_prefix in sce_prefixes:
                    if attr.startswith(sce_prefix):
                        name_from_num[getattr(ScintillaConstants, attr)] = attr
        else:
            name_from_num \
                = self._style_name_from_style_num_from_lang[self.sce_lang]
        const_name = self._style_name_from_style_num_from_lang[self.sce_lang].get(style_num, "Unknown style")
        style_names.append("%d - %s" % (style_num, const_name))

        # Get a style group from styles.py.
        if self.sce_lang in styles.StateMap:
            for style_group, const_names in styles.StateMap[self.sce_lang].items():
                if const_name in const_names:
                    style_names.append(style_group)
                    break
        else:
            log.warn("lang '%s' not in styles.StateMap: won't have "
                     "common style groups in HTML output" % self.sce_lang)

        return style_names

    __string_styles = None

    def string_styles(self):
        if self.__string_styles is None:
            state_map = styles.StateMap[self.sce_lang]
            self.__string_styles = [
                getattr(ScintillaConstants, style_name)
                for style_class in ("strings", "stringeol")
                for style_name in state_map.get(style_class, [])
            ]
        return self.__string_styles

    __comment_styles = None

    def comment_styles(self):
        if self.__comment_styles is None:
            state_map = styles.StateMap[self.sce_lang]
            self.__comment_styles = [
                getattr(ScintillaConstants, style_name)
                for style_class in ("comments", "here documents",
                                    "data sections")
                for style_name in state_map.get(style_class, [])
            ]
        return self.__comment_styles

    __number_styles = None

    def number_styles(self):
        if self.__number_styles is None:
            state_map = styles.StateMap[self.sce_lang]
            self.__number_styles = [
                getattr(ScintillaConstants, style_name)
                for style_class in ("numbers",)
                for style_name in state_map.get(style_class, [])
            ]
        return self.__number_styles


class ECMAScriptBuffer(ScintillaMixin, XMLParsingBufferMixin):
    lang = lang
    m_lang = lang
    sce_lang = "UDL"

    # Fillup chars for ECMAScript: basically, any non-identifier char.
    # - remove '*' from fillup chars because: "from foo import <|>*"
    cpln_fillup_chars = "~`!@#$%^&()-=+{}[]|\\;:'\",.<>?/ "

    # Characters that should close an autocomplete UI:
    # - wanted for XML completion: ">'\" "
    # - wanted for CSS completion: " ('\";},.>"
    # - wanted for JS completion:  "~`!@#%^&*()-=+{}[]|\\;:'\",.<>?/ "
    # - dropping ':' because I think that may be a problem for XML tag
    #   completion with namespaces (not sure of that though)
    # - dropping '[' because need for "<!<|>" -> "<![CDATA[" cpln
    # - dropping '-' because causes problem with CSS (bug 78312)
    # - dropping '!' because causes problem with CSS "!important" (bug 78312)
    cpln_stop_chars = "'\" (;},~`@#%^&*()=+{}]|\\;,.<>?/"

    sce_prefixes = ["SCE_UDL_"]

    cb_show_if_empty = True

    keyword_style = ScintillaConstants.SCE_UDL_CSL_WORD
    identifier_style = ScintillaConstants.SCE_UDL_CSL_IDENTIFIER
    whitespace_style = ScintillaConstants.SCE_UDL_CSL_DEFAULT

    @property
    def libs(self):
        return self.langintel.libs_from_buf(self)

    def scoperef_from_blob_and_line(self, blob, line, pos):  # line is 1-based
        def _scoperef_from_blob_and_line(result, scope, parent_path):
            linestart = None
            lineend = None
            posstart = None
            posend = None
            subscopes = scope.findall("scope")
            for subscope in subscopes:
                path = parent_path + [subscope]
                # Get first and last line in all subecopes:
                _lstart, _lend, _pstart, _pend = _scoperef_from_blob_and_line(result, subscope, path)

                # Get current scope line and lineend:
                lstart = int(subscope.get("line", 0))
                lend = int(subscope.get("lineend", lstart))
                if _lstart is not None and lstart > _lstart:
                    lstart = _lstart
                if _lend is not None and lend < _lend:
                    lend = _lend
                # Keep track of linestart and lineend:
                if linestart is None or linestart > lstart:
                    linestart = lstart
                if lineend is None or lineend < lend:
                    lineend = lend

                # If the line being requested is in range...
                if line >= lstart and line <= lend:
                    dline = line - lstart
                    # Check if it's closer to the already found result:
                    if dline <= 0:
                        # It's at the line or after...
                        dline = -dline
                        if result.dline is None or dline < result.dline:
                            # it's closer, set result
                            result.dline = dline
                            result.path = path
                        elif dline == result.dline:
                            pstart = int(subscope.get("start", 0))
                            pend = int(subscope.get("end", pstart))
                            if _pstart is not None and pstart > _pstart:
                                pstart = _pstart
                            if _pend is not None and pend < _pend:
                                pend = _pend
                            # Keep track of posstart and posend:
                            if posstart is None or posstart > pstart:
                                posstart = pstart
                            if posend is None or posend < pend:
                                posend = pend
                            dpos = min(abs(pos - pstart), abs(pos - pend))
                            if result.dpos is None or dpos < result.dpos:
                                # it's the same but it's a deeper scope, set result
                                result.dpos = dpos
                                result.path = path
                    else:
                        # it's before...
                        if result.dline is None or dline < result.dline:
                            # it's closer, set result
                            result.dline = dline
                            result.path = path
            return linestart, lineend, posstart, posend
        result = namedtuple("Result", "dline path")
        result.dline = None
        result.dpos = None
        result.path = []
        linestart, lineend, posstart, posend = _scoperef_from_blob_and_line(result, blob, [])
        return (blob, [subscope.get("name") for subscope in result.path])

    def trg_from_pos(self, pos, implicit=True):
        """ECMAScript trigger types:

        ecmascript-complete-object-properties
        ecmascript-complete-object-members
        ecmascript-calltip-call-signature
        ecmascript-complete-jsdoc-tags
        complete-available-imports
        complete-module-members

        Not yet implemented:
            complete-available-classes
            calltip-base-signature
        """
        DEBUG = True  # not using 'logging' system, because want to be fast
        if DEBUG:
            print("\n----- ECMAScript trg_from_pos(pos=%r, implicit=%r) -----" % (pos, implicit))

        if pos == 0:
            return None
        accessor = self.accessor
        last_pos = pos - 1
        last_char = accessor.char_at_pos(last_pos)
        if DEBUG:
            print("  last_pos: %s" % last_pos)
            print("  last_char: %r" % last_char)

        # Quick out if the preceding char isn't a trigger char.
        # Note: Cannot use this now that we have a 2-char locals trigger.
        # if last_char not in " .(@_,":
        #    if DEBUG:
        #        print "trg_from_pos: no: %r is not in ' .(@'_," % last_char
        #    return None

        style = accessor.style_at_pos(last_pos)
        if DEBUG:
            style_names = self.style_names_from_style_num(style)
            print("  style: %s (%s)" % (style, ", ".join(style_names)))

        # JSDoc completions
        if last_char == "@" and style in self.comment_styles():
            # If the preceeding non-whitespace character is a "*" or newline
            # then we complete for jsdoc tag names
            p = last_pos - 1
            min_p = max(0, p - 50)      # Don't bother looking more than 50 chars
            if DEBUG:
                print("Checking match for jsdoc completions")
            while p >= min_p and accessor.style_at_pos(p) in self.comment_styles():
                ch = accessor.char_at_pos(p)
                p -= 1
                # if DEBUG:
                #    print("Looking at ch: %r" % (ch))
                if ch == "*" or ch in "\r\n":
                    break
                elif ch not in " \t\v":
                    # Not whitespace, not a valid tag then
                    return None
            else:
                # Nothing found in the specified range
                if DEBUG:
                    print("trg_from_pos: not a jsdoc")
                return None
            if DEBUG:
                print("Matched trigger for jsdoc completion")
            return Trigger(lang, TRG_FORM_CPLN,
                           "jsdoc-tags", pos, implicit)

        # JSDoc calltip
        elif last_char in " \t" and style in self.comment_styles():
            # whitespace in a comment, see if it matches for jsdoc calltip
            p = last_pos - 1
            min_p = max(0, p - 50)  # Don't bother looking more than 50 chars
            if DEBUG:
                print("Checking match for jsdoc calltip")
            ch = None
            ident_found_pos = None
            while p >= min_p and accessor.style_at_pos(p) in self.comment_styles():
                ch = accessor.char_at_pos(p)
                p -= 1
                if ident_found_pos is None:
                    # print("jsdoc: Looking for identifier, at ch: %r" % (ch))
                    if ch in " \t":
                        pass
                    elif _isident(ch):
                        ident_found_pos = p + 1
                    else:
                        if DEBUG:
                            print("No jsdoc, whitespace not preceeded by an identifer")
                        return None
                elif ch == "@":
                    # This is what we've been looking for!
                    jsdoc_field = accessor.text_range(p + 2, ident_found_pos + 1)
                    if DEBUG:
                        print("Matched trigger for jsdoc calltip: '%s'" % (jsdoc_field, ))
                    return Trigger(lang, TRG_FORM_CALLTIP,
                                   "jsdoc-tags", ident_found_pos, implicit,
                                   jsdoc_field=jsdoc_field)
                elif not _isident(ch):
                    if DEBUG:
                        print("No jsdoc, identifier not preceeded by an '@'")
                    # Not whitespace, not a valid tag then
                    return None
            # Nothing found in the specified range
            if DEBUG:
                print("No jsdoc, ran out of characters to look at.")

        # Remaing triggers should never trigger in some styles.
        if (
            implicit and
            style in self.implicit_completion_skip_styles and
            (last_char not in "\"'/" or style in self.completion_skip_styles)
        ):
            if DEBUG:
                print("trg_from_pos: no: completion is suppressed in style at %s: %s (%s)" % (last_pos, style, ", ".join(style_names)))
            return None

        if last_char == ' ' and style == ScintillaConstants.SCE_UDL_M_TAGSPACE:
            # JSX tag properties
            return Trigger(lang, TRG_FORM_CPLN,
                        "object-properties", pos, implicit)

        elif last_char == ' ':
            # used for:
            #    * complete-module-members

            # Typing a space is very common so lets have a quick out before
            # doing the more correct processing:
            if last_pos - 1 < 0 or accessor.char_at_pos(last_pos - 1) not in "t,{":
                return None

            working_text = accessor.text_range(max(0, last_pos - 200), last_pos)
            line = self._last_logical_line(working_text).strip()
            if not line:
                return None
            ch = line[-1]
            line = line.replace('\t', ' ')

            # is it "import <|> from FOO" ?
            # is it "import { <|> from FOO" ?
            # is it "import BAR, <|> from FOO" ?
            if line == "import" or ch in (",", "{"):
                # imp_prefix = tuple(line[line.index(" from "):].strip().split('.'))
                imp_prefix = ()
                # Need better checks
                return Trigger(lang, TRG_FORM_CPLN,
                            "module-members", pos, implicit,
                            imp_prefix=imp_prefix)

            # TODO: ECMASCRIPT ABOVE, PYTHON BELOW: (remove this comment line)

            # is it "from FOO import <|>" ?
            if line.endswith(" import"):
                if line.startswith('from '):
                    imp_prefix = tuple(line[len('from '):-len(' import')].strip().split('.'))
                    return Trigger(lang, TRG_FORM_CPLN,
                               "module-members", pos, implicit,
                               imp_prefix=imp_prefix)

            if ch == ',':
                # is it "from FOO import BAR, <|>" ?
                if line.startswith('from ') and ' import ' in line:
                    imp_prefix = tuple(line[len('from '):line.index(' import')].strip().split('.'))
                    # Need better checks
                    return Trigger(lang, TRG_FORM_CPLN,
                               "module-members", pos, implicit,
                               imp_prefix=imp_prefix)

        elif last_char in "'\"":
            # used for:
            #    * complete-available-imports

            # Typing a space is very common so lets have a quick out before
            # doing the more correct processing:
            if last_pos - 1 < 0 or accessor.char_at_pos(last_pos - 1) != " ":
                return None

            working_text = accessor.text_range(max(0, last_pos - 200), last_pos)
            line = self._last_logical_line(working_text).strip()
            if not line:
                return None
            line = line.replace('\t', ' ')

            # from '<|>
            if line == "from" or line.endswith(" from"):
                return Trigger(lang, TRG_FORM_CPLN,
                               "available-imports", pos, implicit,
                               imp_prefix=())

        elif last_char == '/':
            if style in self.string_styles():
                working_text = accessor.text_range(max(0, last_pos - 200), last_pos)
                line = self._last_logical_line(working_text).strip()
                if not line:
                    return None
                line = line.replace('\t', ' ')
                if line.startswith("import ") and " from " in line:
                    # import BAR from 'FOO/
                    imp_prefix = tuple(line[line.rindex(" from ") + 7:].split('/'))
                    return Trigger(lang, TRG_FORM_CPLN,
                                   "available-imports", pos, implicit,
                                   imp_prefix=imp_prefix)

        elif last_char == '.':  # must be "complete-object-members" or None
            # If the first non-whitespace character preceding the '.' in the
            # same statement is an identifer character then trigger, if it
            # is a ')', then _maybe_ we should trigger (yes if this is
            # function call paren).
            #
            # Triggering examples:
            #   FOO.            FOO .                       FOO; BAR.
            #   FOO().          FOO.BAR.                    FOO(BAR, BAZ.
            #   FOO().BAR.      FOO("blah();", "blam").     FOO = {BAR.
            #   FOO(BAR.        FOO[BAR.
            #   ...more cases showing possible delineation of expression
            # Non-triggering examples:
            #   FOO..
            #   FOO[1].         too hard to determine sequence element types
            # Not sure if want to support:
            #   "foo".          do we want to support literals? what about
            #                   lists? tuples? dicts?
            working_text = accessor.text_range(max(0, last_pos - 200), last_pos)
            line = self._last_logical_line(working_text).strip()
            if line:
                ch = line[-1]
                if isident(ch) or isdigit(ch) or ch in '.)':
                    line = line.replace('\t', ' ')
                    return Trigger(lang, TRG_FORM_CPLN,
                                   "object-members", pos, implicit)
                elif ch in ("\"'"):
                    return Trigger(lang, TRG_FORM_CPLN,
                                   "literal-members", pos, implicit,
                                   citdl_expr="str")
            else:
                ch = None
            if DEBUG:
                print("trg_from_pos: no: non-ws char preceding '.' is not an identifier char or ')': %r" % ch)
            return None

        elif last_char == '(':
            # If the first non-whitespace character preceding the '(' in the
            # same statement is an identifer character then trigger calltip,
            #
            # Triggering examples:
            #   FOO.            FOO (                       FOO; BAR(
            #   FOO.BAR(        FOO(BAR, BAZ(               FOO = {BAR(
            #   FOO(BAR(        FOO[BAR(
            # Non-triggering examples:
            #   FOO()(      a function call returning a callable that is
            #               immediately called again is too rare to bother
            #               with
            #   def foo(    might be a "calltip-base-signature", but this
            #               trigger is not yet implemented
            #   import (    will be handled by complete_members
            #   class Foo(  is an "complete-available-classes" trigger,
            #               but this is not yet implemented
            working_text = accessor.text_range(max(0, last_pos - 200), last_pos)
            line = self._last_logical_line(working_text).rstrip()
            if line:
                ch = line[-1]
                if isident(ch) or isdigit(ch):
                    # If this is:
                    #   def foo(
                    # then this might be the (as yet unimplemented)
                    # "calltip-base-signature" trigger or it should not be a
                    # trigger point.
                    #
                    # If this is:
                    #   class Foo(
                    # then this should be the (as yet unimplemented)
                    # "complete-available-classes" trigger.
                    line = line.replace('\t', ' ')
                    lstripped = line.lstrip()
                    if lstripped.startswith("def"):
                        if DEBUG:
                            print("trg_from_pos: no: point is function declaration")
                    elif lstripped.startswith("class") and '(' not in lstripped:
                        # Second test is necessary to not exclude:
                        #   class Foo(bar(<|>
                        if DEBUG:
                            print("trg_from_pos: no: point is class declaration")
                    elif lstripped.startswith('from ') and ' import' in lstripped:
                        # Need better checks
                        # is it "from FOO import (<|>" ?
                        imp_prefix = tuple(lstripped[len('from '):lstripped.index(' import')].split('.'))
                        if DEBUG:
                            print("trg_from_pos: from FOO import (")
                        return Trigger(lang, TRG_FORM_CPLN,
                                   "module-members", pos, implicit,
                                   imp_prefix=imp_prefix)
                    else:
                        return Trigger(lang, TRG_FORM_CALLTIP, "call-signature", pos, implicit)
                else:
                    if DEBUG:
                        print("trg_from_pos: no: non-ws char preceding '(' is not an identifier char: %r" % ch)
            else:
                if DEBUG:
                    print("trg_from_pos: no: no chars preceding '('")
            return None

        elif last_char == ',':
            working_text = accessor.text_range(max(0, last_pos - 200), last_pos)
            line = self._last_logical_line(working_text).rstrip()
            if line:
                last_bracket = line.rfind("(")
                pos = (pos - (len(line) - last_bracket))
                return Trigger(lang, TRG_FORM_CALLTIP, "call-signature", pos, implicit)
            else:
                return None

        elif pos >= 2 and style in (self.identifier_style, self.keyword_style):
            # 2 character trigger for local symbols
            if DEBUG:
                if style == self.identifier_style:
                    print("Identifier style")
                else:
                    print("Identifier keyword style")
            # Previous char also need to be an identifier/word, then the one
            # before that needs to be something different (operator/space).
            if (
                accessor.style_at_pos(last_pos - 1) != style or
                (pos > 2 and accessor.style_at_pos(last_pos - 2) == style)
            ):
                if DEBUG:
                    print("Not a block of two ident/word chars")
                return None
            if pos > 2 and accessor.char_at_pos(last_pos - 2) == ".":
                if DEBUG:
                    print("  preceeded by '.' operator - not a trigger")
                return None

            # Check if it makes sense to show the completions here. If defining
            # a class name, or function name, you don't want to see completions.
            # Also, do not override another completion type (e.g. imports).
            start = accessor.line_start_pos_from_pos(pos)
            preceeding_text = accessor.text_range(start, last_pos - 2).strip()
            if preceeding_text:
                first_word = preceeding_text.split(" ")[0]
                if first_word in ("class", "def", "import", "from", "except", "raise"):
                    if DEBUG:
                        print("  no trigger, as starts with %r" % (first_word, ))
                    # Don't trigger over the top of another trigger, i.e.
                    #   complete-available-imports
                    #   complete-module-members
                    #   complete-available-exceptions
                    return None

            citdl_expr = accessor.text_range(last_pos - 1, last_pos + 1)
            if DEBUG:
                print("  triggered 2 char symbol trigger: %r" % (citdl_expr, ))
            return Trigger(lang, TRG_FORM_CPLN, "local-symbols",
                           last_pos - 1, implicit,
                           citdl_expr=citdl_expr,
                           preceeding_text=preceeding_text)

    def _last_logical_line(self, text):
        lines = text.splitlines(0) or ['']
        logicalline = lines.pop()
        while lines and lines[-1].endswith('\\'):
            logicalline = lines.pop()[:-1] + ' ' + logicalline
        return logicalline


class ECMAScriptImportHandler(ImportHandler):
    lang = lang  # XXX do this for other langs as well
    sep = '/'

    def __init__(self, mgr):
        ImportHandler.__init__(self, mgr)
        self.__stdCIXScanId = None

    suffixes = (
        ".js",
        ".jsx",
        ".es",
    )
    suffixes_dict = dict((s, i) for i, s in enumerate(suffixes, 1))

    subpaths = (
        "src",
        "es",
        "lib",
        "dist",
    )
    subpaths_re = re.compile(r'(^|/)(?:%s)($|/)' % r'|'.join(subpaths))

    def _find_importable(self, imp_dir, name, boost, find_package=True):
        mod, suffix = os.path.splitext(name)
        if mod != 'index':
            suffixes = self.suffixes
            suffixes_dict = self.suffixes_dict

            package_json = os.path.join(imp_dir, os.path.join(name, 'package.json'))
            if find_package and os.path.exists(package_json):
                try:
                    package = json.load(open(package_json))
                    main = package['main']
                except:
                    pass
                else:
                    while main.startswith('./'):
                        main = main[2:]
                    main_dir = os.path.dirname(main)
                    main_base = os.path.join(main_dir, os.path.basename(main).partition('.')[0])
                    for subpath in self.subpaths:
                        _name = os.path.join(name, self.subpaths_re.sub(r'\1%s\2' % subpath, main))
                        if os.path.exists(os.path.join(imp_dir, dirname(_name))):
                            module = self._find_importable(imp_dir, _name, boost // 2, find_package=False)
                            if module:
                                # Remove subpath from module name
                                module = (module[0], name, module[2])
                                return module

                        _name = os.path.join(name, self.subpaths_re.sub(r'\1%s\2' % subpath, main_base))
                        if os.path.exists(os.path.join(imp_dir, dirname(_name))):
                            module = self._find_importable(imp_dir, _name, boost // 2, find_package=False)
                            if module:
                                # Remove subpath from module name
                                module = (module[0], name, module[2])
                                return module

                        _name = os.path.join(name, self.subpaths_re.sub(r'\1%s\2' % subpath, main_dir))
                        if os.path.exists(os.path.join(imp_dir, dirname(_name))):
                            module = self._find_importable(imp_dir, _name, boost // 2, find_package=False)
                            if module:
                                # Remove subpath from module name
                                module = (module[0], name, module[2])
                                return module

            if not suffix:
                for _suffix in suffixes:
                    _name = dirname(name)
                    _mod = basename(name)
                    init = os.path.join(_name, _mod + _suffix)
                    if os.path.exists(os.path.join(imp_dir, init)):
                        return (suffixes_dict[_suffix] + boost, _name, (init, _mod, False))

            for _suffix in suffixes:
                init = os.path.join(name, 'index' + _suffix)
                if os.path.exists(os.path.join(imp_dir, init)):
                    return (suffixes_dict[_suffix] + boost, name, (init, 'index', False))

            if suffix in suffixes:
                if os.path.exists(os.path.join(imp_dir, name)):
                    return (suffixes_dict[suffix] + boost, mod, (name, basename(mod), False))

    def find_importables_in_dir(self, imp_dir):
        """See citadel.py::ImportHandler.find_importables_in_dir() for
        details.

        Importables for ECMAScript look like this:
            {"foo":    ("foo.js",             None,       False),
             "foolib": ("foolib/index.js",    "index",    False),
             "bar":    ("bar.jsx",            None,       False),
             "baz":    ("baz.node",           None,       False),
             "qoox":   ("qoox.es",            None,       False),
             "qooz":   ("qooz.ts",            None,       False),

        If several files happen to have the same name but different
        suffixes, the one with preferred suffix wins. The suffixe preference
        is defined by the order of elements in the sequence generated
        by _gen_suffixes().
        """
        if imp_dir == "<Unsaved>":
            # TODO: stop these getting in here.
            return {}

        modules = []

        if os.path.isdir(imp_dir):
            for name in os.listdir(imp_dir):
                if not name.startswith('.'):
                    module = self._find_importable(imp_dir, name, 100)
                    if module:
                        modules.append(module)

            package_json = os.path.join(imp_dir, 'package.json')
            if os.path.exists(package_json):
                for subpath in self.subpaths:
                    _imp_dir = os.path.join(imp_dir, subpath)
                    if os.path.isdir(_imp_dir):
                        for name in os.listdir(_imp_dir):
                            if not name.startswith('.'):
                                module = self._find_importable(imp_dir, os.path.join(subpath, name), 0, find_package=False)
                                if module:
                                    # Remove subpath from module name
                                    mod, suffix = os.path.splitext(name)
                                    module = (module[0], mod, module[2])
                                    modules.append(module)
                        break

        modules.sort(key=lambda mod: mod[0])

        importables = {}

        for _, mod, importable in modules:
            if mod not in importables:
                importables[mod] = importable

        return importables


class ECMAScriptCILEDriver(CILEDriver):
    lang = lang

    def scan_purelang(self, buf):
        # log.warn("TODO: ECMAScript cile that uses elementtree")
        content = buf.accessor.text
        el = ecmacile.scan_et(content, buf.path, lang=self.lang)
        return el


# ---- internal support stuff


# ---- registration

def register(mgr):
    """Register language support with the Manager."""
    mgr.set_lang_info(lang,
                      silvercity_lexer=ECMAScriptLexer(),
                      buf_class=ECMAScriptBuffer,
                      langintel_class=ECMAScriptLangIntel,
                      import_handler_class=ECMAScriptImportHandler,
                      cile_driver_class=ECMAScriptCILEDriver,
                      is_cpln_lang=True)
