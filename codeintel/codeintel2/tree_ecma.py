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
# Contributor(s):
#   ActiveState Software Inc
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

"""Completion evaluation code for ECMAScript"""

from __future__ import absolute_import

import re
from os.path import dirname, join, exists, isdir, abspath
import operator

from codeintel2.common import CodeIntelError
from codeintel2.tree import TreeEvaluator

base_exception_class_completions = [
    "Exception",
]


tokenize_re = re.compile(r'(^|[.()])([^.()]*)')


class ClassInstance:
    def __init__(self, elem):
        self.elem = elem

    @property
    def tag(self):
        return self.elem.tag

    @property
    def names(self):
        return self.elem.names

    def get(self, name, default=None):
        if name == "ilk":
            return "instance"
        return self.elem.get(name, default)

    def __iter__(self):
        return iter(self.elem)

    def __repr__(self):
        return "<instance" + repr(self.elem)[6:]


class FakeImport:
    def __init__(self, elem, tag="import", **attributes):
        self._elem = elem
        self._tag = tag
        self._attributes = attributes

    @property
    def tag(self):
        return self._tag

    def get(self, name, default=None):
        if name in self._attributes:
            return self._attributes[name]
        return self._elem.get(name, default)

    def __iter__(self):
        return iter([self])

    def __repr__(self):
        return repr(self._elem)


class ECMAScriptImportLibGenerator(object):
    """A lazily loading lib generator.

    To be used for Komodo's import lookup handling. This generator will return
    libraries as needed, then when the given set of libraries runs out (i.e.
    when there were no matches in the given libraries), to then try and find
    other possible directories (libraries) that could offer a match."""
    def __init__(self, mgr, lang, bufpath, imp_prefix, libs):
        self.mgr = mgr
        self.lang = lang
        self.imp_prefix = imp_prefix
        self.bufpath = bufpath
        self.libs = libs
        self.index = 0

    def __iter__(self):
        self.index = 0
        return self

    def __next__(self):
        if self.index < len(self.libs):
            # Return the regular libs.
            try:
                return self.libs[self.index]
            finally:
                self.index += 1
        elif self.index == len(self.libs):
            # Try to find a matching parent directory to use.
            # print("Lazily loading the parent import libs: %r" % (self.imp_prefix, ))
            self.index += 1
            lookuppath = dirname(self.bufpath)
            parent_dirs_left = 5
            import_name = self.imp_prefix[0]
            if "/" in import_name:
                import_name = import_name.split("/", 1)[0]
            while lookuppath and parent_dirs_left > 0:
                # print('    exists: %r - %r' % (exists(join(lookuppath, import_name, "__init__.py")), join(lookuppath, import_name, "__init__.py")))
                parent_dirs_left -= 1
                if exists(join(lookuppath, import_name, "__init__.py")):
                    # Matching directory - return that as a library.
                    # print("  adding parent dir lib: %r" % (lookuppath))
                    return self.mgr.db.get_lang_lib(self.lang, "parentdirlib", [lookuppath])
                lookuppath = dirname(lookuppath)
            # No match found - we're done.
            raise StopIteration
        else:
            raise StopIteration
    next = __next__


