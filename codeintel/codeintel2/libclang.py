from __future__ import print_function, absolute_import

import os
import copy
import shlex
import threading

from clang import cindex
# Monkeypatch cindex to avoid AttributeError when __del__ is called during program shutdown (Python 2 only):
cindex._CXString.__del__ = lambda self: cindex and cindex.conf.lib.clang_disposeString(self)
cindex.Diagnostic.__del__ = lambda self: cindex and cindex.conf.lib.clang_disposeDiagnostic(self)
cindex.TokenGroup.__del__ = lambda self: cindex and cindex.conf.lib.clang_disposeTokens(self._tu, self._memory, self._count)
cindex.CodeCompletionResults.__del__ = lambda self: cindex and cindex.conf.lib.clang_disposeCodeCompleteResults(self)
cindex.Index.__del__ = lambda self: cindex and cindex.conf.lib.clang_disposeIndex(self)
cindex.TranslationUnit.__del__ = lambda self: cindex and cindex.conf.lib.clang_disposeTranslationUnit(self)
cindex.CompileCommands.__del__ = lambda self: cindex and cindex.conf.lib.clang_CompileCommands_dispose(self.ccmds)
cindex.CompilationDatabase.__del__ = lambda self: cindex and cindex.conf.lib.clang_CompilationDatabase_dispose(self)


import logging
logger = logging.getLogger(__name__)


def encode(value):
    import sys
    if sys.version_info[0] == 3:
        return value

    try:
        return value.encode('utf-8')
    except AttributeError:
        return value


def decode(value):
    import sys
    if sys.version_info[0] == 2:
        return value

    try:
        return value.decode('utf-8')
    except AttributeError:
        return value


def getAbbr(strings):
    for chunks in strings:
        if chunks.isKindTypedText():
            return decode(chunks.spelling)
    return ''


kinds = {
    1: 'type',                  # CXCursor_UnexposedDecl (A declaration whose specific kind is not
                                # exposed via this interface)
    2: 'type',                  # CXCursor_StructDecl (A C or C++ struct)
    3: 'type',                  # CXCursor_UnionDecl (A C or C++ union)
    4: 'type',                  # CXCursor_ClassDecl (A C++ class)
    5: 'type',                  # CXCursor_EnumDecl (An enumeration)
    6: 'member',                # CXCursor_FieldDecl (A field (in C) or non-static data member
                                # (in C++) in a struct, union, or C++ class)
    7: 'enum',                  # CXCursor_EnumConstantDecl (An enumerator constant)
    8: 'function',              # CXCursor_FunctionDecl (A function)
    9: 'variable',              # CXCursor_VarDecl (A variable)
    10: 'argument',             # CXCursor_ParmDecl (A function or method parameter)
    20: 'type',                 # CXCursor_TypedefDecl (A typedef)
    21: 'function',             # CXCursor_CXXMethod (A C++ class method)
    22: 'namespace',            # CXCursor_Namespace (A C++ namespace)
    24: 'function',             # CXCursor_Constructor (A C++ constructor)
    25: 'function',             # CXCursor_Destructor (A C++ destructor)
    27: 'argument',             # CXCursor_TemplateTypeParameter (A C++ template type parameter)
    28: 'argument',             # CXCursor_NonTypeTemplateParameter (A C++ non-type template
                                # parameter)
    29: 'argument',             # CXCursor_TemplateTemplateParameter (A C++ template template
                                # parameter)
    30: 'function',             # CXCursor_FunctionTemplate (A C++ function template)
    31: 'template',             # CXCursor_ClassTemplate (A C++ class template)
    33: 'namespace',            # CXCursor_NamespaceAlias (A C++ namespace alias declaration)
    36: 'type',                 # CXCursor_TypeAliasDecl (A C++ alias declaration)
    72: 'unimplemented',        # CXCursor_NotImplemented
    501: 'macro',               # CXCursor_MacroDefinition
    601: 'type alias',          # CXCursor_TypeAliasTemplateDecl (Template alias declaration).
    700: 'overload candidate',  # CXCursor_OverloadCandidate A code completion overload candidate.
}


