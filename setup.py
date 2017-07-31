#!/usr/bin/env python
import os
import sys
import glob

from setuptools import setup, Extension

import codeintel
VERSION = codeintel.__version__

########################################################################
# SilverCity (it's in PyPI, but unpatched, this one is patched)

SilverCity_src_files = []
SilverCity_extra_link_args = []
SilverCity_extra_objects = []
SilverCity_define_macros = []
SilverCity_libraries = []

SilverCity_src = 'silvercity/PySilverCity/Src'

# Add Python extension source files
SilverCity_src_files.extend([
    os.path.join(SilverCity_src, file) for file in [
        'PyLexerModule.cxx',
        'PyPropSet.cxx',
        'PySilverCity.cxx',
        'PyWordList.cxx',
    ]
])

SilverCity_libsrc = 'silvercity/Lib/Src'

# Add library source files
SilverCity_src_files.extend([
    os.path.join(SilverCity_libsrc, file) for file in [
        'BufferAccessor.cxx',
        'LexState.cxx',
        'LineVector.cxx',
        'SC_PropSet.cxx',
        'Platform.cxx',
    ]
])

# Add Scintilla support files
scintilla_include = 'scintilla/include'
scintilla_src = 'scintilla/src'
scintilla_lexlib = 'scintilla/lexlib'
scintilla_lexers = 'scintilla/lexers'

SilverCity_src_files.extend([
    os.path.join(scintilla_src, file) for file in [
        'KeyMap.cxx',
        'Catalogue.cxx',
        'UniConversion.cxx',
    ]
])

SilverCity_src_files.extend([
    os.path.join(scintilla_lexlib, file) for file in [
        'WordList.cxx',
        'PropSetSimple.cxx',
        'Accessor.cxx',
        'CharacterCategory.cxx',
        'CharacterSet.cxx',
        'LexerBase.cxx',
        'LexerNoExceptions.cxx',
        'LexerSimple.cxx',
        'LexerModule.cxx',
        'StyleContext.cxx',
    ]
])

# Add Scintilla lexers
for file in os.listdir(scintilla_lexers):
    file_ = os.path.join(scintilla_lexers, file)
    if os.path.basename(file_).startswith('Lex') and \
       os.path.splitext(file_)[1] == '.cxx':
        SilverCity_src_files.append(file_)

# Add pcre source files
pcre_src = 'pcre'
pcre_h_name = os.path.join(pcre_src, 'pcre.h')
if not os.path.exists(pcre_h_name):
    with open(os.path.join(pcre_src, 'pcre.in')) as pcre_in:
        with open(pcre_h_name, 'w') as pcre_h:
            pcre_h.write(pcre_in.read())
pcre_dftables_c_name = os.path.join(pcre_src, 'pcre_dftables.c')
if not os.path.exists(pcre_dftables_c_name):
    # pcre_dftables.c is originally generated using the
    # command ``dftables pcre_dftables.c``
    with open('pcre_dftables.c.in') as pcre_dftables_c_in:
        with open(pcre_dftables_c_name, 'w') as pcre_dftables_c:
            pcre_dftables_c.write(pcre_dftables_c_in.read())
config_h_name = os.path.join(pcre_src, 'config.h')
if not os.path.exists(config_h_name):
    with open(config_h_name, 'w') as config_h:
        config_h.write('/* Fake config.h */')
SilverCity_define_macros.extend([
    ('PCRE_STATIC', None),
    ('HAVE_STRERROR', None),
    ('HAVE_MEMMOVE', None),
    ('HAVE_BCOPY', None),
    ('NEWLINE', "'\\n'"),
    ('LINK_SIZE', 2),
    ('MATCH_LIMIT', 10000000),
    ('POSIX_MALLOC_THRESHOLD', 10),
    ('EXPORT', ""),
])
SilverCity_src_files.extend([
    os.path.join(pcre_src, file) for file in [
        'pcre_compile.c',
        'pcre_config.c',
        'pcre_dfa_exec.c',
        'pcre_exec.c',
        'pcre_fullinfo.c',
        'pcre_get.c',
        'pcre_globals.c',
        'pcre_info.c',
        'pcre_maketables.c',
        'pcre_ord2utf8.c',
        'pcre_printint.c',
        'pcre_refcount.c',
        'pcre_study.c',
        'pcre_tables.c',
        'pcre_try_flipped.c',
        'pcre_ucp_findchar.c',
        'pcre_valid_utf8.c',
        'pcre_version.c',
        'pcre_xclass.c',
        'pcre_dftables.c',
    ]
])


SilverCity_include_dirs = [
    scintilla_src,
    scintilla_lexlib,
    scintilla_lexers,
    scintilla_include,
    SilverCity_src,
    SilverCity_libsrc,
    pcre_src,
]


SilverCity_ext = Extension(
    'codeintel.SilverCity._SilverCity',
    SilverCity_src_files,
    include_dirs=SilverCity_include_dirs,
    extra_compile_args=[],
    define_macros=SilverCity_define_macros,
    extra_link_args=SilverCity_extra_link_args,
    extra_objects=SilverCity_extra_objects,
    libraries=SilverCity_libraries,
)

