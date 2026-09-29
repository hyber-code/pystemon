'''
Syntax names Pastebin accepts (the api_paste_format list of https://pastebin.com/doc_api,
plus 'text' for plain text). The scraping API uses the same short names for its `lang`
filter and in the `syntax` field of its paste list.
'''

PASTEBIN_SYNTAXES = frozenset([
    '4cs', '6502acme', '6502kickass', '6502tasm', '68000devpac', 'abap', 'actionscript',
    'actionscript3', 'ada', 'aimms', 'algol68', 'apache', 'applescript', 'apt_sources',
    'arduino', 'arm', 'asm', 'asp', 'asymptote', 'autoconf', 'autohotkey', 'autoit', 'avisynth',
    'awk', 'b3d', 'bascomavr', 'bash', 'basic4gl', 'bf', 'bibtex', 'blitzbasic', 'bmx', 'bnf',
    'boo', 'c', 'c_loadrunner', 'c_mac', 'c_winapi', 'caddcl', 'cadlisp', 'ceylon', 'cfdg',
    'cfm', 'chaiscript', 'chapel', 'cil', 'clojure', 'cmake', 'cobol', 'coffeescript', 'cpp',
    'cpp-qt', 'cpp-winapi', 'csharp', 'css', 'cuesheet', 'd', 'dart', 'dcl', 'dcpu16', 'dcs',
    'delphi', 'diff', 'div', 'dos', 'dot', 'e', 'ecmascript', 'eiffel', 'email', 'epc',
    'erlang', 'euphoria', 'ezt', 'f1', 'falcon', 'filemaker', 'fo', 'fortran', 'freebasic',
    'freeswitch', 'fsharp', 'gambas', 'gdb', 'gdscript', 'genero', 'genie', 'gettext', 'glsl',
    'gml', 'gnuplot', 'go', 'godot-glsl', 'groovy', 'gwbasic', 'haskell', 'haxe', 'hicest',
    'hq9plus', 'html4strict', 'html5', 'icon', 'idl', 'ini', 'inno', 'intercal', 'io',
    'ispfpanel', 'j', 'java', 'java5', 'javascript', 'jcl', 'jquery', 'json', 'julia',
    'kixtart', 'klonec', 'klonecpp', 'kotlin', 'ksp', 'latex', 'lb', 'ldif', 'lisp', 'llvm',
    'locobasic', 'logtalk', 'lolcode', 'lotusformulas', 'lotusscript', 'lscript', 'lsl2', 'lua',
    'm68k', 'magiksf', 'make', 'mapbasic', 'markdown', 'matlab', 'mercury', 'metapost', 'mirc',
    'mk-61', 'mmix', 'modula2', 'modula3', 'mpasm', 'mxml', 'mysql', 'nagios', 'netrexx',
    'newlisp', 'nginx', 'nim', 'nsis', 'oberon2', 'objc', 'objeck', 'ocaml', 'ocaml-brief',
    'octave', 'oobas', 'oorexx', 'oracle11', 'oracle8', 'oxygene', 'oz', 'parasail', 'parigp',
    'pascal', 'pawn', 'pcre', 'per', 'perl', 'perl6', 'pf', 'phix', 'php', 'php-brief', 'pic16',
    'pike', 'pixelbender', 'pli', 'plsql', 'postgresql', 'postscript', 'povray', 'powerbuilder',
    'powershell', 'proftpd', 'progress', 'prolog', 'properties', 'providex', 'puppet',
    'purebasic', 'pycon', 'pys60', 'python', 'q', 'qbasic', 'qml', 'racket', 'rails', 'rbs',
    'rebol', 'reg', 'rexx', 'robots', 'roff', 'rpmspec', 'rsplus', 'ruby', 'rust', 'sas',
    'scala', 'scheme', 'scilab', 'scl', 'sclang', 'sdlbasic', 'smalltalk', 'smarty', 'spark',
    'sparql', 'sqf', 'sql', 'sshconfig', 'standardml', 'stonescript', 'swift', 'systemverilog',
    'tcl', 'teraterm', 'texgraph', 'text', 'thinbasic', 'tsql', 'typescript', 'typoscript',
    'unicon', 'upc', 'urbi', 'uscript', 'vala', 'vb', 'vbnet', 'vbscript', 'vedit', 'verilog',
    'vhdl', 'vim', 'visualfoxpro', 'visualprolog', 'whitespace', 'whois', 'winbatch', 'xbasic',
    'xml', 'xojo', 'xorg_conf', 'xpp', 'yaml', 'yara', 'z80', 'zxbasic',
])