class ECMAScriptTreeEvaluator(TreeEvaluator):

    # Own copy of libs (that shadows the real self.buf.libs) - this is required
    # in order to properly adjust the "reldirlib" libraries as they hit imports
    # from different directories - i.e. to correctly deal with relative imports.
    _libs = None
    _SENTINEL_MAX_EXPR_COUNT = 100

    @property
    def libs(self):
        if self._libs is None:
            self._libs = self.buf.libs
        return self._libs

    @libs.setter
    def libs(self, value):
        self._libs = value

    def eval_cplns(self):
        self.log_start()
        if self.trg.type == 'available-exceptions':
            # TODO: Should perform a lookup to determine all available exception
            #       classes.
            return base_exception_class_completions
        start_scoperef = self.get_start_scoperef()
        self.info("start scope is %r", start_scoperef)
        if self.trg.type == 'local-symbols':
            return self._available_symbols(start_scoperef, self.expr)
        # if self.trg.type == 'available-classes':
        #    return self._available_classes(start_scoperef, self.trg.extra["consumed"])
        if self.trg.type == "object-properties":
            return self._available_properties(start_scoperef, self.expr)
        hit = self._hit_from_citdl(self.expr, start_scoperef)
        return list(self._members_from_hit(hit))

    def eval_calltips(self):
        self.log_start()
        start_scoperef = self.get_start_scoperef()
        self.info("start scope is %r", start_scoperef)
        hit = self._hit_from_citdl(self.expr, start_scoperef)
        return [self._calltip_from_hit(hit)]

    def eval_defns(self):
        self.log_start()
        start_scoperef = self.get_start_scoperef()
        self.info("start scope is %r", start_scoperef)
        hit = self._hit_from_citdl(self.expr, start_scoperef, defn_only=True)
        return [self._defn_from_hit(hit)]

    def _defn_from_hit(self, hit):
        defn = TreeEvaluator._defn_from_hit(self, hit)
        if not defn.path:
            # Locate the module in the users own ECMAScript stdlib,
            # bug 65296.
            langintel = self.buf.langintel
            info = langintel.ecmascript_info_from_env(self.buf.env)
            ver, prefix, libdir, sitelibdir, sys_path = info
            if libdir:
                elem, (blob, lpath) = hit
                path = join(libdir, blob.get("name"))
                if exists(path + ".py"):
                    defn.path = path + ".py"
                elif isdir(path) and exists(join(path, "__init__.py")):
                    defn.path = join(path, "__init__.py")
        return defn

    # def _available_classes(self, scoperef, consumed):
    #    matches = set()
    #    blob = scoperef[0] # XXX??
    #    for elem in blob:
    #        if elem.tag == 'scope' and elem.get('ilk') == 'class':
    #            matches.add(elem.get('name'))
    #    matches.difference_update(set(consumed))
    #    matches_list = sorted(list(matches))
    #    return [('class', m) for m in matches_list]

    def _available_symbols(self, scoperef, expr):
        cplns = []
        found_names = set()
        while scoperef:
            elem = self._elem_from_scoperef(scoperef)
            if not elem:
                break
            for child in elem:
                if child.tag == "import":
                    name = child.get("alias") or child.get("symbol") or child.get("module")
                    # TODO: Deal with "*" imports.
                else:
                    name = child.get("name", "")
                if name.startswith(expr):
                    if name not in found_names:
                        found_names.add(name)
                        ilk = child.get("ilk") or child.tag
                        if ilk == "import":
                            ilk = "module"
                        cplns.append((ilk, name))
            scoperef = self.parent_scoperef_from_scoperef(scoperef)

        # Add keywords, being smart about where they are allowed.
        preceeding_text = self.trg.extra.get("preceeding_text", "")
        for keyword in self.buf.langintel.keywords:
            # Don't remove short keywords, as that has a conflict with fill-up
            # characters, see bug 100471.
            # if len(keyword) < 3 or not keyword.startswith(expr):
            if not keyword.startswith(expr):
                continue
            # Always add None and lambda, otherwise only at the start of lines.
            if not preceeding_text or keyword in ("None", "lambda"):
                cplns.append(("keyword", keyword))

        return sorted(cplns, key=operator.itemgetter(1))

    def _available_properties(self, scoperef, expr):
        while scoperef:
            elem = self._elem_from_scoperef(scoperef)
            attributes = elem.get("attributes", "").split()
            if "__jsx__" in attributes:
                break
            scoperef = self.parent_scoperef_from_scoperef(scoperef)
        if elem is not None:
            if "props" in elem.names:
                props = set(elem.names["props"].names)
            else:
                props = set()
            elem, scoperef = self._hit_from_variable_type_inference(elem, scoperef)
            hit, nconsumed = self._hit_from_getattr(["propTypes"], elem, scoperef)
            if hit:
                elem, scoperef = hit
                while elem.tag == "variable":
                    elem, scoperef = self._hit_from_variable_type_inference(elem, scoperef)
                return [("attribute", "%s" % p) for p in set(elem.names) - props]
        return []

    def _tokenize_citdl_expr(self, citdl):
        level = 0
        params = ""
        for m in tokenize_re.finditer(citdl):
            sep, word = m.groups()
            if sep == "(":
                level += 1
            elif sep == ")":
                level -= 1
                if not level:
                    yield params + ")"
                    params = ""
                    if word:
                        yield word
                    continue
            elif not level:
                if word:
                    yield word
                continue
            params += sep + word

    def _join_citdl_expr(self, tokens):
        return '.'.join(tokens)

    def _calltip_from_func(self, elem, scoperef, class_name=None):
        # See "Determining a Function CallTip" in the spec for a
        # discussion of this algorithm.
        signature = elem.get("signature")
        ctlines = []
        if not signature:
            name = class_name or elem.get("name")
            ctlines = [name + "(...)"]
        else:
            ctlines = signature.splitlines(0)
        doc = elem.get("doc")
        if doc:
            ctlines += doc.splitlines(0)
        return '\n'.join(ctlines)

    def _calltip_from_class(self, elem, scoperef):
        # If the class has a defined signature then use that.
        signature = elem.get("signature")
        if signature:
            doc = elem.get("doc")
            ctlines = signature.splitlines(0)
            if doc:
                ctlines += doc.splitlines(0)
            return '\n'.join(ctlines)
        else:
            ctor_hit = self._ctor_hit_from_class(elem, scoperef)
            if ctor_hit and (ctor_hit[0].get("doc") or ctor_hit[0].get("signature")):
                self.log("ctor is %r on %r", *ctor_hit)
                return self._calltip_from_func(ctor_hit[0], ctor_hit[1],
                                               class_name=elem.get("name"))

            else:
                doc = elem.get("doc")
                if doc:
                    ctlines = [ln for ln in doc.splitlines(0) if ln]
                else:
                    ctlines = [elem.get("name") + "()"]
                return '\n'.join(ctlines)

    def _ctor_hit_from_class(self, elem, scoperef, defn_only=False):
        """Return the ECMAScript ctor for the given class element, or None."""
        if "constructor" in elem.names:
            class_scoperef = (scoperef[0], scoperef[1] + [elem.get("name")])
            return elem.names["constructor"], class_scoperef
        else:
            for classref in elem.get("classrefs", "").split():
                try:
                    base_hit = self._hit_from_type_inference(classref, scoperef, defn_only=defn_only)
                except CodeIntelError as ex:
                    self.warn(str(ex))
                else:
                    base_elem, base_scoperef = base_hit
                    ctor_hit = self._ctor_hit_from_class(base_elem, base_scoperef, defn_only=defn_only)
                    if ctor_hit:
                        return ctor_hit
        return None

    def _calltip_from_hit(self, hit):
        # TODO: compare with CitadelEvaluator._getSymbolCallTips()
        elem, scoperef = hit
        if elem.tag == "variable":
            # XXX
            pass
        elif elem.tag == "scope":
            ilk = elem.get("ilk")
            if ilk == "function":
                calltip = self._calltip_from_func(elem, scoperef)
            elif ilk in ("class", "instance"):
                calltip = self._calltip_from_class(elem, scoperef)
            else:
                raise NotImplementedError("unexpected scope ilk for "
                                          "calltip hit: %r" % elem)
        else:
            raise NotImplementedError("unexpected elem for calltip "
                                      "hit: %r" % elem)
        return calltip

    def _members_from_elem(self, elem):
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
                import_handler = self.citadel.import_handler_from_lang(self.trg.lang)
                blob = import_handler.import_blob_name(module_name, self.libs, self.ctlr)
                if symbol_name == "*":
                    for m_name, m_elem in blob.names.items():
                        m_type = m_elem.get("ilk") or m_elem.tag
                        members.add((m_type, m_name))
                elif symbol_name in blob.names:
                    symbol = blob.names[symbol_name]
                    member_type = (symbol.get("ilk") or symbol.tag)
                    members.add((member_type, alias or symbol_name))
                else:
                    hit, nconsumed = self._hit_from_elem_imports([symbol_name], blob)
                    if hit:
                        symbol = hit[0]
                        member_type = (symbol.get("ilk") or symbol.tag)
                        members.add((member_type, alias or symbol_name))
                    else:
                        self.warn("could not resolve %r", elem)
            else:
                cpln_name = alias or module_name.split("/", 1)[0]
                members.add(("module", cpln_name))
        else:
            members.add((elem.get("ilk") or elem.tag, elem.get("name")))
        return members

    def _members_from_hit(self, hit, defn_only=False, hidden=None):
        elem, scoperef = hit
        ilk = elem.get("ilk")

        if ilk == "class":
            refs = "classrefs"
            if hidden is None:
                hidden = ["__hidden__", "__instancevar__"]
        elif ilk == "instance":
            refs = "classrefs"
            if hidden is None:
                hidden = ["__hidden__", "__staticmethod__", "__ctor__"]
        elif ilk == "interface":
            refs = "interfacerefs"
            if hidden is None:
                hidden = ["__hidden__"]
        elif ilk == "object":
            refs = "objectrefs"
            if hidden is None:
                hidden = ["__hidden__"]
        else:
            refs = None
            if hidden is None:
                hidden = ["__hidden__"]

        members = set()
        for child in elem:
            attributes = child.get("attributes", "").split()
            for attr in hidden:
                if attr in attributes:
                    break
            else:
                try:
                    members.update(self._members_from_elem(child))
                except CodeIntelError as ex:
                    self.warn("%s (skipping members for %s)", ex, child)

        if refs:
            for ref in elem.get(refs, "").split():
                try:
                    subhit = self._hit_from_type_inference(ref, scoperef, defn_only=defn_only)
                except CodeIntelError as ex:
                    # Continue with what we *can* resolve.
                    self.warn(str(ex))
                else:
                    members.update(self._members_from_hit(subhit, defn_only, hidden=hidden))

        # Scope with citdl type:
        citdl = elem.get("citdl")
        if citdl:
            try:
                subhit = self._hit_from_type_inference(citdl, scoperef, defn_only)
            except CodeIntelError as ex:
                # Continue with what we *can* resolve.
                self.warn(str(ex))
            else:
                members.update(self._members_from_hit(subhit, defn_only, hidden=hidden))

        return members

    def _hit_from_citdl(self, expr, scoperef, variable=None, defn_only=False):
        """Resolve the given CITDL expression (starting at the given
        scope) down to a non-import/non-variable hit.
        """
        self._check_infinite_recursion(expr)
        tokens = list(self._tokenize_citdl_expr(expr))
        # self.log("expr tokens: %r", tokens)

        hit, nconsumed = self._hit_from_tokens(tokens, expr, scoperef, variable=variable, defn_only=defn_only)

        return hit

    def _hit_from_tokens(self, tokens, expr, scoperef, variable=None, defn_only=False):
        args_scoperef = scoperef

        # First part...
        hit, nconsumed = self._hit_from_first_part(tokens, scoperef, variable=variable, defn_only=defn_only)
        if not hit:
            # TODO: Add the fallback Buffer-specific near-by hunt
            #      for a symbol for the first token. See my spiral-bound
            #      book for some notes.
            raise CodeIntelError("could not resolve first part of '%s'" % expr)
        self.debug("_hit_from_citdl: first part: %r -> %r", tokens[:nconsumed], hit)

        # ...the remainder.
        remaining_tokens = tokens[nconsumed:]
        while remaining_tokens:
            elem, scoperef = hit
            self.debug("_hit_from_citdl: resolve %r on %r in %r", remaining_tokens, elem, scoperef)
            if remaining_tokens[0][0] == "(":
                new_hit = self._hit_from_call(elem, scoperef, remaining_tokens[0], args_scoperef, defn_only=defn_only)
                nconsumed = 1
            else:
                new_hit, nconsumed = self._hit_from_getattr(remaining_tokens, elem, scoperef, defn_only=defn_only)
            remaining_tokens = remaining_tokens[nconsumed:]
            hit = new_hit

        # Resolve any variable type inferences.
        elem, scoperef = hit
        while elem.tag == "variable" and (not defn_only or "__no_defn__" in elem.get("attributes", "").split()):
            elem, scoperef = self._hit_from_variable_type_inference(elem, scoperef, defn_only=defn_only)

        self.info("'%s' is %s on %s", expr, elem, scoperef)
        return (elem, scoperef), len(tokens) - len(remaining_tokens)

    def _hit_from_require(self, tokens, scoperef, variable=None, defn_only=False):
        # Node.js / CommonJS hack: try to resolve things via require()
        if len(tokens) > 1 and tokens[1][0] == "(":
            if variable is not None:
                requirename = variable.get("required_library_name")
                if requirename:
                    self.log("_hit_from_variable_type_inference: resolving require(%r)", requirename)
                    module_name = requirename.lstrip("./")
                    if len(tokens) > 2:
                        symbol = tokens[2]
                        remaining_tokens = tokens[3:]
                        _nconsumed = 2
                    else:
                        symbol = None
                        remaining_tokens = tokens[2:]
                        _nconsumed = 1
                    require = FakeImport(variable,
                                         module=requirename,
                                         symbol=symbol,
                                         alias=None)
                    hit, nconsumed = self._hit_from_elem_imports([module_name] + remaining_tokens, require, defn_only=defn_only)
                    if hit is not None:
                        return hit, nconsumed + _nconsumed
                raise CodeIntelError("could not resolve require(%r)" % requirename)
        return None, None

    def _hit_from_first_part(self, tokens, scoperef, variable=None, defn_only=False):
        """Find a hit for the first part of the tokens.

        Returns (<hit>, <num-tokens-consumed>) or (None, None) if could
        not resolve.

        Example for 'os.sep':
            tokens: ('os', 'sep')
            retval: ((<variable 'sep'>,  (<blob 'os', [])),   1)
        Example for 'os.path':
            tokens: ('os', 'path')
            retval: ((<import os.path>,  (<blob 'os', [])),   2)
        """
        first_token = tokens[0]

        self.log("find '%s ...' starting at %s:", first_token, scoperef)

        # escile will sometimes give a citdl expression of "__builtins__",
        # check for this now, bug:
        #   http://bugs.activestate.com/show_bug.cgi?id=71972
        if first_token == "__builtins__":
            # __builtins__ is the same as the built_in_blob, return it.
            scoperef = (self.built_in_blob, [])
            return (self.built_in_blob, scoperef), 1

        if first_token == "require":
            hit, nconsumed = self._hit_from_require(tokens, scoperef, variable=variable, defn_only=defn_only)
            if hit is not None:
                return hit, nconsumed

        elem = self._elem_from_scoperef(scoperef)
        citdl, citdl_scoperef = elem.get("citdl"), scoperef

        while True:
            if first_token in elem.names:
                item = elem.names[first_token]
                if item is not variable:
                    # TODO: skip __hidden__ names
                    self.log("is '%s' accessible on %s? yes: %s",
                            first_token, scoperef, elem.names[first_token])
                    return (item, scoperef), 1

            hit, nconsumed = self._hit_from_elem_imports(tokens, elem, defn_only=defn_only)
            if hit is not None:
                self.log("is '%s' accessible on %s? yes: %s",
                         ".".join(tokens[:nconsumed]), scoperef, hit[0])
                return hit, nconsumed
            self.log("is '%s' accessible on %s? no", first_token, scoperef)

            if first_token == elem.get("name") and elem.get("ilk") == "blob":
                # The element itself is the thing we wanted...
                self.log("is '%s' accessible on %s? yes: %s",
                        first_token, scoperef, elem)
                return (elem, scoperef), 1

            scoperef = self.parent_scoperef_from_scoperef(scoperef)
            if not scoperef:
                # Scope with citdl type, try fallback to citdl:
                if citdl:
                    try:
                        subhit = self._hit_from_type_inference(citdl, citdl_scoperef, defn_only=defn_only)
                    except CodeIntelError as ex:
                        pass
                    else:
                        item, itemscoperef = subhit
                        hit, nconsumed = self._hit_from_first_part(tokens, itemscoperef, defn_only=defn_only)
                        if hit:
                            self.log("is '%s' accessible on %s? yes: %s",
                                    ".".join(tokens[:nconsumed]), citdl_scoperef, hit[0])
                            return hit, nconsumed
                return None, None
            elem = self._elem_from_scoperef(scoperef)

    def _set_reldirlib_from_blob(self, blob):
        """Set the relative import directory to be this blob's location."""
        # See bug 45822 and bug 88971 for examples of why this is necessary.
        if blob is None:
            return
        blob_src = blob.get("src")
        if blob_src and blob.get("ilk") == "blob":
            reldirpath = dirname(blob_src)
            reldirlib = self.mgr.db.get_lang_lib(self.trg.lang, "reldirlib",
                                                 [reldirpath])
            newlibs = self.libs[:]  # Make a copy of the libs.
            if newlibs[0].name == "reldirlib":
                # Update the existing reldirlib location.
                newlibs[0] = reldirlib
            else:
                # Add in the relative directory lib.
                newlibs.insert(0, reldirlib)
            self.log("imports:: setting reldirlib to: %r", reldirpath)
            self.libs = newlibs

    def _add_parentdirlib(self, libs, tokens):
        """Add a lazily loaded parent directory import library."""
        if isinstance(libs, ECMAScriptImportLibGenerator):
            # Reset to the original libs.
            libs = libs.libs
        libs = ECMAScriptImportLibGenerator(self.mgr, self.trg.lang, self.buf.path, tokens, libs)
        return libs

    def _hit_from_elem_imports(self, tokens, elem, defn_only=False):
        """See if token is from one of the imports on this <scope> elem.

        Returns (<hit>, <num-tokens-consumed>) or (None, None) if not found.
        XXX import_handler.import_blob_name() calls all have potential
            to raise CodeIntelError.
        """
        # PERF: just have a .import_handler property on the evalr?
        import_handler = self.citadel.import_handler_from_lang(self.trg.lang)

        # PERF: Add .imports method to ciElementTree for quick iteration
        #      over them. Or perhaps some cache to speed this method.
        # TODO: The right answer here is to not resolve the <import>,
        #      just return it. It is complicated enough that the
        #      construction of members has to know the original context.
        #      See the "Foo.mypackage.<|>mymodule.yo" part of test
        #      ecmascript/cpln/wacky_imports.
        #      XXX Not totally confident that this is the right answer.
        first_token = tokens[0]
        possible_submodule_tokens = []

        self._check_infinite_recursion(first_token)
        orig_libs = self.libs
        for imp_elem in (i for i in elem if i.tag == "import"):
            libs = orig_libs  # reset libs back to the original
            self.debug("'%s ...' from %r?", first_token, imp_elem)
            alias = imp_elem.get("alias")
            symbol_name = imp_elem.get("symbol")
            module_name = imp_elem.get("module")
            allow_parentdirlib = True

            if module_name.startswith("."):
                allow_parentdirlib = False
                # Need a different curdirlib.
                if libs[0].name == "reldirlib":
                    lookuppath = libs[0].dirs[0]
                else:
                    lookuppath = dirname(self.buf.path)
                _module_name = module_name.lstrip("./")
                lookuppath = abspath(join(lookuppath, module_name[:-len(_module_name)]))
                module_name = _module_name
                libs = [self.mgr.db.get_lang_lib(self.trg.lang, "curdirlib", [lookuppath])]
                if not module_name:
                    module_name = symbol_name
                    symbol_name = None

            # from module import *
            if symbol_name == "*":
                try:
                    if allow_parentdirlib:
                        libs = self._add_parentdirlib(libs, module_name.split("/"))
                    blob = import_handler.import_blob_name(module_name, libs, self.ctlr)
                except CodeIntelError:
                    pass  # don't freak out: might not be our import anyway
                else:
                    self._set_reldirlib_from_blob(blob)
                    try:
                        hit, nconsumed = self._hit_from_getattr(tokens, blob, (blob, []), defn_only=defn_only)
                    except CodeIntelError:
                        pass
                    else:
                        if hit:
                            return hit, nconsumed

            # from module import symbol, from module import symbol as alias
            # from module import submod, from module import submod as alias
            elif (
                (alias and alias == first_token) or
                (not alias and symbol_name == first_token) or
                (not alias and module_name == first_token)
            ):
                if not symbol_name:
                    symbol_name = "default"
                # Try 'from module import symbol/from module import
                # symbol as alias' first.
                if allow_parentdirlib:
                    libs = self._add_parentdirlib(libs, module_name.split("/"))
                try:
                    blob = import_handler.import_blob_name(module_name, libs, self.ctlr)
                except CodeIntelError:
                    # That didn't work either. Give up.
                    self.warn("could not import '%s' from %s", first_token, imp_elem)
                else:
                    self._set_reldirlib_from_blob(blob)

                    scoperef = (blob, [])

                    # Always try to get exports first, fallback to global scope if there isn't one:
                    if "exports" in blob.names:
                        exports = blob.names["exports"]
                    else:
                        exports = blob

                    while True:
                        # Try to find the symbol in the exported items:
                        if symbol_name in exports.names:
                            scoperef = (scoperef[0], scoperef[1] + [exports.get("name")])
                            elem = exports.names[symbol_name]
                        elif symbol_name == "default":
                            elem = exports  # "default" fallsback to the full exports
                        elif "default" in exports.names:
                            # Complicated case where default has the imported symbol
                            exports = exports.names["default"]
                            while exports.tag == "variable":
                                exports, scoperef = self._hit_from_variable_type_inference(exports, scoperef, defn_only=defn_only)
                            blob = scoperef[0]
                            continue
                        else:
                            elem = None
                        break

                    if elem is not None:
                        while elem.tag == "variable" and (not defn_only or "__no_defn__" in elem.get("attributes", "").split()):
                            elem, scoperef = self._hit_from_variable_type_inference(elem, scoperef, defn_only=defn_only)
                        return (elem, scoperef), 1
                    else:
                        try:
                            hit, nconsumed = self._hit_from_elem_imports([first_token] + tokens[1:], blob, defn_only=defn_only)
                        except CodeIntelError:
                            self.warn("could not import name '%s' from %s", first_token, imp_elem)
                            pass
                        else:
                            if hit:
                                return hit, nconsumed

            elif "/" in module_name:
                # E.g., might be looking up ('os', 'path', ...) and
                # have <import os.path>.
                module_tokens = module_name.split("/")
                if allow_parentdirlib:
                    libs = self._add_parentdirlib(libs, module_tokens)
                if module_tokens == tokens[:len(module_tokens)]:
                    # E.g. tokens:   ('os', 'path', ...)
                    #      imp_elem: <import os.path>
                    #      return:   <blob 'os.path'> for first two tokens
                    blob = import_handler.import_blob_name(module_name, libs, self.ctlr)
                    self._set_reldirlib_from_blob(blob)
                    # XXX Is this correct scoperef for module object?
                    return (blob, (blob, [])), len(module_tokens)
                elif module_tokens[0] == tokens[0]:
                    # To check later if there are no exact import matches.
                    possible_submodule_tokens.append(module_tokens)

        # No matches, check if there is a partial import match.
        if possible_submodule_tokens:
            libs = orig_libs  # reset libs back to the original
            if allow_parentdirlib:
                libs = self._add_parentdirlib(libs, module_tokens)
            # E.g. tokens:   ('os', 'sep', ...)
            #      imp_elem: <import os.path>
            #      return:   <blob 'os'> for first token
            for i in range(len(module_tokens) - 1, 0, -1):
                for module_tokens in possible_submodule_tokens:
                    if module_tokens[:i] == tokens[:i]:
                        blob = import_handler.import_blob_name("/".join(module_tokens[:i]), libs, self.ctlr)
                        self._set_reldirlib_from_blob(blob)
                        # XXX Is this correct scoperef for module object?
                        return (blob, (blob, [])), i

        return None, None

    def _hit_from_call(self, elem, scoperef, args, args_scoperef, defn_only=False):
        """Resolve the function call inference for 'elem' at 'scoperef'."""
        # This might be a variable, in that case we keep resolving the variable
        # until we get to the final function/class element that is to be called.
        while elem.tag == "variable":
            elem, scoperef = self._hit_from_variable_type_inference(elem, scoperef, defn_only=defn_only)
        ilk = elem.get("ilk")
        if ilk == "class":
            # Return the class element.
            self.log("_hit_from_call: resolved to class instance '%s'", elem.get("name"))
            return (ClassInstance(elem), scoperef)
        if ilk == "function":
            citdl = elem.get("returns")
            if citdl:
                self.log("_hit_from_call: function with citdl %r", citdl)
                if citdl.startswith("__arg"):
                    args = args[1:-1].split(",")
                    try:
                        arg = args[int(citdl[5:]) - 1]
                        if not arg.startswith("__arg"):
                            citdl = arg
                            scoperef = args_scoperef
                    except (ValueError, IndexError):
                        pass
                return self._hit_from_citdl(citdl, scoperef, defn_only=defn_only)
        raise CodeIntelError("no return type info for %r" % elem)

    def _hit_from_getattr(self, tokens, elem, scoperef, defn_only=False):
        """Return a hit for a getattr on the given element.

        Returns (<hit>, <num-tokens-consumed>) or raises an CodeIntelError.

        Typically this just does a getattr of tokens[0], but handling
        some multi-level imports can result in multiple tokens being
        consumed.
        """
        # TODO: On failure, call a hook to make an educated guess. Some
        #      attribute names are strong signals as to the object type
        #      -- typically those for common built-in classes.
        first_token = tokens[0]
        self.log("resolve getattr '%s' on %r in %r:", first_token, elem, scoperef)

        if elem.tag == "variable":
            attr = elem.names.get(first_token)
            if attr is not None:
                self.log("attr is %r in %r", attr, elem)
                # update the scoperef, we are now inside the class.
                scoperef = (scoperef[0], scoperef[1] + [elem.get("name")])
                return (attr, scoperef), 1

            citdl = elem.get("citdl")
            if not citdl:
                raise CodeIntelError("no type-inference info for %r" % elem)
            self._check_infinite_recursion(citdl)
            tokens = list(self._tokenize_citdl_expr(citdl)) + tokens
            # self.log("citdl tokens: %r", tokens)

            hit, nconsumed = self._hit_from_tokens(tokens, citdl, scoperef, variable=elem, defn_only=defn_only)
            if hit is not None:
                return hit, nconsumed

            raise CodeIntelError("could not resolve '%s' getattr on %r in %r"
                                % (first_token, elem, scoperef))

        assert elem.tag == "scope"
        ilk = elem.get("ilk")
        if ilk == "function":
            attr = elem.names.get(first_token)
            if attr is not None:
                ilk = elem.get("ilk")
                if ilk in ("class", "instance", "object", "interface", "function"):
                    # update the scoperef, we are now inside the function.
                    scoperef = (scoperef[0], scoperef[1] + [elem.get("name")])
                    return (attr, scoperef), 1
            # Internal function arguments and variable should
            # *not* resolve. And we don't support function
            # attributes.

        elif ilk in ("class", "instance"):
            attr = elem.names.get(first_token)
            if attr is not None:
                self.log("attr is %r in %r", attr, elem)
                # update the scoperef, we are now inside the class.
                scoperef = (scoperef[0], scoperef[1] + [elem.get("name")])
                return (attr, scoperef), 1

            self.debug("look for %r from imports in %r", tokens, elem)
            hit, nconsumed = self._hit_from_elem_imports(tokens, elem, defn_only=defn_only)
            if hit is not None:
                return hit, nconsumed

            for classref in elem.get("classrefs", "").split():
                try:
                    self.log("is '%s' from base class: %r?", first_token,
                             classref)
                    base_elem, base_scoperef = self._hit_from_type_inference(classref, scoperef, defn_only=defn_only)
                    return self._hit_from_getattr(tokens, base_elem,
                                                  base_scoperef, defn_only=defn_only)
                except CodeIntelError as ex:
                    self.log("could not resolve classref '%s' on scoperef %r",
                             classref, scoperef, )
                    # Was not available, try the next class then.

        elif ilk == "object":
            attr = elem.names.get(first_token)
            if attr is not None:
                self.log("attr is %r in %r", attr, elem)
                # update the scoperef, we are now inside the object.
                scoperef = (scoperef[0], scoperef[1] + [elem.get("name")])
                return (attr, scoperef), 1

            self.debug("look for %r from imports in %r", tokens, elem)
            hit, nconsumed = self._hit_from_elem_imports(tokens, elem, defn_only=defn_only)
            if hit is not None:
                return hit, nconsumed

            for objectref in elem.get("objectrefs", "").split():
                try:
                    self.log("is '%s' from base object: %r?", first_token,
                             objectref)
                    base_elem, base_scoperef = self._hit_from_type_inference(objectref, scoperef, defn_only=defn_only)
                    return self._hit_from_getattr(tokens, base_elem,
                                                  base_scoperef, defn_only=defn_only)
                except CodeIntelError as ex:
                    self.log("could not resolve objectref '%s' on scoperef %r",
                             objectref, scoperef, )
                    # Was not available, try the next object then.

        elif ilk == "interface":
            attr = elem.names.get(first_token)
            if attr is not None:
                self.log("attr is %r in %r", attr, elem)
                # update the scoperef, we are now inside the interface.
                scoperef = (scoperef[0], scoperef[1] + [elem.get("name")])
                return (attr, scoperef), 1

            self.debug("look for %r from imports in %r", tokens, elem)
            hit, nconsumed = self._hit_from_elem_imports(tokens, elem, defn_only=defn_only)
            if hit is not None:
                return hit, nconsumed

            for interfaceref in elem.get("interfacerefs", "").split():
                try:
                    self.log("is '%s' from base interface: %r?", first_token,
                             interfaceref)
                    base_elem, base_scoperef = self._hit_from_type_inference(interfaceref, scoperef, defn_only=defn_only)
                    return self._hit_from_getattr(tokens, base_elem,
                                                  base_scoperef, defn_only=defn_only)
                except CodeIntelError as ex:
                    self.log("could not resolve interfaceref '%s' on scoperef %r",
                             interfaceref, scoperef, )
                    # Was not available, try the next interface then.

        elif ilk == "blob":
            attr = elem.names.get(first_token)
            if attr is not None:
                self.log("attr is %r in %r", attr, elem)
                return (attr, scoperef), 1

            self.debug("look for %r from imports in %r", tokens, elem)
            hit, nconsumed = self._hit_from_elem_imports(tokens, elem, defn_only=defn_only)
            if hit is not None:
                return hit, nconsumed

        else:
            raise NotImplementedError("unexpected scope ilk: %r" % ilk)

        # Scope with citdl type:
        citdl = elem.get("citdl")
        if citdl:
            elem, scoperef = self._hit_from_type_inference(citdl, scoperef, defn_only)
            return self._hit_from_getattr(tokens, elem, scoperef, defn_only=defn_only)

        raise CodeIntelError("could not resolve '%s' getattr on %r in %r"
                             % (first_token, elem, scoperef))

    def _hit_from_variable_type_inference(self, elem, scoperef, defn_only=False):
        """Resolve the type inference for 'elem' at 'scoperef'."""
        citdl = elem.get("citdl")
        if not citdl:
            raise CodeIntelError("no type-inference info for %r" % elem)
        self.log("resolve '%s' type inference for %r:", citdl, elem)

        return self._hit_from_citdl(citdl, scoperef, variable=elem, defn_only=defn_only)

    def _hit_from_type_inference(self, citdl, scoperef, variable=None, defn_only=False):
        """Resolve the 'citdl' type inference at 'scoperef'."""
        self.log("resolve '%s' type inference:", citdl)
        return self._hit_from_citdl(citdl, scoperef, variable=variable, defn_only=defn_only)

    @property
    def stdlib(self):
        # XXX Presume last lib is stdlib.
        return self.buf.libs[-1]

    _built_in_blob = None

    @property
    def built_in_blob(self):
        if self._built_in_blob is None:
            self._built_in_blob = self.stdlib.get_blob("*")
        return self._built_in_blob

    def parent_scoperef_from_scoperef(self, scoperef):
        blob, lpath = scoperef
        if lpath:
            parent_lpath = lpath[:-1]
            if parent_lpath:
                elem = self._elem_from_scoperef((blob, parent_lpath))
                if elem.get("ilk") in ("class", "instance"):
                    # ECMAScript eval shouldn't consider the class-level
                    # scope as a parent scope when resolving from the
                    # top-level. (test ecmascript/cpln/skip_class_scope)
                    parent_lpath = parent_lpath[:-1]
            return (blob, parent_lpath)
        elif blob is self._built_in_blob:
            return None
        else:
            return (self.built_in_blob, [])

    def _elem_from_scoperef(self, scoperef):
        """A scoperef is (<blob>, <lpath>). Return the actual elem in
        the <blob> ciElementTree being referred to.
        """
        elem = scoperef[0]
        for lname in scoperef[1]:
            elem = elem.names[lname]
        return elem