def formatResult(result):
    completion = {}

    abbr = ''
    word = ''
    info = ''
    returnValue = None

    place_markers_for_optional_args = False

    def roll_out_optional(chunks):
        result = []
        word = ''
        for chunk in chunks:
            if chunk.isKindInformative() or chunk.isKindResultType() or chunk.isKindTypedText():
                continue

            word += decode(chunk.spelling)
            if chunk.isKindOptional():
                result += roll_out_optional(chunk.string)

        return [word] + result

    for chunk in result.string:

        if chunk.isKindInformative():
            continue

        if chunk.isKindResultType():
            returnValue = chunk
            continue

        chunk_spelling = decode(chunk.spelling)

        if chunk.isKindTypedText():
            abbr = chunk_spelling

        if chunk.isKindOptional():
            for optional_arg in roll_out_optional(chunk.string):
                if place_markers_for_optional_args:
                    word += '$%s' % optional_arg
                info += optional_arg + '=?'

        if chunk.isKindPlaceHolder():
            word += '$%s' % chunk_spelling
        else:
            word += chunk_spelling

        info += chunk_spelling

    menu = info

    if returnValue:
        menu = decode(returnValue.spelling) + " " + menu

    completion['word'] = word
    completion['abbr'] = abbr
    completion['menu'] = menu
    completion['info'] = info
    completion['dup'] = 1

    # Replace the number that represents a specific kind with a better
    # textual representation.
    completion['kind'] = kinds.get(result.cursorKind, 'unknown')

    return completion


