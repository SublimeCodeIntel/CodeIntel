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
# Portions created by ActiveState Software Inc are Copyright (C) 2004-2006
# ActiveState Software Inc. All Rights Reserved.
#
# Portions created by German Mendez Bravo (Kronuz) are Copyright (C) 2017
# German Mendez Bravo (Kronuz). All Rights Reserved.
#
# Contributor(s):
#   Trent Mick (TrentM@ActiveState.com)
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
"""
    ecmacile - a Code Intelligence Language Engine for the ECMAScript language

    Module Usage:
        from ecmacile import scan
        mtime = os.stat("foo.js")[stat.ST_MTIME]
        content = open("foo.js", "r").read()
        scan(content, "foo.js", mtime=mtime)

    Command-line Usage:
        ecmacile.py [<options>...] [<ECMAScript files>...]

    Options:
        -h, --help          dump this help and exit
        -V, --version       dump this script's version and exit
        -v, --verbose       verbose output, use twice for more verbose output
        -f, --filename <path>   specify the filename of the file content
                            passed in on stdin, this is used for the "path"
                            attribute of the emitted <file> tag.
        --md5=<string>      md5 hash for the input
        --mtime=<secs>      modification time for output info, in #secs since
                            1/1/70.
        -L, --language <name>
                            the language of the file being scanned
        -c, --clock         print timing info for scans (CIX is not printed)

    One or more ECMAScript files can be specified as arguments or content can be
    passed in on stdin. A directory can also be specified, in which case
    all .js, .jsx and .es files in that directory are scanned.

    This is a Language Engine for the Code Intelligence (codeintel) system.
    Code Intelligence XML format. See:
        http://specs.activestate.com/Komodo_3.0/func/code_intelligence.html

    The command-line interface will return non-zero iff the scan failed.
"""
# Dev Notes:
# <none>
#
# TODO:
# - type inferencing: asserts
# - type inferencing: return statements
# - type inferencing: calls to isinstance
# - special handling for None may be required
# - Comments and doc strings. What format?
#   - JavaDoc - type hard to parse and not reliable
#     (http://java.sun.com/j2se/javadoc/writingdoccomments/).
#   - PHPDoc? Possibly, but not that rigorous.
#   - Grouch (http://www.mems-exchange.org/software/grouch/) -- dunno yet.
#     - Don't like requirement for "Instance attributes:" landmark in doc
#       strings.
#     - This can't be a full solution because the requirement to repeat
#       the argument name doesn't "fit" with having a near-by comment when
#       variable is declared.
#     - Two space indent is quite rigid
#     - Only allowing attribute description on the next line is limiting.
#     - Seems focussed just on class attributes rather than function
#       arguments.
#   - Perhaps what PerlCOM POD markup uses?
#   - Home grown? My own style? Dunno
# - make type inferencing optional (because it will probably take a long
#   time to generate), this is tricky though b/c should the CodeIntel system
#   re-scan a file after "I want type inferencing now" is turned on? Hmmm.
# - [lower priority] handle staticmethod(methname) and
#   classmethod(methname). This means having to delay emitting XML until
#   end of class scope and adding .visitCallFunc().
# - [lower priority] look for associated comments for variable
#   declarations (as per VS.NET's spec, c.f. "Supplying Code Comments" in
#   the VS.NET user docs)
from __future__ import print_function

import os
import sys
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import getopt
from hashlib import md5
import re
import logging
import pprint
import glob
import time
import stat
from six.moves import cStringIO as StringIO
try:
    from io import BytesIO
except ImportError:
    BytesIO = StringIO
import six
from collections import OrderedDict

# this particular ET is different from xml.etree and is expected
# to be returned from scan_et() by the clients of this module
import ciElementTree as ET

import esprima

from codeintel2.common import CILEError
from codeintel2.jsdoc import JSDoc as RealJSDoc, JSDocParameter
from codeintel2 import util

__LOCAL__ = "__local__"
__EXPORTED__ = "__exported__"
__INSTANCEVAR__ = "__instancevar__"


# ---- exceptions

class ESCILEError(CILEError):
    pass


# ---- global data
_version_ = (0, 1, 0)
log = logging.getLogger("codeintel.ecmacile")
# log.setLevel(logging.DEBUG)
util.makePerformantLogger(log)

_gClockIt = 0   # if true then we are gathering timing data
_gClock = None  # if gathering timing data this is set to time retrieval fn
_gStartTime = None   # start time of current file being scanned


CITDL_MODULE = "module"
CITDL_CLASS = "class"
CITDL_OBJECT = "object"
CITDL_INTERFACE = "interface"
CITDL_FUNCTION = "function"

CITDL_VOID = "void"
CITDL_NULL = "null"
CITDL_UNDEFINED = "undefined"
CITDL_STRING = "string"
CITDL_NUMBER = "number"
CITDL_BOOLEAN = "boolean"

CITDL_ARRAY = "Array"
CITDL_INSTANCE = "Object"
CITDL_REGEXP = "RegExp"

CITDL_REQUIRE = "require(string)"

CITDL_EMPTY = (CITDL_UNDEFINED, CITDL_NULL, CITDL_VOID)


JSDocParameter.type_map = {
    "void": CITDL_VOID,
    "null": CITDL_NULL,
    "undefined": CITDL_UNDEFINED,
    "array": CITDL_ARRAY,
    "function": CITDL_FUNCTION,
    "object": CITDL_INSTANCE,
    "string": CITDL_STRING,
    "number": CITDL_NUMBER,
    "boolean": CITDL_BOOLEAN,
    "regex": CITDL_REGEXP,
}


def JSDocParameter____init__(self, paramname, paramtype=None, doc=None):
    self.paramname = paramname
    self.paramtype = paramtype
    self.doc = doc

    if paramname:
        self.optional = paramname[0] == '[' and paramname[-1] == ']'
        name = paramname[1:-1] if self.optional else paramname
        name, _, default = name.partition('=')
        self.name = name.strip()
        self.default = default.strip()
    else:
        self.name = None
        self.default = None
        self.optional = None

    if paramtype:
        paramtype = paramtype.strip()
        paramtype = paramtype.lstrip('{')  # FIXME: Bug in JSDoc "{string|function}" gets "{string"
        self.type = JSDocParameter.type_map.get(paramtype.lower(), paramtype)
    else:
        self.type = None


JSDocParameter.__init__ = JSDocParameter____init__


class JSDoc(RealJSDoc):
    def __init__(self, comment=None, strip_html_tags=False):
        RealJSDoc.__init__(self, comment=comment, strip_html_tags=strip_html_tags)
        params_dict = {}
        for param in self.params:
            params_dict[param.paramname] = param
        self.params_dict = params_dict


# ---- internal routines and classes
def _isobject(namespace):
    return (len(namespace["types"]) == 1 and CITDL_OBJECT in namespace["types"] or (
        CITDL_MODULE not in namespace["types"] and
        CITDL_CLASS not in namespace["types"] and
        CITDL_INTERFACE not in namespace["types"] and
        CITDL_FUNCTION not in namespace["types"] and
        CITDL_REQUIRE not in namespace["types"] and
        namespace["symbols"]))


def _isclass(namespace):
    return (len(namespace["types"]) == 1 and CITDL_CLASS in namespace["types"])


def _isinterface(namespace):
    return (len(namespace["types"]) == 1 and CITDL_INTERFACE in namespace["types"])


def _isfunction(namespace):
    return (len(namespace["types"]) == 1 and CITDL_FUNCTION in namespace["types"])


def _isrequire(namespace):
    return (len(namespace["types"]) == 1 and CITDL_REQUIRE in namespace["types"])


def getAttrStr(attrs):
    """Construct an XML-safe attribute string from the given attributes

        "attrs" is a dictionary of attributes

    The returned attribute string includes a leading space, if necessary,
    so it is safe to use the string right after a tag name. Any Unicode
    attributes will be encoded into UTF8 encoding as part of this process.
    """
    from xml.sax.saxutils import quoteattr
    s = ''
    for attr, value in list(attrs.items()):
        if not isinstance(value, six.string_types):
            value = six.text_type(value).encode("utf-8")
        elif isinstance(value, six.text_type):
            value = value.encode("utf-8")
        s += ' %s=%s' % (attr, quoteattr(value))
    return s


# match 0x00-0x1f except TAB(0x09), LF(0x0A), and CR(0x0D)
_encre = re.compile('([\x00-\x08\x0b\x0c\x0e-\x1f])')


def xmlencode(s):
    """Encode the given string for inclusion in a UTF-8 XML document.

    Note: s must *not* be Unicode, it must be encoded before being passed in.

    Specifically, illegal or unpresentable characters are encoded as
    XML character entities.
    """
    # As defined in the XML spec some of the character from 0x00 to 0x19
    # are not allowed in well-formed XML. We replace those with entity
    # references here.
    #   http://www.w3.org/TR/2000/REC-xml-20001006#charsets
    #
    # Dev Notes:
    # - It would be nice if ECMAScript has a codec for this. Perhaps we
    #   should write one.
    # - Eric, at one point, had this change to '_xmlencode' for rubycile:
    #    p4 diff2 -du \
    #        //depot/main/Apps/Komodo-devel/src/codeintel/ruby/rubycile.py#7 \
    #        //depot/main/Apps/Komodo-devel/src/codeintel/ruby/rubycile.py#8
    #   but:
    #        My guess is that there was a bug here, and explicitly
    #        utf-8-encoding non-ascii characters fixed it. This was a year
    #        ago, and I don't recall what I mean by "avoid shuffling the data
    #        around", but it must be related to something I observed without
    #        that code.

    # replace with XML decimal char entity, e.g. '&#7;'
    return _encre.sub(lambda m: '&#%d;' % ord(m.group(1)), s)


def cdataescape(s):
    """Return the string escaped for inclusion in an XML CDATA section.

    Note: Any Unicode will be encoded to UTF8 encoding as part of this process.

    A CDATA section is terminated with ']]>', therefore this token in the
    content must be escaped. To my knowledge the XML spec does not define
    how to do that. My chosen escape is (courteousy of EricP) is to split
    that token into multiple CDATA sections, so that, for example:

        blah...]]>...blah

    becomes:

        blah...]]]]><![CDATA[>...blah

    and the resulting content should be copacetic:

        <b><![CDATA[blah...]]]]><![CDATA[>...blah]]></b>
    """
    if isinstance(s, six.text_type):
        s = s.encode("utf-8")
    parts = s.split("]]>")
    return "]]]]><![CDATA[>".join(parts)


def _unistr(x):
    if isinstance(x, six.text_type):
        return x
    elif isinstance(x, six.binary_type):
        return x.decode('utf8')
    else:
        return six.text_type(x)


def _et_attrs(attrs):
    return dict((_unistr(k), xmlencode(_unistr(v))) for k, v in list(attrs.items())
                if v is not None)


def _et_data(data):
    return xmlencode(_unistr(data))