########################################################################
# sgmlop (it's in PyPI but as an external)

sgmlop_include_dirs = [
    'codeintel.sgmlop',
]
sgmlop_ext = Extension(
    'codeintel.sgmlop', [
        'sgmlop/sgmlop.c',
    ],
    include_dirs=sgmlop_include_dirs,
)

########################################################################
# cElementTree (it's in PyPI, but unpatched, this one is patched)
# ciElementTree (it's not in PyPI)

xElementTree_src_files = [
    'xElementTree/src/expat/xmlparse.c',
    'xElementTree/src/expat/xmlrole.c',
    'xElementTree/src/expat/xmltok.c',
]

xElementTree_include_dirs = [
    'xElementTree/src',
    'xElementTree/src/expat',
]

xElementTree_define_macros = [
    ('XML_STATIC', None),
    ('HAVE_MEMMOVE', None),
]

cElementTree_ext = Extension(
    'codeintel._cElementTree', ['xElementTree/src/py%s_cElementTree.c' % sys.version_info[0]] + xElementTree_src_files,
    include_dirs=xElementTree_include_dirs,
    define_macros=xElementTree_define_macros,
)

ciElementTree_ext = Extension(
    'codeintel._ciElementTree', ['xElementTree/src/py%s_ciElementTree.c' % sys.version_info[0]] + xElementTree_src_files,
    include_dirs=xElementTree_include_dirs,
    define_macros=xElementTree_define_macros,
)


########################################################################
# codeintel

install_requires = [
    '3to2',
    'applib',
    'chardet',
    'cmdln',
    'inflector',
    'six',
    'zope.cachedescriptors',
]

if sys.version_info[0] == 2:
    install_requires.append('clang')
    if sys.platform != 'win32':
        # subprocess32 is not available for windows
        install_requires.append('subprocess32')
else:
    install_requires.append('libclang-py3')


def package_files(directory):
    paths = []
    for directory in glob.glob(directory):
        if os.path.isdir(directory):
            if os.path.isdir(directory):
                for path, directories, filenames in os.walk(directory):
                    for filename in filenames:
                        paths.append(os.path.join('..', path, filename))
        else:
            paths.append(os.path.join('..', directory))
    return paths


setup(
    name="CodeIntel",
    version=VERSION,
    description="Komodo Edit CodeIntel",
    long_description="""\
Code intelligence ported from Open Komodo Editor. Supports all the languages
Komodo Editor supports for Code Intelligence (CIX, CodeIntel2):

Go, JavaScript, Mason, XBL, XUL, RHTML, SCSS, Python, HTML, Ruby, Python3, XML,
Sass, XSLT, Django, HTML5, Perl, CSS, Twig, Less, Smarty, Node.js, Tcl,
TemplateToolkit, PHP, C/C++, Objective-C.""",
    url="https://github.com/Kronuz/CodeIntel",
    author="Komodo Edit Team",
    author_email="german.mb@gmail.com",
    maintainer="German Mendez Bravo (Kronuz)",
    maintainer_email="german.mb@gmail.com",
    license="MPL 1.1",
    classifiers=[
        # License should match "license" above.
        "License :: OSI Approved :: Mozilla Public License 1.1 (MPL 1.1)",
        # How mature is this project? Common values are
        #   3 - Alpha
        #   4 - Beta
        #   5 - Production/Stable
        "Development Status :: 4 - Beta",
        # Specify the Python versions you support here. In particular, ensure
        # that you indicate whether you support Python 2, Python 3 or both.
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
    keywords='codeintel intellisense autocomplete ide languages python go javascript mason xbl xul rhtml scss python html ruby python3 xml sass xslt django html5 perl css twig less smarty node tcl templatetoolkit php',
    install_requires=install_requires,
    ext_modules=[
        SilverCity_ext,
        cElementTree_ext,
        ciElementTree_ext,
        sgmlop_ext,
    ],
    entry_points={
        'console_scripts': [
            'codeintel = codeintel.__main__:main',
        ],
    },
    packages=[
        'codeintel',
        'codeintel.codeintel2',
        'codeintel.codeintel2.oop',
        'codeintel.codeintel2.database',
        'codeintel.elementtree',
        'codeintel.SilverCity',
        'codeintel.test2',
    ],
    package_data={
        '': (
            package_files('codeintel/codeintel2/lexers/*.lexres') +
            package_files('codeintel/codeintel2/catalogs/*.cix') +
            package_files('codeintel/codeintel2/stdlibs/*.cix') +
            package_files('codeintel/codeintel2/golib') +
            package_files('codeintel/codeintel2/lib_srcs') +
            package_files('codeintel/test2/scan_inputs') +
            package_files('codeintel/test2/scan_outputs') +
            package_files('codeintel/test2/bits') +
            package_files('codeintel/catalogs')
        )
    },
)