class ClangCompleter(object):
    def __init__(self, library_path=None, compilation_database_path=None, log=None):
        self.log = log or logger
        self.libclangLock = threading.Lock()

        # Config
        if not library_path:
            library_path = self._findLibrary()

        if library_path:
            if os.path.isdir(library_path):
                cindex.Config.set_library_path(library_path)
            else:
                cindex.Config.set_library_file(library_path)

        cindex.Config.set_compatibility_check(False)

        # Index
        try:
            self.index = cindex.Index.create()
        except Exception as e:
            if library_path:
                suggestion = "Are you sure '%s' contains libclang?" % library_path
            else:
                suggestion = "Consider setting library_path."
            self.log.error(suggestion)
            return

        # Builtin Header Path
        self.builtin_header_path = None
        if not self._canFindBuiltinHeaders(self.index):
            self.builtin_header_path = self._getBuiltinHeaderPath(library_path)

            if not self.builtin_header_path:
                self.log.warn("libclang can not find the builtin includes. This will cause slow code completion. Please report the problem.")

        self.translationUnits = {}
        if compilation_database_path:
            self.compilation_database = cindex.CompilationDatabase.fromDirectory(compilation_database_path)
        else:
            self.compilation_database = None

        self._last_query = {'args': [], 'cwd': None}

    def _findLibrary(self):
        from ctypes.util import find_library
        from ctypes import cdll
        import platform

        library = find_library('clang')
        if library:
            return library

        name = platform.system()

        if name == 'Darwin':
            files = ['libclang.dylib']
        elif name == 'Windows':
            files = ['libclang_x64.dll', 'libclang.dll', 'clang.dll']
        else:
            files = ['libclang.so']

        knownPaths = [
            '/usr/lib64',                                                  # x86_64 (openSUSE, Fedora)
            '/usr/lib',
            '/usr/local/lib64',
            '/usr/local/lib',
            '/usr/local/opt/llvm/lib/',
            '/Library/Developer/CommandLineTools/usr/lib',                 # macOS
            '/Developer/usr/clang-ide/lib',
            '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib',
            'C:/Program Files/LLVM/lib',                                   # Windows
            'C:/Program Files/LLVM/bin',
            'C:/Program Files (x86)/LLVM/lib',
            'C:/Program Files (x86)/LLVM/bin',
            'C:/msys64/mingw64/lib',
            'C:/msys64/mingw64/bin',
            'C:/msys32/mingw32/lib',
            'C:/msys32/mingw32/bin',
            'C:/msys/mingw32/lib',
            'C:/msys/mingw32/bin',
            'C:/msys/mingw64/lib',
            'C:/msys/mingw64/bin',
            'C:/MinGW/lib',
            'C:/MinGW/bin',
            'C:/LLVM/lib',
            'C:/LLVM/bin',
        ]

        for path in knownPaths:
            for file in files:
                library = os.path.join(path, file)
                if os.path.isfile(library):
                    try:
                        cdll.LoadLibrary(library)
                        return library
                    except Exception:
                        pass

    # Check if libclang is able to find the builtin include files.
    #
    # libclang sometimes fails to correctly locate its builtin include files. This
    # happens especially if libclang is not installed at a standard location. This
    # function checks if the builtin includes are available.
    def _canFindBuiltinHeaders(self, index, args=[]):
        flags = 0
        currentFile = ('test.c', '#include "stddef.h"')
        try:
            tu = index.parse('test.c', args, [currentFile], flags)
        except cindex.TranslationUnitLoadError:
            return 0
        return len(tu.diagnostics) == 0

    # Derive path to clang builtin headers.
    #
    # This function tries to derive a path to clang's builtin header files. We are
    # just guessing, but the guess is very educated. In fact, we should be right
    # for all manual installations (the ones where the builtin header path problem
    # is very common) as well as a set of very common distributions.
    def _getBuiltinHeaderPath(self, library_path):
        if os.path.isfile(library_path):
            library_path = os.path.dirname(library_path)

        knownPaths = [
            os.path.join(os.path.dirname(library_path), 'lib', 'clang'),   # default value
            os.path.join(os.path.dirname(library_path), 'clang'),          # gentoo
            os.path.join(library_path, 'clang'),                           # opensuse
            os.path.join(library_path),                                    # Google
            '/usr/lib64/clang',                                            # x86_64 (openSUSE, Fedora)
            '/usr/lib/clang'
        ]

        for path in knownPaths:
            try:
                subdirs = [f for f in os.listdir(path) if os.path.isdir(os.path.join(path, f))]
                subdirs = sorted(subdirs) or ['.']
                path = os.path.join(path, subdirs[-1], 'include')
                if self._canFindBuiltinHeaders(self.index, ['-I{}'.format(path)]):
                    return path
            except:
                pass

        return None

    # Get the compilation parameters from the compilation database for source
    # 'fileName'. The parameters are returned as map with the following keys :
    #
    #   'args' : compiler arguments.
    #            Compilation database returns the complete command line. We need
    #            to filter at least the compiler invocation, the '-o' + output
    #            file, the input file and the '-c' arguments. We alter -I paths
    #            to make them absolute, so that we can launch clang from wherever
    #            we are.
    #            Note : we behave differently from cc_args.py which only keeps
    #            '-I', '-D' and '-include' options.
    #
    #    'cwd' : the compiler working directory
    #
    # The last found args and cwd are remembered and reused whenever a file is
    # not found in the compilation database. For example, this is the case for
    # all headers. This achieve very good results in practice.
    def _getCompilationDBParams(self, fileName):
        if self.compilation_database:
            cmds = self.compilation_database.getCompileCommands(fileName)
            if cmds is not None:
                cwd = decode(cmds[0].directory)
                args = []
                skip_next = 1  # Skip compiler invocation
                for arg in (decode(x) for x in cmds[0].arguments):
                    if skip_next:
                        skip_next = 0
                        continue
                    if arg == '-c':
                        continue
                    if arg == fileName or os.path.realpath(os.path.join(cwd, arg)) == fileName:
                        continue
                    if arg == '-o':
                        skip_next = 1
                        continue
                    if arg.startswith('-I'):
                        includePath = arg[2:]
                        if not os.path.isabs(includePath):
                            includePath = os.path.normpath(os.path.join(cwd, includePath))
                        args.append('-I' + includePath)
                        continue
                    args.append(arg)
                self._last_query = {'args': args, 'cwd': cwd}

        # Do not directly return _last_query, but make sure we return a deep copy.
        # Otherwise users of that result may accidently change it and store invalid
        # values in our cache.
        return copy.deepcopy(self._last_query)

    def _getCompileParams(self, fileName, flags=None):
        params = self._getCompilationDBParams(fileName)
        args = params['args']

        # Use python's shell command lexer to correctly split the list of options in
        # accordance with the POSIX standard
        if flags:
            if not isinstance(flags, (tuple, list, set)):
                flags = [flags]
            for f in flags:
                args.extend(shlex.split(f))

        if self.builtin_header_path and '-nobuiltininc' not in args:
            args.append('-I{}'.format(self.builtin_header_path))

        return {
            'args': args,
            'cwd': params['cwd'],
        }

    def _getCurrentTranslationUnit(self, args, fileName, fileBuffer=None, update=False):
        unsaved_files = [(fileName, encode(fileBuffer))] if fileBuffer else None

        tu = self.translationUnits.get(fileName)
        if tu is not None:
            if update:
                tu.reparse(unsaved_files)
            return tu

        flags = cindex.TranslationUnit.PARSE_PRECOMPILED_PREAMBLE | cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
        try:
            tu = self.index.parse(fileName, args, unsaved_files, flags)
        except cindex.TranslationUnitLoadError:
            return None

        self.translationUnits[fileName] = tu

        # Reparse to initialize the PCH cache even for auto completion
        # This should be done by index.parse(), however it is not.
        # So we need to reparse ourselves.
        tu.reparse(unsaved_files)

        return tu

    def warmupCache(self, fileName, fileBuffer=None, flags=None, update=True):
        """
        Used for warming up cache and also for keeping buffer readily updated
        """
        params = self._getCompileParams(fileName, flags=flags)

        with self.libclangLock:
            self._getCurrentTranslationUnit(params['args'], fileName, fileBuffer, update=update)

    def getCurrentCompletions(self, fileName, line, column, fileBuffer=None, flags=None, prefix=None, sorting=None, include_macros=False, include_code_patterns=False, include_brief_comments=False):
        """
        Gets completions
            prefix filters those with such prefix
            sorting can be priority or alpha
        """
        params = self._getCompileParams(fileName, flags=flags)

        with self.libclangLock:
            tu = self._getCurrentTranslationUnit(params['args'], fileName, fileBuffer)
            if tu is None:
                self.log.info("Couldn't get the TranslationUnit. The following arguments are used for clang: %s", " ".join(decode(params['args'])))
                return None

            unsaved_files = [(fileName, encode(fileBuffer))] if fileBuffer else None
            cr = tu.codeComplete(fileName, line, column, unsaved_files=unsaved_files, include_macros=include_macros, include_code_patterns=include_code_patterns, include_brief_comments=include_brief_comments)
            if cr is None:
                self.log.info("Cannot parse this source file. The following arguments are used for clang: %s", " ".join(decode(params['args'])))
                return None

            results = cr.results

        if prefix:
            results = [x for x in results if getAbbr(x.string).startswith(prefix)]

        sort_key = {
            'priority': lambda x: x.string.priority,
            'alpha': lambda x: getAbbr(x.string).lower(),
        }.get(sorting)
        if sort_key:
            results = sorted(results, key=sort_key)

        return list(map(formatResult, results))

    def gotoDeclaration(self, fileName, line, column, fileBuffer=None, flags=None):
        """
        Gets location for jump to definition
        """
        params = self._getCompileParams(fileName, flags=flags)
        with self.libclangLock:
            tu = self._getCurrentTranslationUnit(params['args'], fileName, fileBuffer, update=True)
            if tu is None:
                self.log.info("Couldn't get the TranslationUnit. The following arguments are used for clang: %s", " ".join(decode(params['args'])))
                return None

            f = cindex.File.from_name(tu, fileName)
            loc = cindex.SourceLocation.from_position(tu, f, line, column + 1)
            cursor = cindex.Cursor.from_location(tu, loc)
            defs = [cursor.get_definition(), cursor.referenced]

            for d in defs:
                if d is not None and loc != d.location:
                    loc = d.location
                    if loc.file is not None:
                        return {
                            'filename': loc.file.name,
                            'line': loc.line,
                            'column': loc.column,
                        }
                    return None