def _node_attrs(node, extra_attributes=[], **kw):
    return dict(name=node["name"],
                line=node.get("line"),
                lineend=node.get("lineend"),
                start=node.get("start"),
                end=node.get("end"),
                doc=node.get("doc"),
                attributes=" ".join(node.get("attributes", []) + extra_attributes) or None,
                **kw)


def _node_citdls(node):
    # 'guesses' is a types dict: {<type guess>: <score>, ...}
    guesses = node.get("types", {})
    for item in sorted(reversed(list(guesses.items())), key=lambda x: -x[1]):
        citdl = item[0]
        if citdl:
            ts = citdl.split(None, 1)
            # Don't emit void types, it does not help us.
            if ts[0] not in CITDL_EMPTY:
                citdl = ts[0]  # XXX Drop the <start-scope> part of CITDL for now.
                yield citdl


def _node_citdl(node):
    citdls = list(_node_citdls(node))
    if citdls:
        return citdls[0]


class AST2CIXVisitor(esprima.NodeVisitor):
    """Generate Code Intelligence XML (CIX) from walking a ECMAScript AST tree.

    This just generates the CIX content _inside_ of the <file/> tag. The
    prefix and suffix have to be added separately.

    Note: All node text elements are encoded in UTF-8 format by the ECMAScript AST
          tree processing, no matter what encoding is used for the file's
          original content. The generated CIX XML will also be UTF-8 encoded.

          ECMAScript AST docs at:
          http://esprima.org
    """
    DEBUG = 0

    def __init__(self, moduleName=None, content=None, filename=None, lang='ECMAScript'):
        self.lang = lang
        if self.DEBUG is None:
            self.DEBUG = log.isEnabledFor(logging.DEBUG)
        self.moduleName = moduleName
        self.content = content
        self.filename = filename
        if content and self.DEBUG:
            self.lines = content.splitlines(0)
        else:
            self.lines = None
        # Symbol Tables (dicts) are built up for each scope. The namespace
        # stack to the global-level is maintain in self.nsstack.
        self.st = {  # the main module symbol table
            # <scope name>: <namespace dict>
        }
        self.nsstack = []
        self.cix = ET.TreeBuilder()
        self.tree = None

        self.uniques = {}

    def _unique_id(self, name):
        if name not in self.uniques:
            self.uniques[name] = 0
        unique_name = "____%s_%s" % (name, self.uniques[name])
        self.uniques[name] += 1
        return unique_name

    def get_type(self, obj):
        typ = type(obj.value)
        return {
            type(None): CITDL_NULL,
            type(u''): CITDL_STRING,
            type(b''): CITDL_STRING,
            type(1): CITDL_NUMBER,
            type(1.1): CITDL_NUMBER,
            type(1 == 1): CITDL_BOOLEAN,
            type(re.compile('')): CITDL_REGEXP,
        }.get(typ, typ.__name__)

    def get_repr(self, obj):
        if obj.regex:
            r = "/%s/%s" % (obj.regex.pattern, obj.regex.flags)
        elif isinstance(obj.value, six.text_type):
            r = repr(obj.value).lstrip('bur')
        else:
            r = repr(obj.value)
        return r

    def parse(self, **kwargs):
        """Parse text into a tree and walk the result"""
        convertor = None

        log.info('FILE: %s', self.filename)
        self.tree = _getAST(convertor, self.content, self.filename, **kwargs)
        # log.debug('TREE: %r', self.tree)

    def generic_visit(self, node):
        """Called if no explicit visitor function exists for a node."""
        # log.info("GENERIC visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        return super(AST2CIXVisitor, self).generic_visit(node)

    def generic_transform(self, node, metadata):
        """Called if no explicit visitor function exists for a node."""
        # log.info("GENERIC transform_%s:%s: %r %r", node.__class__.__name__, metadata.start.line, self.lines and metadata.start.line and self.lines[metadata.start.line - 1], node.keys())
        return super(AST2CIXVisitor, self).generic_transform(node, metadata)

    def walk(self):
        return self.visit(self.tree)

    def emit_start(self, s, attrs={}):
        self.cix.start(s, _et_attrs(attrs))

    def emit_data(self, data):
        self.cix.data(_et_data(data))

    def emit_end(self, s):
        self.cix.end(s)

    def emit_tag(self, s, attrs={}, data=None):
        self.emit_start(s, _et_attrs(attrs))
        if data is not None:
            self.emit_data(data)
        self.emit_end(s)

    def cix_module(self, node):
        """Emit CIX for the given module namespace."""
        # log.debug("cix_module(%s, level=%r)", '.'.join(node["nspath"]), level)
        assert len(node["types"]) == 1 and CITDL_MODULE in node["types"]
        attrs = _node_attrs(node, lang=self.lang, ilk="blob")
        self.emit_start('scope', attrs)
        for import_ in node.get("imports", []):
            self.cix_import(import_)
        self.cix_symbols(node["symbols"])
        self.emit_end('scope')

    def cix_import(self, node):
        # log.debug("cix_import(%s, level=%r)", node["module"], level)
        attrs = node
        self.emit_tag('import', attrs)

    def cix_symbols(self, node, parentIsClass=False):
        # Sort variables by line order. This provide the most naturally
        # readable comparison of document with its associate CIX content.
        vars = sorted(list(node.values()), key=lambda v: v.get("line"))
        for var in vars:
            self.cix_symbol(var, parentIsClass)

    def cix_symbol(self, node, parentIsClass=False):
        if _isclass(node):
            self.cix_class(node)
        elif _isinterface(node):
            self.cix_interface(node)
        elif _isfunction(node):
            self.cix_function(node)
        elif _isobject(node):
            self.cix_object(node)
        else:
            self.cix_variable(node, parentIsClass)

    def cix_variable(self, node, parentIsClass=False):
        # log.debug("cix_variable(%s, level=%r, parentIsClass=%r)",
        #          '.'.join(node["nspath"]), level, parentIsClass)
        extra_attributes = []

        if parentIsClass and "is-class-var" not in node:
            # Special CodeIntel <variable> attribute to distinguish from the
            # usual class variables.
            extra_attributes.append(__INSTANCEVAR__)

        citdl = _node_citdl(node)
        required_library_name = node.get("required_library_name")
        attrs = _node_attrs(node,
                            citdl=citdl,
                            required_library_name=required_library_name,
                            extra_attributes=extra_attributes)

        self.emit_start('variable', attrs)

        self.cix_symbols(node["symbols"])

        self.emit_end('variable')

    def cix_class(self, node):
        # log.debug("cix_class(%s, level=%r)", '.'.join(node["nspath"]), level)

        if node.get("classrefs"):
            citdls = (t for t in (_node_citdl(n) for n in node["classrefs"])
                      if t is not None)
            classrefs = " ".join(citdls)
        else:
            classrefs = None

        extra_attributes = []

        attrs = _node_attrs(node,
                            extra_attributes=extra_attributes,
                            signature=node.get("signature"),
                            ilk="class",
                            classrefs=classrefs)

        self.emit_start('scope', attrs)

        for import_ in node.get("imports", []):
            self.cix_import(import_)

        self.cix_symbols(node["symbols"], parentIsClass=True)

        self.emit_end('scope')

    def cix_interface(self, node):
        # log.debug("cix_interface(%s, level=%r)", '.'.join(node["nspath"]), level)

        if node.get("interfacerefs"):
            citdls = (t for t in (_node_citdl(n) for n in node["interfacerefs"])
                      if t is not None)
            interfacerefs = " ".join(citdls)
        else:
            interfacerefs = None

        extra_attributes = []

        attrs = _node_attrs(node,
                            extra_attributes=extra_attributes,
                            signature=node.get("signature"),
                            ilk="interface",
                            interfacerefs=interfacerefs)

        self.emit_start('scope', attrs)

        for import_ in node.get("imports", []):
            self.cix_import(import_)

        self.cix_symbols(node["symbols"])

        self.emit_end('scope')

    def cix_object(self, node):
        # log.debug("cix_object(%s, level=%r)", '.'.join(node["nspath"]), level)

        if node.get("objectrefs"):
            citdls = (t for t in (_node_citdl(n) for n in node["objectrefs"])
                      if t is not None)
            objectrefs = " ".join(citdls)
        else:
            objectrefs = None

        extra_attributes = []

        citdl = _node_citdl(node)
        required_library_name = node.get("required_library_name")
        attrs = _node_attrs(node,
                            extra_attributes=extra_attributes,
                            signature=node.get("signature"),
                            ilk="object",
                            citdl=citdl,
                            required_library_name=required_library_name,
                            objectrefs=objectrefs)

        self.emit_start('scope', attrs)

        for import_ in node.get("imports", []):
            self.cix_import(import_)

        self.cix_symbols(node["symbols"])

        self.emit_end('scope')

    def cix_argument(self, node):
        # log.debug("cix_argument(%s, level=%r)", '.'.join(node["nspath"]), level)
        extra_attributes = []

        citdl = _node_citdl(node)
        required_library_name = node.get("required_library_name")
        attrs = _node_attrs(node,
                            extra_attributes=extra_attributes,
                            citdl=citdl,
                            required_library_name=required_library_name,
                            ilk="argument")
        self.emit_tag('variable', attrs)

    def cix_function(self, node):
        # log.debug("cix_function(%s, level=%r)", '.'.join(node["nspath"]), level)
        # Determine the best return type.
        best_citdl = None
        max_count = 0
        for citdl, count in list(node["returns"].items()):
            if count > max_count:
                best_citdl = citdl

        extra_attributes = []

        attrs = _node_attrs(node,
                            extra_attributes=extra_attributes,
                            returns=best_citdl,
                            signature=node.get("signature"),
                            ilk="function")

        self.emit_start("scope", attrs)

        for import_ in node.get("imports", []):
            self.cix_import(import_)
        argNames = []
        for arg in node["arguments"]:
            argNames.append(arg["name"])
            self.cix_argument(arg)
        symbols = {}  # don't re-emit the function arguments
        for symbolName, symbol in list(node["symbols"].items()):
            if symbolName not in argNames:
                symbols[symbolName] = symbol
        self.cix_symbols(symbols)
        # XXX <returns/> if one is defined
        self.emit_end('scope')

    def getCIX(self, path):
        """Return CIX content for parsed data."""
        log.debug("getCIX")
        self.emit_start('file', dict(lang=self.lang, path=path))
        if self.st:
            moduleNS = self.st[()]
            self.cix_module(moduleNS)
        self.emit_end('file')
        file = self.cix.close()
        return file

    def _parseMemberExpression(self, expr, base):
        object, _, property = expr.rpartition('.')
        property = esprima.nodes.Identifier(property)
        property.loc = base.loc
        property.range = base.range
        if object:
            expression = esprima.nodes.StaticMemberExpression(self._parseMemberExpression(object, base), property)
            expression.loc = base.loc
            expression.range = base.range
            return expression
        return property

    def visit_Module(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        nspath = ()
        namespace = {"name": self.moduleName,
                     "nspath": nspath,
                     "types": OrderedDict({CITDL_MODULE: 0}),
                     "symbols": {}}

        doc = None
        if node.body:
            leadingComments = node.body[0].leadingComments
            if leadingComments:
                doc = "/*%s*/" % "\n".join(d.value for d in leadingComments if d.value.startswith('*'))
        jsdoc = JSDoc(doc) if doc else None
        if jsdoc:
            if jsdoc.doc:
                namespace["doc"] = jsdoc.doc

        self.st[nspath] = namespace
        self.nsstack.append(namespace)
        self.generic_visit(node)
        self.nsstack.pop()

    def visit_ReturnStatement(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self.generic_visit(node)

        # If there's already a variable assigned to the node, use it:
        variable = node.argument and node.argument._variable
        if variable:
            if _isclass(variable) or _isinterface(variable) or _isfunction(variable) or _isobject(variable) or _isrequire(variable):
                citdl_types = [".".join(variable["nspath"])]
            else:
                citdl_types = list(variable["types"].keys())
        else:
            citdl_types = self._guessTypes(node.argument)

        for citdl in citdl_types:
            if citdl:
                ts = citdl.split(None, 1)
                if ts[0] not in CITDL_EMPTY:
                    func_node = self.nsstack[-1]
                    if "returns" in func_node:
                        t = func_node["returns"]
                        citdl = ts[0]  # XXX Drop the <start-scope> part of CITDL for now.
                        t[citdl] = t.get(citdl, 0) + 1

    def _createObject(self, type, parent, node, extra_attributes):
        nspath = parent["nspath"]

        namespace = {
            "types": OrderedDict({type: 0}),
            "%srefs" % type: [],
            "symbols": {},
        }

        bodies = node.body
        if bodies and not isinstance(bodies, list):
            bodies = bodies.body
        if bodies and not isinstance(bodies, list):
            bodies = [bodies]

        doc = None
        if node.body:
            leadingComments = node.leadingComments
            if leadingComments:
                doc = "/*%s*/" % "\n".join(d.value for d in leadingComments if d.value.startswith('*'))
        jsdoc = JSDoc(doc) if doc else None
        if jsdoc:
            if jsdoc.doc:
                namespace["doc"] = jsdoc.doc

        namespace["declaration"] = namespace
        namespace["line"] = node.loc.start.line
        namespace["start"] = node.range[0]
        namespace["end"] = node.range[1]
        if bodies:
            lastNode = bodies[-1]
            namespace["lineend"] = lastNode.loc.end.line
            namespace["end"] = lastNode.range[1]

        name = None
        if node._member or node._field:
            if node._member:
                name = node._member.property.name
            else:  # if node._field:
                name = node._field.name
        if not name and node.id:
            name = node.id.name
        if not name and node.name:
            name = node.name
        if not name:
            name = self._unique_id(type)

        nspath = nspath + (name,)
        namespace["nspath"] = nspath
        namespace["name"] = name

        # self.st[nspath] = namespace  # Objects don't add to the scope's symbol table
        parent["symbols"][name] = namespace

        attributes = []
        namespace["attributes"] = attributes
        namespace["attributes"].extend(extra_attributes)

        node._parent = parent
        node._variable = namespace

        return namespace

    def visit_JSXElement(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitJSXElement(node)

    def _visitJSXElement(self, node, extra_attributes=[]):
        parent = self.nsstack[-1]

        node.name = self._unique_id(node.openingElement.name.name)
        namespace = self._createObject(CITDL_OBJECT, parent, node, extra_attributes)
        namespace["objectrefs"] = [{"name": "Object", "types": OrderedDict({CITDL_INSTANCE: 0})}]

        # Guess JSX element type:
        for citdl in self._guessTypes(node.openingElement.name.name):
            # ts = citdl.split(None, 1)
            # ts[0] += "()"
            # citdl = " ".join(ts)
            if citdl not in namespace["types"]:
                namespace["types"][citdl] = 0
            namespace["types"][citdl] += 1

        namespace["attributes"].append("__jsx__")

        self.nsstack.append(namespace)

        node.openingElement.name = "props"
        props = self._createObject(CITDL_OBJECT, namespace, node.openingElement, extra_attributes)
        props["types"][CITDL_INSTANCE] = 0
        props["objectrefs"] = [{"name": "Object", "types": OrderedDict({CITDL_INSTANCE: 0})}]
        self.nsstack.append(props)
        self.visit(node.openingElement)
        self.nsstack.pop()

        if node.children:
            for child in node.children:
                self.visit(child)
        if node.closingElement:
            self.visit(node.closingElement)

        self.nsstack.pop()

        if __EXPORTED__ in extra_attributes:
            default = self._parseMemberExpression("exports." + namespace["name"], node)
            name = self._parseMemberExpression(namespace["name"], node)
            name._member = default
            self._visitSimpleAssign(default, name, node.loc.start.line, node.range[0], node.range[1])

    def visit_JSXAttribute(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitAssign(node.name, node.value, node.loc.start.line, node.range[0], node.range[1])

    def visit_ExportAllDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())

        exports, citdl = self._resolveObjectRef(u"exports")
        exports["types"][CITDL_REQUIRE] = 0
        exports["required_library_name"] = node.source.value
        if "line" not in exports:
            exports["line"] = node.loc.start.line
            exports["start"] = node.range[0]
            exports["end"] = node.range[1]

        self.generic_visit(node)

    def visit_ExportNamedDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())

        exports, citdl = self._resolveObjectRef(u"exports")
        self.nsstack.append(exports)

        if node.source:
            self._addImports(node)

        if node.specifiers:
            for specifier in node.specifiers:
                typ = specifier.type
                if typ is esprima.Syntax.ExportDefaultSpecifier:
                    specifier.exported = specifier.local
                declaration = specifier.exported if node.source else specifier.local

                # Try resolving the variable for the declaration and use the line where it was declared
                variable, citdl = self._resolveObjectRef(declaration)
                if variable:
                    line = variable.get('line', node.loc.start.line)
                    start = variable.get('start', node.range[0])
                    end = variable.get('end', node.range[1])
                else:
                    line = node.loc.start.line
                    start = node.range[0]
                    end = node.range[1]

                self._visitAssign(specifier.exported, declaration, line, start, end, extra_attributes=["__no_defn__"])

        self.nsstack.pop()

        if node.declaration:
            typ = node.declaration.type
            if typ is esprima.Syntax.VariableDeclaration:
                self._visitVariableDeclaration(node.declaration, extra_attributes=[__EXPORTED__])
            elif typ is esprima.Syntax.AssignmentExpression:
                self._visitAssignmentExpression(node.declaration, extra_attributes=[__EXPORTED__])
            elif typ is esprima.Syntax.ObjectExpression:
                self._visitObject(node.declaration, extra_attributes=[__EXPORTED__])
            elif typ in (esprima.Syntax.ClassDeclaration, esprima.Syntax.ClassExpression):
                self._visitClass(node.declaration, extra_attributes=[__EXPORTED__])
            elif typ in (esprima.Syntax.FunctionDeclaration, esprima.Syntax.FunctionExpression):
                self._visitFunction(node.declaration, extra_attributes=[__EXPORTED__])
            else:
                self.generic_visit(node)

    def visit_ExportDefaultDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())

        exports, citdl = self._resolveObjectRef(u"exports")
        self.nsstack.append(exports)

        default = self._parseMemberExpression(u"default", node)
        node.declaration._field = default
        typ = node.declaration.type
        if typ is esprima.Syntax.AssignmentExpression:
            self._visitAssignmentExpression(node.declaration)
            declaration = node.declaration.left
        else:
            self.visit(node.declaration)
            declaration = node.declaration

        # Try resolving the variable for the declaration and use the line where it was declared
        variable, citdl = self._resolveObjectRef(declaration)
        if variable:
            line = variable.get('line', node.loc.start.line)
            start = variable.get('start', node.range[0])
            end = variable.get('end', node.range[1])
        else:
            line = node.loc.start.line
            start = node.range[0]
            end = node.range[1]

        if typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier, esprima.Syntax.MemberExpression):
            extra_attributes = ["__no_defn__"]
        else:
            extra_attributes = []

        self._visitSimpleAssign(default, declaration, line, start, end, extra_attributes=extra_attributes)

        self.nsstack.pop()

    def visit_ObjectExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        for prop in node.properties:
            if prop.type is esprima.Syntax.Property and not prop.computed:
                prop.value._member = esprima.nodes.StaticMemberExpression(prop.value, prop.key)
                prop.value._member.loc = prop.value.loc
                prop.value._member.range = prop.value.range

        self._visitObject(node)

    def _visitObject(self, node, extra_attributes=[]):
        parent = self.nsstack[-1]
        namespace = self._createObject(CITDL_OBJECT, parent, node, extra_attributes)
        namespace["types"][CITDL_INSTANCE] = 0
        namespace["objectrefs"] = [{"name": "Object", "types": OrderedDict({CITDL_INSTANCE: 0})}]

        self.nsstack.append(namespace)
        self.generic_visit(node)
        self.nsstack.pop()

        if __EXPORTED__ in extra_attributes:
            default = self._parseMemberExpression("exports." + namespace["name"], node)
            name = self._parseMemberExpression(namespace["name"], node)
            name._member = default
            self._visitSimpleAssign(default, name, node.loc.start.line, node.range[0], node.range[1])

    def visit_Property(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        # Propagate comments:
        if not node.value.leadingComments and node.leadingComments:
            node.value.leadingComments = node.leadingComments
        self.generic_visit(node)
        if not node.computed:
            self._visitSimpleAssign(node.key, node.value, node.loc.start.line, node.range[0], node.range[1])

    def visit_SpreadElement(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self.generic_visit(node)
        namespace = self.nsstack[-1]
        if "objectrefs" in namespace:
            baseNode = node.argument
            baseName = self._getExprRepr(baseNode)
            objectref = {"name": baseName, "types": OrderedDict()}
            for t in self._guessTypes(baseNode):
                if t not in objectref["types"]:
                    objectref["types"][t] = 0
                objectref["types"][t] += 1
            namespace["objectrefs"].append(objectref)

    def visit_ClassExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitClass(node)

    def visit_ClassDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitClass(node)

    def _visitClass(self, node, extra_attributes=[]):
        parent = self.nsstack[-1]
        namespace = self._createObject(CITDL_CLASS, parent, node, extra_attributes)
        self.st[namespace["nspath"]] = namespace

        baseNode = node.superClass
        if baseNode:
            baseName = self._getExprRepr(baseNode)
            classref = {"name": baseName, "types": OrderedDict()}
            for t in self._guessTypes(baseNode):
                if t not in classref["types"]:
                    classref["types"][t] = 0
                classref["types"][t] += 1
            namespace["classrefs"].append(classref)

        self.nsstack.append(namespace)
        self.generic_visit(node)
        self.nsstack.pop()

        if __EXPORTED__ in extra_attributes:
            default = self._parseMemberExpression("exports." + namespace["name"], node)
            name = self._parseMemberExpression(namespace["name"], node)
            name._member = default
            self._visitSimpleAssign(default, name, node.loc.start.line, node.range[0], node.range[1])

    def _visitInterface(self, node, extra_attributes=[]):
        parent = self.nsstack[-1]
        namespace = self._createObject(CITDL_INTERFACE, parent, node, extra_attributes)
        self.st[namespace["nspath"]] = namespace

        baseNode = node.superClass
        if baseNode:
            baseName = self._getExprRepr(baseNode)
            classref = {"name": baseName, "types": OrderedDict()}
            for t in self._guessTypes(baseNode):
                if t not in classref["types"]:
                    classref["types"][t] = 0
                classref["types"][t] += 1
            namespace["interfacerefs"].append(classref)

        self.nsstack.append(namespace)
        self.generic_visit(node)
        self.nsstack.pop()

        if __EXPORTED__ in extra_attributes:
            default = self._parseMemberExpression("exports." + namespace["name"], node)
            name = self._parseMemberExpression(namespace["name"], node)
            name._member = default
            self._visitSimpleAssign(default, name, node.loc.start.line, node.range[0], node.range[1])

    def visit_FieldDefinition(self, node):
        if node.value:
            node.value.static = node.static
            self._visitSimpleAssign(node.key, node.value, node.loc.start.line, node.range[0], node.range[1])
        else:
            self.generic_visit(node)

    def visit_MethodDefinition(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        # Propagate comments:
        if not node.value.leadingComments and node.leadingComments:
            node.value.leadingComments = node.leadingComments
        node.value.static = node.static
        node.value.id = node.key
        self._visitFunction(node.value)

    def visit_ArrowFunctionExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitFunction(node)

    def visit_FunctionExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitFunction(node)

    def visit_FunctionDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitFunction(node)

    def _visitFunction(self, node, extra_attributes=[]):
        parent = self.nsstack[-1]
        nspath = parent["nspath"]

        namespace = {
            "types": OrderedDict({CITDL_FUNCTION: 0}),
            "returns": OrderedDict(),
            "arguments": [],
            "symbols": {},
        }

        bodies = node.body and node.body.body
        if bodies and not isinstance(bodies, list):
            bodies = [bodies]

        doc = None
        if node.body:
            leadingComments = node.leadingComments
            if leadingComments:
                doc = "/*%s*/" % "\n".join(d.value for d in leadingComments if d.value.startswith('*'))
        jsdoc = JSDoc(doc) if doc else None
        if jsdoc:
            if jsdoc.doc:
                namespace["doc"] = jsdoc.doc
            if jsdoc.returns:
                t = jsdoc.returns.type
                if t not in namespace["returns"]:
                    namespace["returns"][(t, None)] = 0
                namespace["returns"][(t, None)] += 1

        namespace["declaration"] = namespace
        namespace["line"] = node.loc.start.line
        namespace["start"] = node.range[0]
        namespace["end"] = node.range[1]
        if bodies:
            lastNode = bodies[-1]
            namespace["lineend"] = lastNode.loc.end.line
            namespace["end"] = lastNode.range[1]

        name = None
        if node._member or node._field:
            if node._member:
                name = node._member.property.name
            else:  # if node._field:
                name = node._field.name
        if not name and node.id:
            name = node.id.name
        if not name:
            name = self._unique_id("lambda")

        nspath = nspath + (name,)
        namespace["nspath"] = nspath
        namespace["name"] = name

        parentIsClass = _isclass(parent)

        # Determine attributes
        attributes = []
        # attributes.append("private")
        # attributes.append("protected")
        if name == "constructor" and parentIsClass:
            attributes.append("__ctor__")

        # process decorators
        if node.static:
            attributes.append("__staticmethod__")
        # TODO: ... property getter and setter

        namespace["attributes"] = attributes
        namespace["attributes"].extend(extra_attributes)

        if parentIsClass and name == "constructor":
            fallbackSig = parent["name"]
        else:
            fallbackSig = name

        # Handle arguments. The format of the relevant Function attributes
        # makes this a little bit of pain.
        sigArgs = []
        arguments = []
        for idx, param in enumerate(node.params, 1):
            argument = {"types": OrderedDict({"__arg%s" % idx: 0}),
                        "line": node.loc.start.line,
                        "start": node.range[0],
                        "end": node.range[1],
                        "symbols": {},
                        "argument": True}
            typ = param.type
            if typ is esprima.Syntax.ObjectPattern:
                args = []
                for p in param.properties:
                    if p.type is esprima.Syntax.Property and not p.computed:
                        argument = {"types": OrderedDict({"__arg%s.%s" % (idx, p.key.name): 0}),
                                    "line": node.loc.start.line,
                                    "start": node.range[0],
                                    "end": node.range[1],
                                    "symbols": {},
                                    "argument": True}
                        argName = self._getExprRepr(p.value)
                        argument["name"] = argName
                        argument["nspath"] = nspath + (argName,)
                        argument["attributes"] = ["kwargs"]
                        arguments.append(argument)
                        args.append('%s: %s' % (p.key.name, argName) if p.key.name != argName else argName)
                sigArgs.append("{ %s }" % ", ".join(args))
                continue
            elif typ is esprima.Syntax.ArrayPattern:
                args = []
                for i, e in enumerate(param.elements):
                    argument = {"types": OrderedDict({"__arg%s[%s]" % (idx, i): 0}),
                                "line": node.loc.start.line,
                                "start": node.range[0],
                                "end": node.range[1],
                                "symbols": {},
                                "argument": True}
                    argName = self._getExprRepr(e)
                    argument["name"] = argName
                    argument["nspath"] = nspath + (argName,)
                    argument["attributes"] = ["kwargs"]
                    arguments.append(argument)
                    args.append(argName)
                sigArgs.append("[ %s ]" % ", ".join(args))
                continue
            elif typ is esprima.Syntax.RestElement:
                param = param.argument
                argName = param.name
                argument["attributes"] = ["kwargs"]
            elif typ is esprima.Syntax.Identifier:
                argName = param.name
            elif typ is esprima.Syntax.AssignmentPattern:
                argName = self._getExprRepr(param.left)
                defaultNode = param.right
                try:
                    argument["default"] = self._getExprRepr(param.right)
                except ESCILEError as ex:
                    raise ESCILEError("unexpected default argument node type for Function '%s': %s" % (name, ex))
                for t in self._guessTypes(defaultNode):
                    log.info("guessed type: %s ::= %s", argName, t)
                    if t not in argument["types"]:
                        argument["types"][t] = 0
                    argument["types"][t] += 1
            else:
                raise ESCILEError("unexpected argument node type '%s' for Function '%s'" % (typ, name))
            argument["name"] = argName
            argument["nspath"] = nspath + (argName,)
            arguments.append(argument)
            argDocs = jsdoc and jsdoc.params_dict.get(argName)
            if argDocs:
                t = argDocs.type
                if t not in argument["types"]:
                    argument["types"][t] = 0
                if argDocs.default:
                    argument["default"] = argDocs.default
                argument["doc"] = argDocs.doc
            sigArg = argName
            if argument.get("attributes") == "kwargs":
                sigArg = "..." + sigArg
            if "default" in argument:
                sigArg += "=" + argument["default"]
            sigArgs.append(sigArg)

        if parentIsClass and "__staticmethod__" not in attributes:
            # If this is a class method, then add 'this' as a class instance variable.
            this = {"name": "this",
                    "nspath": nspath + ("this",),
                    "types": OrderedDict(),
                    "line": node.loc.start.line,
                    "start": node.range[0],
                    "end": node.range[1],
                    "symbols": {},
                    "argument": True,
                    }
            className = self.nsstack[-1]["nspath"][-1]
            this["types"][className] = 1
            this["declaration"] = self.nsstack[-1]
            namespace["symbols"]["this"] = this

        for argument in arguments:
            if "declaration" not in argument:
                argument["declaration"] = argument  # namespace dict of the declaration
            namespace["arguments"].append(argument)
            namespace["symbols"][argument["name"]] = argument

        fallbackSig += "(%s)" % (", ".join(sigArgs))
        if "__staticmethod__" in attributes:
            fallbackSig += " - staticmethod"

        if "signature" not in namespace:
            namespace["signature"] = fallbackSig

        self.st[nspath] = namespace
        parent["symbols"][name] = namespace

        node._parent = parent
        node._variable = namespace
        if node._field or node._member:
            node._xxx = name

        self.nsstack.append(namespace)
        self.generic_visit(node)
        self.nsstack.pop()

        if "this" in namespace["symbols"]:
            if node._member:
                self._extractThis(namespace, parent)
            elif node._field:
                self._promoteToClass(namespace)

        if __EXPORTED__ in extra_attributes:
            default = self._parseMemberExpression("exports." + name, node)
            name = self._parseMemberExpression(name, node)
            name._member = default
            self._visitSimpleAssign(default, name, node.loc.start.line, node.range[0], node.range[1])

    def visit_CallExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        namespace = self.nsstack[-1]

        if node.arguments and len(node.arguments) == 1:
            callee, callee_type = self._resolveObjectRef(node.callee)
            if callee:
                isRequire = "require" in callee["types"]
                isInteropRequireDefault = "_interopRequireDefault" in callee["types"]
            else:
                isRequire = node.callee.name == "require"
                isInteropRequireDefault = node.callee.name == "_interopRequireDefault"

            if isRequire or isInteropRequireDefault:
                argument = node.arguments[0]
                typ = argument.type

                if typ is esprima.Syntax.Literal:
                    module = argument.value
                elif typ is esprima.Syntax.Identifier:
                    module = argument.name
                else:
                    module = None

                if module:
                    name = None
                    if node._member or node._field:
                        if node._member:
                            name = node._member.property.name
                            obj, citdl = self._resolveObjectRef(node._member.object, spawn=False)
                            if obj:
                                namespace = obj
                        else:  # if node._field:
                            name = node._field.name
                    if not name:
                        name = "____require(%s)" % module

                    if typ is esprima.Syntax.Literal:
                        imports = namespace.setdefault("imports", [])
                        import_ = {"module": module}
                        import_["line"] = node.loc.start.line
                        import_["start"] = node.range[0]
                        import_["end"] = node.range[1]
                        import_["alias"] = name
                        if name == "exports":
                            import_["symbol"] = "*"
                        imports.append(import_)
                        if node._member:
                            node._member._required_library_name = module
                        elif node._field:
                            node._field._required_library_name = module

                    elif isInteropRequireDefault:
                        node._node = self._parseMemberExpression(module, node)

        self.generic_visit(node)

    def visit_ImportDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._addImports(node)
        self.generic_visit(node)

    def _addImports(self, node):
        module = node.source.value
        imports = self.nsstack[-1].setdefault("imports", [])
        for specifier in node.specifiers:
            import_ = {"module": module}
            import_["line"] = specifier.loc.start.line
            import_["start"] = specifier.range[0]
            import_["end"] = specifier.range[1]
            if specifier.imported:
                import_["symbol"] = specifier.imported.name
                if specifier.local and specifier.local.name != specifier.imported.name:
                    import_["alias"] = specifier.local.name
            elif specifier.exported:
                import_["symbol"] = specifier.local.name
                if specifier.exported.name != specifier.local.name:
                    import_["alias"] = specifier.exported.name
            else:
                if specifier.local and specifier.local.name != module:
                    import_["alias"] = specifier.local.name
            imports.append(import_)

    def _extractThis(self, src, dst):
        symbols = src["symbols"]["this"]["symbols"]
        for field in list(symbols):
            symbol = symbols.pop(field)
            symbol["nspath"] = symbol["nspath"][:-2] + (symbol["nspath"][-1],)
            if field in dst["symbols"]:
                for t, s in symbol["types"].items():
                    if t not in dst["symbols"][field]:
                        dst["symbols"][field][t] = 0
                    dst["symbols"][field][t] += s
            else:
                dst["symbols"][field] = symbol

    def _promoteToClass(self, variable):
        """This promotes a function to a class, the function becomes the
        constructor and 'this' variable is added."""
        constructor = {}
        for k in list(variable):
            # copy line to constructor:
            if k in ("line", "lineend", "start", "end"):
                constructor[k] = variable[k]
            # Move almost everything to constructor
            elif k not in ("name", "nspath", "declaration", "attributes"):
                constructor[k] = variable.pop(k)
        nspath = variable["nspath"]
        constructor["name"] = "constructor"
        constructor.setdefault("attributes", []).append("__ctor__")
        constructor["nspath"] = nspath + ("constructor",)
        constructor["declaration"] = variable
        variable.update({
            "types": OrderedDict({CITDL_CLASS: 0}),
            "classrefs": [],
            "attributes": [],
            "symbols": {
                "constructor": constructor,
            },
        })

        if "this" in constructor["symbols"]:
            self._extractThis(constructor, variable)

        # Move non-argument symbols to class:
        for k, v in list(constructor["symbols"].items()):
            if v.get("argument"):
                _nspath = constructor["nspath"] + (v["nspath"][-1],)
                if self.st.pop(v["nspath"], None):
                    self.st[_nspath] = v
                v["nspath"] = _nspath
            else:
                del constructor["symbols"][k]
                variable["symbols"][k] = v
                if CITDL_FUNCTION in v["types"]:
                    v["attributes"].append("__staticmethod__")
                    v["signature"] += " - staticmethod"
                    if "this" in v["symbols"]:
                        del v["symbols"]["this"]
                else:
                    v["is-class-var"] = True

        # If this is a class method, then add 'this' as a class instance variable.
        this = {"name": "this",
                "nspath": constructor["nspath"] + ("this",),
                "types": OrderedDict(),
                "line": constructor["line"],
                "start": constructor["start"],
                "end": constructor["end"],
                "symbols": {},
                "argument": True,
                }
        className = nspath[-1]
        this["types"][className] = 1
        this["declaration"] = variable
        constructor["symbols"]["this"] = this

    def visit_StaticMemberExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())

        # Pass assignment member to object
        node.object._member = node._member
        node.object._field = node._field

        # Treat "prototype" case
        if node.property.name == "prototype":
            variable, citdl = self._resolveObjectRef(node.object)
            if variable:
                if CITDL_FUNCTION in variable["types"]:
                    self._promoteToClass(variable)
            elif node.object.type is esprima.Syntax.Identifier:
                n = esprima.nodes.ClassBody([])
                n.loc = node.loc
                n.range = node.range
                n = esprima.nodes.ClassDeclaration(node.object, None, n)
                n.loc = node.loc
                n.range = node.range
                self.visit(n)

        self.generic_visit(node)

    def visit_ExpressionStatement(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        # Propagate comments:
        if not node.expression.leadingComments and node.leadingComments:
            node.expression.leadingComments = node.leadingComments
        self.generic_visit(node)

    def visit_VariableDeclaration(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitVariableDeclaration(node)

    def _visitVariableDeclaration(self, node, extra_attributes=[]):
        # kind = node.kind  # var, let or const
        for declaration in node.declarations:
            if declaration.init:
                declaration.init._field = declaration.id
            self._visitAssign(declaration.id, declaration.init, declaration.loc.start.line, declaration.range[0], declaration.range[1], extra_attributes=extra_attributes)

    def visit_AssignmentExpression(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self._visitAssignmentExpression(node)

    def _visitAssignmentExpression(self, node, extra_attributes=[]):
        # Propagate comments:
        if not node.right.leadingComments and node.leadingComments:
            node.right.leadingComments = node.leadingComments
        if node.left.type is esprima.Syntax.MemberExpression:
            node.right._member = node.left
        else:
            node.right._field = node.left
        if node.operator == '=':
            self._visitAssign(node.left, node.right, node.loc.start.line, node.range[0], node.range[1], extra_attributes=extra_attributes)
        else:
            log.info("_visitAssignmentExpression:: skipping unknown operator: %r", node.operator)
            self.generic_visit(node)

    def _visitAssign(self, lhsNode, rhsNode, lineno, start, end, extra_attributes=[]):
        log.debug("_visitAssign(lhsNode=%r, rhsNode=%r)", lhsNode, rhsNode)
        typ = getattr(lhsNode, 'type', type(lhsNode))

        if rhsNode:
            # Try visiting the right side; do this after resolving (and using)
            # the variable's object declaration scope, when it's being assigned
            # as a member expression:
            if typ is esprima.Syntax.MemberExpression:
                variable, _ = self._resolveObjectRef(lhsNode.object)
            else:
                variable = None

            # Visit:
            if variable:
                self.nsstack.append(variable)
                self.visit(rhsNode)
                self.nsstack.pop()
            else:
                self.visit(rhsNode)

            # If the right side was overriden
            if rhsNode._node:
                rhsNode = rhsNode._node

        self.visit(lhsNode)

        if typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier, esprima.Syntax.MemberExpression):
            # E.g.:
            #   foo = ...       (Identifier)
            #   foo.bar = ...   (MemberExpression)
            #   foo[1] = ...    (MemberExpression)
            self._visitSimpleAssign(lhsNode, rhsNode, lineno, start, end, extra_attributes=extra_attributes)

        elif typ is esprima.Syntax.ArrayPattern:
            # E.g.:
            #   foo, bar = ...
            #   [foo, bar] = ...
            # If the RHS is an array, then we update each assigned-to variable.
            rtyp = getattr(rhsNode, 'type', type(rhsNode))
            if rtyp is esprima.Syntax.ArrayExpression:
                rhsNumElements = len(rhsNode.elements)
            for i, left in enumerate(lhsNode.elements):
                if rtyp is esprima.Syntax.Identifier:
                    right = esprima.nodes.ComputedMemberExpression(rhsNode, esprima.nodes.Literal(i, "%d" % i))
                    right.loc = rhsNode.loc
                    right.range = rhsNode.range
                elif rtyp is esprima.Syntax.MemberExpression:
                    right = esprima.nodes.ComputedMemberExpression(rhsNode, esprima.nodes.Literal(i, "%d" % i))
                    right.loc = rhsNode.loc
                    right.range = rhsNode.range
                elif rtyp is esprima.Syntax.CallExpression:
                    right = esprima.nodes.ComputedMemberExpression(rhsNode, esprima.nodes.Literal(i, "%d" % i))
                    right.loc = rhsNode.loc
                    right.range = rhsNode.range
                if rtyp is esprima.Syntax.ArrayExpression:
                    right = rhsNode.elements[i] if i < rhsNumElements else None
                elif rtyp is esprima.Syntax.ObjectExpression:
                    right = None
                else:
                    log.info("visitAssign:: skipping unknown rhsNode type: %s", rtyp)
                    break
                self._visitSimpleAssign(left, right, lineno, start, end, extra_attributes=extra_attributes)

        elif typ is esprima.Syntax.ObjectPattern:
            # E.g.:
            #   {foo, bar} = ...
            #   {foo, bar: BAR} = ...
            # If the RHS is an object, then we update each assigned-to variable.
            rtyp = getattr(rhsNode, 'type', type(rhsNode))
            if rtyp is esprima.Syntax.ObjectExpression:
                rhsProperties = dict((rprop.key.name, rprop) for rprop in rhsNode.properties if rprop.type is esprima.Syntax.Property and not rprop.computed)
            for prop in lhsNode.properties:
                left = prop.value
                if rtyp is esprima.Syntax.Identifier:
                    right = esprima.nodes.StaticMemberExpression(rhsNode, prop.key)
                    right.loc = rhsNode.loc
                    right.range = rhsNode.range
                elif rtyp is esprima.Syntax.MemberExpression:
                    right = esprima.nodes.StaticMemberExpression(rhsNode, prop.key)
                    right.loc = rhsNode.loc
                    right.range = rhsNode.range
                elif rtyp is esprima.Syntax.CallExpression:
                    right = esprima.nodes.StaticMemberExpression(rhsNode, prop.key)
                    right.loc = rhsNode.loc
                    right.range = rhsNode.range
                elif rtyp is esprima.Syntax.ObjectExpression:
                    right = rhsProperties.get(prop.key.name)
                elif rtyp is esprima.Syntax.ArrayExpression:
                    right = None
                else:
                    log.info("visitAssign:: skipping unknown rhsNode type: %s", rtyp)
                    break
                self._visitSimpleAssign(left, right, lineno, start, end, extra_attributes=extra_attributes)

        else:
            raise ESCILEError("unexpected type of LHS of assignment: %s" % typ)

    def _visitSimpleAssign(self, lhsNode, rhsNode, line, start, end, extra_attributes=[]):
        """Handle a simple assignment: assignment to a symbol name or to
        an attribute of a symbol name. If the given left-hand side (lhsNode)
        is not an node type that can be handled, it is dropped.
        """
        log.debug("_visitSimpleAssign(lhsNode=%r, rhsNode=%r)", lhsNode, rhsNode)
        ns = self.nsstack[-1]
        typ = getattr(lhsNode, 'type', type(lhsNode))

        if typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier):
            # E.g.:  foo = ...
            # Assign this to the local namespace, unless there was a
            # 'global' statement. (XXX Not handling 'global' yet.)
            varName = lhsNode.name
            self._assignVariable(varName, ns, rhsNode, line, start, end, isClassVar=_isclass(ns), extra_attributes=extra_attributes)

        elif typ is esprima.Syntax.MemberExpression:
            if lhsNode.computed:
                # E.g.:  bar[1] = "foo"
                ptyp = lhsNode.property.type
                if ptyp is not esprima.Syntax.Literal:
                    # We don't bother with these: too hard.
                    log.info("simpleAssign:: skipping subscript - too hard")
                    return

            # Try to figure out the prototype:
            lhsPrototype = None
            if lhsNode.property.name == "prototype":
                lhsPrototype = lhsNode
            elif lhsNode.object.type is esprima.Syntax.MemberExpression:
                if lhsNode.object.property.name == "prototype":
                    lhsPrototype = lhsNode.object

            if lhsPrototype:
                _xxx = rhsNode._xxx
                rhsNode._xxx = None
                if _xxx in ns["symbols"]:
                    del self.st[ns["symbols"][_xxx]["nspath"]]
                    del ns["symbols"][_xxx]
                # Assignments to prototype work the same as if declared inside a class:
                namespace, citdl = self._resolveObjectRef(lhsPrototype.object)
                if namespace and isinstance(rhsNode, esprima.nodes.Node):
                    self.nsstack.append(namespace)
                    typ = rhsNode.type
                    if typ is esprima.Syntax.Literal:
                        self._assignVariable(lhsNode.property.name, namespace, rhsNode, line, start, end, isClassVar=False, extra_attributes=extra_attributes)
                    elif typ is esprima.Syntax.ObjectExpression:
                        for prop in rhsNode.properties:
                            if prop.type is esprima.Syntax.Property:
                                if not prop.computed:
                                    self._assignVariable(prop.key.name, namespace, prop.value, prop.loc.start.line, prop.range[0], prop.range[1], isClassVar=False)
                                else:
                                    # We don't bother with these: too hard.
                                    log.info("simpleAssign:: skipping computed - too hard")
                    else:
                        rhsNode.id = lhsNode.property
                        self.visit(rhsNode)
                    self.nsstack.pop()
            else:
                variable, citdl = self._resolveObjectRef(lhsNode.object)
                if not variable and lhsNode.object.type is esprima.Syntax.ThisExpression:
                    # Spawn 'this' on the fly:
                    variable = {"name": "this",
                                "nspath": ns["nspath"] + ("this",),
                                "types": OrderedDict(),
                                "line": ns.get("line", line),
                                "start": ns.get("start", start),
                                "end": ns.get("end", end),
                                "symbols": {},
                                "argument": True,
                                }
                    className = ns["nspath"][-1]
                    variable["types"][className] = 1
                    variable["declaration"] = variable
                    ns["symbols"]["this"] = variable

                if variable:
                    self._assignVariable(lhsNode.property.name, variable["declaration"], rhsNode, line, start, end, extra_attributes=extra_attributes)
        else:
            log.debug("could not handle simple assign (module '%s'): "
                      "lhsNode=%r, rhsNode=%r", self.moduleName, lhsNode,
                      rhsNode)
            return

        if lhsNode._required_library_name and rhsNode._assignee:
            varTypes = rhsNode._assignee["types"]
            if CITDL_REQUIRE not in varTypes:
                varTypes[CITDL_REQUIRE] = 0
            rhsNode._assignee["required_library_name"] = lhsNode._required_library_name

    def _assignVariable(self, varName, namespace, rhsNode, line, start, end, isClassVar=False, extra_attributes=[]):
        """Handle a simple variable name assignment.

            "varName" is the variable name being assign to.
            "namespace" is the namespace dict to which to assign the variable.
            "rhsNode" is the Node of the right-hand side of the
                assignment.
            "line" is the line number on which the variable is being assigned.
            "isClassVar" (optional) is a boolean indicating if this var is
                a class variable, as opposed to an instance variable
        """
        nspath = namespace["nspath"]
        log.debug("_assignVariable(varName=%r, namespace %s, rhsNode=%r, line, start, end, isClassVar=%r)",
                  varName, ".".join(nspath), rhsNode, isClassVar)
        variable = namespace["symbols"].get(varName, None)

        if variable is None:
            if rhsNode and rhsNode._xxx and rhsNode._parent:
                variable = rhsNode._parent["symbols"].pop(rhsNode._xxx, None)
                if variable is not None:
                    del self.st[variable["nspath"]]
                    variable["name"] = varName
                    variable["nspath"] = nspath + (varName,)
                    namespace["symbols"][varName] = variable
                    self.st[variable["nspath"]] = variable
                    rhsNode._xxx = None

                    if CITDL_FUNCTION in variable["types"] and "this" not in variable["symbols"]:
                        # If this is a class method, then add 'this' as a class instance variable.
                        this = {"name": "this",
                                "nspath": variable["nspath"] + ("this",),
                                "types": OrderedDict(),
                                "line": variable.get("line", line),
                                "start": variable.get("start", start),
                                "end": variable.get("end", end),
                                "symbols": {},
                                "argument": True,
                                }
                        className = nspath[-1]
                        this["types"][className] = 1
                        this["declaration"] = variable
                        variable["symbols"]["this"] = this

        if variable is None:
            variable = {"name": varName,
                        "nspath": nspath + (varName,),
                        # Could try to parse documentation from a near-by
                        # string.
                        # 'types' is a dict mapping a type name to the number
                        # of times this was guessed as the variable type.
                        "types": OrderedDict(),
                        "symbols": {}}
            # Determine attributes
            attributes = []
            # TODO: figure out attributes (private, protected, etc.)
            variable["attributes"] = attributes

            variable["declaration"] = variable

        if line and "line" not in variable:
            variable["line"] = line
            variable["start"] = start
            variable["end"] = end

        if isClassVar and "is-class-var" not in variable and rhsNode and (rhsNode.static is None or rhsNode.static):
            variable["is-class-var"] = True
            # line number of first class-level assignment wins
            if line:
                variable["line"] = line
                variable["start"] = start
                variable["end"] = end

        variable.setdefault("attributes", []).extend(extra_attributes)

        if rhsNode:
            if rhsNode._member or rhsNode._field:
                expr = self._getExprRepr(rhsNode._member or rhsNode._field)
                if (
                    expr in ("module.exports", "exports") or
                    expr.startswith("module.exports.") or
                    expr.startswith("exports.")
                ) and "__no_defn__" not in variable["attributes"]:
                    variable["attributes"].append("__no_defn__")

            rhsNode._parent = namespace
            rhsNode._assignee = variable

            varTypes = variable["types"]
            for t in self._guessTypes(rhsNode, namespace):
                log.info("guessed type: %s ::= %s", varName, t)
                if t not in varTypes:
                    varTypes[t] = 0
                varTypes[t] += 1

            if "this" in variable["symbols"]:
                if rhsNode._member:
                    self._extractThis(variable, namespace)
                elif rhsNode._field:
                    self._promoteToClass(variable)

            if __EXPORTED__ in extra_attributes:
                default = self._parseMemberExpression("exports." + varName, rhsNode)
                name = self._parseMemberExpression(varName, rhsNode)
                name._member = default
                self._visitSimpleAssign(default, name, rhsNode.loc.start.line, rhsNode.range[0], rhsNode.range[1])

        namespace["symbols"][varName] = variable  # Must be added to symbols after guessing types

        return variable

    def _handleUnknownAssignment(self, lhsNode, rhsNode, lineno, start, end):
        typ = getattr(lhsNode, 'type', type(lhsNode))
        if typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier):
            self._visitSimpleAssign(lhsNode, rhsNode, lineno, start, end)
        elif typ is esprima.Syntax.ArrayExpression:
            for anode in lhsNode.elements:
                self._visitSimpleAssign(anode, rhsNode, lineno, start, end)

    def visit_TryStatement(self, node):
        log.info("visit_%s:%s: %r %r", node.__class__.__name__, node.loc.start.line, self.lines and node.loc.start.line and self.lines[node.loc.start.line - 1], node.keys())
        self.visit(node.block)

        if node.handler:
            self.visit(node.handler)
            if node.handler.param:
                self._handleUnknownAssignment(node.handler.param, None, node.handler.loc.start.line, node.handler.range[0], node.handler.range[1])

        self.visit(node.finalizer)

    def _resolveObjectRef(self, expr, spawn=True):
        """Try to resolve the given expression to a variable namespace.

            "expr" is some kind of Node instance.

        Returns the following 2-tuple for the object:
            (<variable dict>, <CITDL string>)
        where,
            <variable dict> is the defining dict for the variable, e.g.
                    {'name': 'classvar', 'types': {'int': 1}}.
                This is None if the variable could not be resolved.
            <CITDL string> is a string of CITDL code (see the spec) describing
                how to resolve the variable later. This is None if the
                variable could be resolved or if the expression is not
                expressible in CITDL (CITDL does not attempt to be a panacea).
        """
        log.debug("_resolveObjectRef(expr=%r)", expr)
        typ = getattr(expr, 'type', type(expr))

        if isinstance(expr, esprima.nodes.Node):
            if expr._variable is not None:
                return (expr._variable, None)

        if typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier, esprima.Syntax.ThisExpression, six.text_type):
            if typ is esprima.Syntax.ThisExpression:
                name = "this"
            else:  # if typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier):
                name = expr if typ is six.text_type else expr.name
                # module, module.exports and exports auto-spawn:
                if name in ("module", "exports") and spawn:
                    module = self.nsstack[0]
                    if "declaration" not in module:
                        module["declaration"] = module
                    if "exports" not in module["symbols"]:
                        exports = {"name": "exports",
                                   "nspath": ("exports",),
                                   "types": OrderedDict({CITDL_OBJECT: 0, CITDL_INSTANCE: 0}),
                                   "symbols": {},
                                   "attributes": [__LOCAL__],
                                   "line": 0,
                                   "start": 0,
                                   "end": 0}
                        exports["declaration"] = exports
                        module["symbols"]["exports"] = exports
                    else:
                        exports = module["symbols"]["exports"]
                    return (exports if name == "exports" else module, None)
            nspath = self.nsstack[-1]["nspath"]
            for i in range(len(nspath), -1, -1):
                if nspath[:i] in self.st:
                    ns = self.st[nspath[:i]]
                    if name in ns["symbols"]:
                        return (ns["symbols"][name], None)
                    else:
                        log.debug(
                            "_resolveObjectRef: %r not in namespace %r", name,
                            ".".join(ns["nspath"]))

        elif typ is esprima.Syntax.MemberExpression:
            obj, citdl = self._resolveObjectRef(expr.object)
            attr = expr.property.name
            if obj:
                decl = obj["declaration"]  # want the declaration
                if attr in decl["symbols"]:  # and "symbols" in decl #XXX this "and"-part necessary?
                    return (decl["symbols"][attr], None)
            elif citdl:
                # Special case: specifically refer to type object for
                # attribute access on constants, e.g.:
                #   ' '.join
                citdl = "%s.%s" % (citdl, attr)
                return (None, citdl)
                # XXX Could optimize here for common built-in attributes. E.g.,
                #    we *know* that str.join() returns a string.

        elif typ is esprima.Syntax.Literal:
            # Special case: specifically refer to type object for constants.
            citdl = "__builtins__.%s" % self.get_type(expr)
            return (None, citdl)

        elif typ in (esprima.Syntax.CallExpression, esprima.Syntax.NewExpression):
            # XXX Would need flow analysis to have an object dict for whatever
            #    a __call__ would return.
            pass

        # Fallback: return CITDL code for delayed resolution.
        log.debug("_resolveObjectRef: could not resolve %r", expr)
        scope = '.'.join(self.nsstack[-1]["nspath"])
        exprrepr = self._getCITDLExprRepr(expr)
        if exprrepr:
            if scope:
                citdl = "%s %s" % (exprrepr, scope)
            else:
                citdl = exprrepr
        else:
            citdl = None
        return (None, citdl)

    def _guessTypes(self, expr, curr_ns=None):
        # log.debug("_guessTypes(expr=%r)", expr)

        ts = []
        typ = getattr(expr, 'type', type(expr))

        if typ is esprima.Syntax.Literal:
            ts = [self.get_type(expr)]
        elif typ in (esprima.Syntax.AssignmentExpression, esprima.Syntax.AssignmentPattern):
            ts = self._guessTypes(expr.right)
        elif typ is esprima.Syntax.ArrayExpression:
            ts = [CITDL_ARRAY]
        elif typ is esprima.Syntax.ObjectExpression:
            ts = [CITDL_INSTANCE]
        elif typ is esprima.Syntax.BinaryExpression:
            op = expr.operator
            if op in ("==", "===", "!=", "!==", "<", ">", ">=", "<=", "instanceof", "in"):
                ts = [CITDL_BOOLEAN]
            elif op in ("-", "+", "*", "/", "**", "%"):
                order = [CITDL_NUMBER, CITDL_BOOLEAN, CITDL_STRING]
                possibles = self._guessTypes(expr.left) + self._guessTypes(expr.right)
                ts = []
                highest = -1
                for possible in possibles:
                    if possible not in order:
                        ts.append(possible)
                    else:
                        highest = max(highest, order.index(possible))
                if not ts and highest > -1:
                    ts = [order[highest]]
            elif op in ("|", "&", "^", "<<", ">>", ">>>"):
                ts = [CITDL_NUMBER]
            else:
                log.info("don't know how to guess types from this expr: %s, op: %s" % (typ, op))
        elif typ is esprima.Syntax.UnaryExpression:
            op = expr.operator
            if op in ("+", "-", "~", "!"):
                ts = self._guessTypes(expr.argument)
            elif op == "typeof":
                ts = [CITDL_STRING]
        elif typ in (esprima.Syntax.Identifier, esprima.JSXSyntax.JSXIdentifier, esprima.Syntax.MemberExpression, six.text_type):
            variable, citdl = self._resolveObjectRef(expr)
            if variable:
                if _isclass(variable) or _isinterface(variable) or _isfunction(variable) or _isobject(variable) or _isrequire(variable):
                    ts = [".".join(variable["nspath"])]
                else:
                    ts = list(variable["types"].keys())
            elif citdl:
                ts = [citdl]
        elif typ in (esprima.Syntax.CallExpression, esprima.Syntax.NewExpression):
            variable, citdl = self._resolveObjectRef(expr.callee)
            if variable:
                # XXX When/if we support <returns/> and if we have that
                #    info for this 'variable' we can return an actual
                #    value here.
                # Optmizing Shortcut: If the variable is a class then just
                # call its type that class definition, i.e. 'mymodule.MyClass'
                # instead of 'type(call(mymodule.MyClass))'.

                # Remove the common leading namespace elements.
                scope_parts = list(variable["nspath"])
                if curr_ns is not None:
                    for part in curr_ns["nspath"]:
                        if scope_parts and part == scope_parts[0]:
                            scope_parts.pop(0)
                        else:
                            break
                scope = ".".join(scope_parts)
                if _isinterface(variable) or _isobject(variable):
                    ts = [scope]
                else:
                    args = []
                    for arg in expr.arguments:
                        ts = self._guessTypes(arg, curr_ns)
                        args.append(ts[0].split(None, 1)[0] if ts and ts[0] and "(" not in ts[0] and ")" not in ts[0] and "," not in ts[0] else "")
                    args = "(%s)" % ",".join(args)
                    ts = [scope + args]
            elif citdl:
                # For code like this:
                #   for line in lines:
                #       line = line.rstrip()
                # this results in a type guess of "line.rstrip <funcname>".
                # That sucks. Really it should at least be line.rstrip() so
                # that runtime CITDL evaluation can try to determine that
                # rstrip() is a _function_ call rather than _class creation_,
                # which is the current resuilt. (c.f. bug 33493)
                # XXX We *could* attempt to guess based on where we know
                #     "line" to be a module import: the only way that
                #     'rstrip' could be a class rather than a function.
                # TW: I think it should always use "()" no matter if it's
                #     a class or a function. The codeintel handler can work
                #     out which one it is. This gives us the ability to then
                #     distinguish between class methods and instance methods,
                #     as class methods look like:
                #       MyClass.staticmethod()
                #     and instance methods like:
                #       MyClass().instancemethod()
                # Updated to use "()".
                # Ensure we only add the "()" to the type part, not to the
                # scope (if it exists) part, which is separated by a space. Bug:
                #   http://bugs.activestate.com/show_bug.cgi?id=71987
                # citdl in this case looks like "string.split myfunction"
                args = []
                for arg in expr.arguments:
                    ts = self._guessTypes(arg, curr_ns)
                    args.append(ts[0].split(None, 1)[0] if ts and ts[0] and "(" not in ts[0] and ")" not in ts[0] and "," not in ts[0] else "")
                args = "(%s)" % ",".join(args)
                ts = citdl.split(None, 1)
                ts[0] += args
                ts = [" ".join(ts)]
        elif typ is esprima.Syntax.FunctionExpression:
            pass

        else:
            log.info("don't know how to guess types from this expr: %s" % typ)
        return ts

    def _getExprRepr(self, node, wrap=False):
        """Return a string representation for this Python expression.

        Raises ESCILEError if can't do it.
        """
        if node is None:
            return ""

        s = None
        typ = getattr(node, 'type', type(node))
        if typ is esprima.Syntax.Identifier:
            s = node.name
        elif typ is esprima.Syntax.Literal:
            s = node.raw
        elif typ is esprima.Syntax.ThisExpression:
            s = "this"
        elif typ in (esprima.Syntax.AssignmentExpression, esprima.Syntax.AssignmentPattern):
            s = "%s = %s" % (self._getExprRepr(node.left, True), self._getExprRepr(node.right, True))
            if wrap:
                s = "(%s)" % s
        elif typ in (esprima.Syntax.ArrayExpression, esprima.Syntax.ArrayPattern):
            items = [self._getExprRepr(c, True) for c in node.elements]
            s = "[ %s ]" % ", ".join(items) if items else "[]"
        elif typ in (esprima.Syntax.ObjectExpression, esprima.Syntax.ObjectPattern):
            items = ["%s: %s" % (self._getExprRepr(prop.key, True), self._getExprRepr(prop.value, True)) for prop in node.properties if prop.type is esprima.Syntax.Property and not prop.computed]
            s = "{ %s }" % ", ".join(items) if items else "{}"
        elif typ is esprima.Syntax.CallExpression:
            s = "%s(%s)" % (self._getExprRepr(node.callee, True), ", ".join(self._getExprRepr(arg, True) for arg in node.arguments))
        elif typ is esprima.Syntax.NewExpression:
            s = "new %s(%s)" % (self._getExprRepr(node.callee, True), ", ".join(self._getExprRepr(arg, True) for arg in node.arguments))
        elif typ is esprima.Syntax.FunctionExpression:
            s = "%s(%s)" % (self._getExprRepr(node.id, True), ", ".join(self._getExprRepr(param, True) for param in node.params))
        elif typ is esprima.Syntax.MemberExpression:
            s = "%s.%s" % (self._getExprRepr(node.object, True), node.property.name)
        elif typ is esprima.Syntax.UnaryExpression:
            op = node.operator
            sp = " " if op in ("delete", "void", "typeof") else ""
            s = "%s%s%s" % (op, sp, self._getExprRepr(node.argument, True))
        elif typ is esprima.Syntax.LogicalExpression:
            op = node.operator
            s = "%s %s %s" % (self._getExprRepr(node.left, True), op, self._getExprRepr(node.right, True))
            if wrap:
                s = "(%s)" % s
        elif typ is esprima.Syntax.TemplateLiteral:
            template = ""
            for i, quasi in enumerate(node.quasis):
                template += quasi.value.raw
                if not quasi.tail:
                    template += "${ %s }" % self._getExprRepr(node.expressions[i])
            s = "`%s`" % template
        elif typ is esprima.Syntax.ConditionalExpression:
            s = "%s ? %s : %s" % (self._getExprRepr(node.test, True), self._getExprRepr(node.consequent, True), self._getExprRepr(node.alternate, True))
            if wrap:
                s = "(%s)" % s
        elif typ is esprima.Syntax.SequenceExpression:
            s = ", ".join(self._getExprRepr(exp, True) for exp in node.expressions)
            if wrap:
                s = "(%s)" % s
        elif typ is esprima.Syntax.BinaryExpression:
            op = node.operator
            s = "%s %s %s" % (self._getExprRepr(node.left, True), op, self._getExprRepr(node.right, True))
            if wrap:
                s = "(%s)" % s
        elif typ is esprima.Syntax.UpdateExpression:
            op = node.operator
            arg = self._getExprRepr(node.argument, True)
            s = "%s%s" % (op, arg) if node.prefix else (arg, op)
        elif typ in (esprima.Syntax.SpreadElement, esprima.Syntax.RestElement):
            s = "...%s" % self._getExprRepr(node.argument, True)
        elif typ is esprima.Syntax.ArrowFunctionExpression:
            s = "%s=> {...}" % ("async " if node.async else "")
            if wrap:
                s = "(%s)" % s
        elif typ is esprima.JSXSyntax.JSXElement:
            element = node.openingElement
            if element.selfClosing:
                s = "<%s />" % element.name
            else:
                s = "<%s>...</%s>" % (element.name, element.name)

        if s is None:
            raise ESCILEError("don't know how to get string repr of expression: %r" % node)
        return s

    def _getCITDLExprRepr(self, node, _level=0):
        """Return a string repr for this expression that CITDL processing
        can handle.

        CITDL is no panacea -- it is meant to provide simple delayed type
        determination. As a result, many complicated expressions cannot
        be handled. If the expression is not with CITDL's scope, then None
        is returned.
        """
        s = None
        typ = getattr(node, 'type', type(node))
        if typ is six.text_type:
            s = node
        elif typ is esprima.Syntax.Identifier:
            s = node.name
        elif typ is esprima.Syntax.Literal:
            s = self.get_repr(node)
        elif typ is esprima.Syntax.ArrayExpression:
            s = CITDL_ARRAY
        elif typ is esprima.Syntax.ObjectExpression:
            s = CITDL_INSTANCE
        elif typ is esprima.Syntax.MemberExpression:
            exprRepr = self._getCITDLExprRepr(node.object, _level + 1)
            if exprRepr is None:
                pass
            else:
                propRepr = self._getCITDLExprRepr(node.property)
                if node.computed:
                    # E.g.:  bar[1]
                    s = "%s[%s]" % (exprRepr, propRepr)
                else:
                    # E.g.:  bar.foo
                    s = "%s.%s" % (exprRepr, propRepr)
        elif typ in (esprima.Syntax.CallExpression, esprima.Syntax.NewExpression):
            # Only allow CallFunc at the top-level. I.e. this:
            #   spam.ham.eggs()
            # is in scope, but this:
            #   spam.ham().eggs
            # is not.
            if _level != 0:
                pass
            else:
                args = []
                for arg in node.arguments:
                    ts = self._guessTypes(arg)
                    args.append(ts[0].split(None, 1)[0] if ts and ts[0] and "(" not in ts[0] and ")" not in ts[0] and "," not in ts[0] else "")
                args = "(%s)" % ",".join(args)
                s = self._getCITDLExprRepr(node.callee, _level + 1)
                if s is not None:
                    s += args
        return s


def _quietCompilerParse(content, **kwargs):
    oldstderr = sys.stderr
    # sys.stderr = StringIO()
    try:
        return esprima.parse(content, **kwargs)
    finally:
        sys.stderr = oldstderr


def _getAST(convertor, content, f, **kwargs):
    """Return an AST for the given ECMAScript content.

    If cannot, raise an error describing the problem.
    """

    errlineno = None  # line number of an Error
    ast_ = None
    try:
        if convertor:
            content_orig = content
            content = convertor(content_orig, f)
            try:
                ast_ = _quietCompilerParse(content, **kwargs)
            except Exception:
                content = convertor(content_orig, f, refactor=True)
                if not content:
                    raise
                ast_ = _quietCompilerParse(content, **kwargs)
        else:
            ast_ = _quietCompilerParse(content, **kwargs)
    except esprima.Error as ex:
        errlineno = ex.lineNumber
        log.debug("compiler parse #1: syntax error on line %d: %s", errlineno, ex)

    if errlineno is not None:
        # There was a syntax error at this line: try to recover by effectively
        # nulling out the offending line or the previous.
        lines = content.splitlines(True) + [""]
        offender = lines[errlineno - 1]
        log.info("syntax error on line %d: %r: trying to recover", errlineno, offender)
        lines[errlineno - 1] = ";" + ("\n" if offender.endswith("\n") else "")
        newContent = "".join(lines)

        errlineno2 = None
        try:
            ast_ = _quietCompilerParse(newContent, **kwargs)
        except esprima.Error as ex:
            errlineno2 = ex.lineNumber
            log.debug("compiler parse #2: syntax error on line %d: %s", errlineno, ex)

        if ast_ is not None:
            pass
        elif errlineno2 == errlineno:
            if errlineno > 1:
                lines[errlineno - 1] = offender
                lines[errlineno - 2] = ";\n"
                newContent = "".join(lines)

                try:
                    ast_ = _quietCompilerParse(newContent, **kwargs)
                except esprima.Error as ex:
                    log.debug("compiler parse #3: syntax error on line %d: %s", errlineno, ex)
                if ast_ is not None:
                    pass
                else:
                    raise ValueError("cannot recover from syntax error: line %d"
                                    % errlineno)
            else:
                raise ValueError("cannot recover from syntax error: line %d"
                                % errlineno)
        else:
            raise ValueError("cannot recover from multiple syntax errors: "
                             "line %d and then %d" % (errlineno, errlineno2))

    if ast_ is None:
        raise ValueError("could not generate AST")

    return ast_


# ---- public module interface

def scan_cix(content, filename, md5sum=None, mtime=None, lang="ECMAScript", traceback=False):
    """Scan the given ECMAScript content and return Code Intelligence data
    conforming the the Code Intelligence XML format.

        "content" is the ECMAScript content to scan. This should be an
            encoded string: must be a string for `md5` and
            `esprima.parse` -- see bug 73461.
        "filename" is the source of the ECMAScript content (used in the
            generated output).
        "md5sum" (optional) if the MD5 hexdigest has already been calculated
            for the content, it can be passed in here. Otherwise this
            is calculated.
        "mtime" (optional) is a modified time for the file (in seconds since
            the "epoch"). If it is not specified the _current_ time is used.
            Note that the default is not to stat() the file and use that
            because the given content might not reflect the saved file state.
        "lang" (optional) is the language of the given file content.
            Typically this is "ECMAScript" (i.e. a pure ECMAScript file), but it
            may also be "DjangoHTML" or similar for ECMAScript embedded in
            other documents.
        XXX Add an optional 'eoltype' so that it need not be
            re-calculated if already known.

    This can raise one of esprima.Error or ESCILEError
    if there was an error processing. Currently this implementation uses the
    ECMAScript 'compiler' package for processing, therefore the given ECMAScript
    content must be syntactically correct.
    """
    codeintel = scan_et(content, filename, md5sum, mtime, lang, traceback)
    tree = ET.ElementTree(codeintel)

    stream = BytesIO()

    # this is against the W3C spec, but ElementTree wants it lowercase
    tree.write(stream, "utf-8")

    cix = stream.getvalue()

    return cix


def scan_et(content, filename, md5sum=None, mtime=None, lang="ECMAScript", traceback=False):
    """Scan the given ECMAScript content and return Code Intelligence data
    conforming the the Code Intelligence XML format.

        "content" is the ECMAScript content to scan. This should be an
            encoded string: must be a string for `md5` and
            `esprima.parse` -- see bug 73461.
        "filename" is the source of the ECMAScript content (used in the
            generated output).
        "md5sum" (optional) if the MD5 hexdigest has already been calculated
            for the content, it can be passed in here. Otherwise this
            is calculated.
        "mtime" (optional) is a modified time for the file (in seconds since
            the "epoch"). If it is not specified the _current_ time is used.
            Note that the default is not to stat() the file and use that
            because the given content might not reflect the saved file state.
        "lang" (optional) is the language of the given file content.
            Typically this is "ECMAScript" (i.e. a pure ECMAScript file), but it
            may also be "DjangoHTML" or similar for ECMAScript embedded in
            other documents.
        XXX Add an optional 'eoltype' so that it need not be
            re-calculated if already known.

    This can raise one of esprima.Error or ESCILEError
    if there was an error processing. Currently this implementation uses the
    ECMAScript 'compiler' package for processing, therefore the given ECMAScript
    content must be syntactically correct.
    """
    global _gStartTime
    if _gClockIt:
        _gStartTime = _gClock()

    log.info("scan '%s'", filename)
    if md5sum is None:
        md5sum = md5(content.encode('utf-8')).hexdigest()
    if mtime is None:
        mtime = int(time.time())

    # parsing could fail on funky *whitespace* at the end of the file.
    content = content.rstrip()

    # The 'path' attribute must use normalized dir separators.
    if sys.platform.startswith("win"):
        path = filename.replace('\\', '/')
    else:
        path = filename

    options = {
        'esnext': True,
        'tolerant': True,
        'sourceType': 'module',
        'attachComment': True,
        'loc': True,
        'range': True,
    }

    moduleName = os.path.splitext(os.path.basename(filename))[0]
    parser = AST2CIXVisitor(moduleName, content=content, filename=filename, lang=lang)
    try:
        parser.parse(filename=filename.encode('utf-8'), options=options, delegate=parser)
        if _gClockIt:
            sys.stdout.write(" (parse:%.3fs)" % (_gClock() - _gStartTime))
        parser.walk()
    except Exception as ex:
        if traceback or log.isEnabledFor(logging.DEBUG):
            print()
            import traceback
            traceback.print_exception(*sys.exc_info())
        file = ET.Element('file', _et_attrs(dict(lang=lang,
                                                 path=path,
                                                 error=str(ex))))
    else:
        if _gClockIt:
            sys.stdout.write(" (walk:%.3fs)" % (_gClock() - _gStartTime))

        if log.isEnabledFor(logging.INFO):
            # Dump a repr of the gathering info for debugging
            # - We only have to dump the module namespace because
            #   everything else should be linked from it.
            for nspath, namespace in list(parser.st.items()):
                if len(nspath) == 0:  # this is the module namespace
                    pprint.pprint(namespace)

        file = parser.getCIX(path)
        if _gClockIt:
            sys.stdout.write(" (getCIX:%.3fs)" % (_gClock() - _gStartTime))

    codeintel = ET.Element('codeintel', _et_attrs(dict(version="2.0")))
    codeintel.append(file)
    return codeintel


# ---- mainline
def main(argv):
    import time
    logging.basicConfig()

    # Parse options.
    try:
        opts, args = getopt.getopt(argv[1:], "Vvhf:cL:",
            ["version", "verbose", "help", "filename=", "md5=", "mtime=",
             "clock", "language=", "traceback"])
    except getopt.GetoptError as ex:
        log.error(str(ex))
        log.error("Try `ecmacile --help'.")
        return 1
    numVerboses = 0
    stdinFilename = None
    md5sum = None
    mtime = None
    lang = "ECMAScript"
    traceback = False
    global _gClockIt
    for opt, optarg in opts:
        if opt in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return
        elif opt in ("-V", "--version"):
            ver = '.'.join([str(part) for part in _version_])
            print("ecmacile %s" % ver)
            return
        elif opt in ("-v", "--verbose"):
            numVerboses += 1
            if numVerboses == 1:
                log.setLevel(logging.INFO)
            else:
                log.setLevel(logging.DEBUG)
        elif opt in ("-f", "--filename"):
            stdinFilename = optarg
        elif opt == "--traceback":
            traceback = True
        elif opt in ("-L", "--language"):
            lang = optarg
        elif opt in ("--md5",):
            md5sum = optarg
        elif opt in ("--mtime",):
            mtime = optarg
        elif opt in ("-c", "--clock"):
            _gClockIt = 1
            global _gClock
            if sys.platform.startswith("win"):
                _gClock = time.clock
            else:
                _gClock = time.time

    if len(args) == 0:
        contentOnStdin = 1
        filenames = [stdinFilename or "<stdin>"]
    else:
        contentOnStdin = 0
        paths = []
        for arg in args:
            paths += glob.glob(arg)
        filenames = []
        for path in paths:
            if os.path.isfile(path):
                filenames.append(path)
            elif os.path.isdir(path):
                esfiles = [os.path.join(path, n) for n in os.listdir(path)
                           if os.path.splitext(n)[1] in ('.js', '.jsx', '.es')]
                esfiles = [f for f in esfiles if os.path.isfile(f)]
                filenames += esfiles

    try:
        for filename in filenames:
            if contentOnStdin:
                log.debug("reading content from stdin")
                content = sys.stdin.read()
                log.debug("finished reading content from stdin")
                if mtime is None:
                    mtime = int(time.time())
            else:
                if mtime is None:
                    mtime = int(os.stat(filename)[stat.ST_MTIME])
                fin = open(filename, 'r')
                try:
                    content = fin.read()
                finally:
                    fin.close()

            if _gClockIt:
                sys.stdout.write("scanning '%s'..." % filename)
                global _gStartTime
                _gStartTime = _gClock()
            data = scan_cix(content, filename, md5sum=md5sum, mtime=mtime,
                            lang=lang, traceback=traceback)
            if _gClockIt:
                sys.stdout.write(" %.3fs\n" % (_gClock() - _gStartTime))
            elif data:
                sys.stdout.write(data)
    except ESCILEError as ex:
        log.error(str(ex))
        if log.isEnabledFor(logging.DEBUG):
            print()
            import traceback
            traceback.print_exception(*sys.exc_info())
        return 1
    except KeyboardInterrupt:
        log.debug("user abort")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
