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

"""Test some ECMAScript-specific codeintel handling."""

from __future__ import absolute_import
import os
from os.path import join
import unittest
import logging

# from codeintel2.common import *
from codeintel2.util import dedent, unmark_text

from citestsupport import CodeIntelTestCase, writefile


log = logging.getLogger("test")


class DefnTestCase(CodeIntelTestCase):
    lang = "ECMAScript"
    test_dir = join(os.getcwd(), "tmp")

    def test_simple(self):
        test_dir = join(self.test_dir, "test_defn")
        foo_es_content, foo_es_positions = unmark_text(dedent("""\
            import Bar from 'bar';
            Bar.b<1>ar
        """))

        manifest = [
            ("bar.es", dedent("""
                export default { bar: 42 };
             """)),
            ("foo.es", foo_es_content),
        ]
        for file, content in manifest:
            path = join(test_dir, file)
            writefile(path, content)

        buf = self.mgr.buf_from_path(join(test_dir, "foo.es"), lang=self.lang)

        self.assertDefnMatches2(buf, foo_es_positions[1],
            ilk="variable", name="bar", line=2, citdl="Number",
            path=join(test_dir, "bar.es"), )


# ---- mainline

if __name__ == "__main__":
    unittest.main()
