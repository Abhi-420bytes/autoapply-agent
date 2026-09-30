"""Validation of LaTeX that goes into a region (generated or hand-edited).

Region content can only use commands the template itself already uses inside its
regions (or defines in its preamble), plus basic text formatting. That keeps generated
content inside the template's visual language, and closes the dangerous doors:
- file access / code execution (\\input, \\write, \\openin, \\directlua, ...)
- redefinition tricks (\\def, \\let, \\catcode, \\makeatletter, ...)
- layout changes (new \\vspace values, \\setlength, \\geometry, \\newpage, font sizes, ...)
Spacing commands are allowed only with argument values the template already uses in
its regions, so no new spacing is ever introduced (hard rule 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.latex.regions import ANY_MARKER_RE, parse_regions

MAX_REGION_CHARS = 20_000

DENY: frozenset[str] = frozenset(
    {
        # file access / execution
        "input",
        "include",
        "includeonly",
        "InputIfFileExists",
        "IfFileExists",
        "write",
        "immediate",
        "openout",
        "openin",
        "read",
        "readline",
        "closeout",
        "closein",
        "special",
        "pdfliteral",
        "directlua",
        "luaexec",
        "latelua",
        "ShellEscape",
        "verbatiminput",
        "lstinputlisting",
        "includegraphics",
        "includepdf",
        "pdfximage",
        "jobname",
        "endinput",
        # redefinition / catcode games
        "def",
        "edef",
        "gdef",
        "xdef",
        "let",
        "futurelet",
        "catcode",
        "csname",
        "endcsname",
        "expandafter",
        "noexpand",
        "newcommand",
        "renewcommand",
        "providecommand",
        "DeclareRobustCommand",
        "newenvironment",
        "renewenvironment",
        "makeatletter",
        "makeatother",
        "usepackage",
        "RequirePackage",
        "documentclass",
        "AtBeginDocument",
        "AtEndDocument",
        "everypar",
        "uppercase",
        "lowercase",
        "scantokens",
        # layout / spacing / fonts / page breaks
        "setlength",
        "addtolength",
        "setcounter",
        "addtocounter",
        "geometry",
        "newgeometry",
        "pagestyle",
        "thispagestyle",
        "fontsize",
        "selectfont",
        "linespread",
        "baselineskip",
        "newpage",
        "clearpage",
        "cleardoublepage",
        "pagebreak",
        "nopagebreak",
        "enlargethispage",
        "vfill",
        "vskip",
        "hskip",
        "kern",
        "raisebox",
        "scalebox",
        "resizebox",
        "parskip",
        "parindent",
        "topmargin",
        "textheight",
        "textwidth",
        "columnsep",
        "twocolumn",
        "onecolumn",
        "tiny",
        "Huge",
        "huge",
    }
)

SPACING: frozenset[str] = frozenset({"vspace", "hspace"})

SAFE_BASE: frozenset[str] = frozenset(
    {
        "textbf",
        "textit",
        "emph",
        "underline",
        "texttt",
        "textsc",
        "textsl",
        "textnormal",
        "small",
        "footnotesize",
        "normalsize",
        "href",
        "url",
        "item",
        "hfill",
        "quad",
        "qquad",
        "textbar",
        "textendash",
        "textemdash",
        "textasciitilde",
        "textbullet",
        "ldots",
        "dots",
        "LaTeX",
        "TeX",
        "cdot",
        "times",
        "sim",
        "approx",
        "leq",
        "geq",
        "textdollar",
        "textpercent",
        "textregistered",
        "texttrademark",
        "copyright",
        "textbackslash",
        "textasciicircum",
        "begin",
        "end",
        "and",
    }
)
# escaped characters and control symbols that are always fine
SAFE_SYMBOLS: frozenset[str] = frozenset(
    {
        "&",
        "%",
        "$",
        "#",
        "_",
        "{",
        "}",
        "\\",
        " ",
        ",",
        ";",
        "!",
        "-",
        "/",
        "'",
        "`",
        '"',
        "~",
        "^",
        ".",
    }
)

_CMD_RE = re.compile(r"\\([A-Za-z@]+\*?|.)")
_ENV_RE = re.compile(r"\\(begin|end)\s*\{([^}]*)\}")
_SPACING_RE = re.compile(r"\\(vspace|hspace)\*?\s*\{([^}]*)\}")
_NEWCMD_RE = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\*?\s*\{?\\([A-Za-z]+)\}?")
_NEWENV_RE = re.compile(r"\\newenvironment\*?\s*\{([^}]*)\}")


class ContentError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    commands: frozenset[str]
    environments: frozenset[str]
    spacing: frozenset[str]  # exact normalized "vspace{-2pt}" strings from the template
    macros: frozenset[str] = field(default_factory=frozenset)  # preamble-defined (info)


def strip_comments(tex: str) -> str:
    """Remove % comments, respecting escaped \\%."""
    out = []
    for line in tex.split("\n"):
        i = 0
        while True:
            j = line.find("%", i)
            if j == -1:
                out.append(line)
                break
            backslashes = 0
            k = j - 1
            while k >= 0 and line[k] == "\\":
                backslashes += 1
                k -= 1
            if backslashes % 2 == 0:
                out.append(line[:j])
                break
            i = j + 1
    return "\n".join(out)


def _norm_spacing(cmd: str, arg: str) -> str:
    return f"{cmd}{{{arg.replace(' ', '')}}}"


def build_policy(template_tex: str) -> Policy:
    regions = parse_regions(template_tex)
    body = strip_comments("\n".join(r.content for r in regions))
    used = {m.group(1).rstrip("*") for m in _CMD_RE.finditer(body) if m.group(1)[0].isalpha()}
    envs = {m.group(2).strip() for m in _ENV_RE.finditer(body)}
    spacing = {_norm_spacing(m.group(1), m.group(2)) for m in _SPACING_RE.finditer(body)}

    preamble = strip_comments(template_tex.split("\\begin{document}", 1)[0])
    macros = set(_NEWCMD_RE.findall(preamble))
    envs |= {e.strip() for e in _NEWENV_RE.findall(preamble)}

    commands = (used | macros | SAFE_BASE) - DENY
    return Policy(
        commands=frozenset(commands),
        environments=frozenset(envs | {"itemize"}),
        spacing=frozenset(spacing),
        macros=frozenset(macros),
    )


def check_content(name: str, content: str, policy: Policy) -> None:
    """Raise ContentError describing the first problem found in a region's content."""
    if len(content) > MAX_REGION_CHARS:
        raise ContentError(f"{name}: content is too long ({len(content)} chars)")
    if ANY_MARKER_RE.search(content):
        raise ContentError(f"{name}: content must not contain region markers")
    if "^^" in content:
        raise ContentError(f"{name}: '^^' character codes are not allowed")

    code = strip_comments(content)
    depth = 0
    i = 0
    while i < len(code):
        ch = code[i]
        if ch == "\\":
            i += 2  # skip escaped char (\{ \} \\ etc.)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise ContentError(f"{name}: unbalanced braces (extra '}}')")
        i += 1
    if depth != 0:
        raise ContentError(f"{name}: unbalanced braces ({depth} unclosed '{{')")

    for m in _CMD_RE.finditer(code):
        raw = m.group(1)
        cmd = raw.rstrip("*")
        if not raw[0].isalpha() and raw[0] != "@":
            if raw not in SAFE_SYMBOLS:
                raise ContentError(f"{name}: control symbol \\{raw} is not allowed")
            continue
        if "@" in cmd:
            raise ContentError(f"{name}: internal command \\{cmd} is not allowed")
        if cmd in DENY:
            raise ContentError(f"{name}: \\{cmd} is not allowed in region content")
        if cmd in SPACING:
            continue  # checked with its argument below
        if cmd not in policy.commands:
            raise ContentError(
                f"{name}: \\{cmd} isn't used anywhere in the template's regions; "
                "only the template's own commands and basic formatting are allowed"
            )

    for m in _SPACING_RE.finditer(code):
        key = _norm_spacing(m.group(1), m.group(2))
        if key not in policy.spacing:
            raise ContentError(
                f"{name}: \\{key} would add spacing the template doesn't use; "
                "spacing is locked to the template's values"
            )
    bare_spacing = len(re.findall(r"\\(?:vspace|hspace)\b", code))
    if bare_spacing != len(_SPACING_RE.findall(code)):
        raise ContentError(f"{name}: spacing commands must have a {{length}} argument")

    if re.search(r"\\\\\s*\[", code):
        raise ContentError(f"{name}: '\\\\[length]' adds spacing; use a plain line break")

    for m in _ENV_RE.finditer(code):
        env = m.group(2).strip()
        if env == "document" or env not in policy.environments:
            raise ContentError(f"{name}: environment '{env}' is not allowed here")


def check_all(contents: dict[str, str], policy: Policy) -> None:
    for name, body in contents.items():
        check_content(name, body, policy)


_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_text(text: str) -> str:
    """Escape plain text for safe inclusion in LaTeX (used by the generator in Phase 5)."""
    return "".join(_SPECIAL.get(ch, ch) for ch in text)
