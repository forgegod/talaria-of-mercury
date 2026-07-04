"""Shell completion script generation for the ``talaria`` CLI.

Walks the live ``argparse`` parser tree (built by :func:`talaria.cli.build_parser`)
and emits a self-contained completion script for bash or zsh. The generated
script is pure shell — it never shells out to Python on every keystroke and
carries no runtime dependencies beyond the shell itself.

Public surface
--------------

* :func:`collect` — build the parser-tree description (introspection).
* :func:`render_bash` / :func:`render_zsh` — emit a completion script.
* :func:`render` — dispatch on a shell name (``"bash"`` / ``"zsh"``).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

__all__ = ["SHELLS", "CompletionError", "collect", "render", "render_bash", "render_zsh"]

SHELLS = ("bash", "zsh")


class CompletionError(ValueError):
    """Raised when an unsupported shell is requested."""


@dataclass
class _Option:
    flags: list[str]
    takes_arg: bool
    help: str = ""


@dataclass
class _Command:
    name: str
    help: str = ""
    options: list[_Option] = field(default_factory=list)
    subcommands: list["_Command"] = field(default_factory=list)


# ---------- argparse introspection ----------

def _is_subparser_action(action: argparse.Action) -> bool:
    return isinstance(action, argparse._SubParsersAction)


def _option_from_action(action: argparse.Action) -> _Option | None:
    """Return an :class:`_Option` for an optional ``--flag`` action, else ``None``."""
    if not action.option_strings:
        return None  # positional
    if _is_subparser_action(action):
        return None
    return _Option(
        flags=list(action.option_strings),
        takes_arg=action.nargs != 0,
        help=(action.help or "").strip(),
    )


def _collect_subtree(parser: argparse.ArgumentParser, name: str = "") -> _Command:
    """Recursively collect a parser into a :class:`_Command` tree."""
    cmd = _Command(name=name, help=(parser.description or "").strip())
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public API
        opt = _option_from_action(action)
        if opt is not None:
            cmd.options.append(opt)
    for action in parser._actions:  # noqa: SLF001
        if _is_subparser_action(action):
            for sub_name, sub_parser in action.choices.items():
                cmd.subcommands.append(_collect_subtree(sub_parser, name=sub_name))
    return cmd


def collect(parser: argparse.ArgumentParser) -> _Command:
    """Build a :class:`_Command` description of *parser* and its subparsers."""
    return _collect_subtree(parser)


def _flatten(cmd: _Command) -> list[tuple[list[str], _Command]]:
    """Return ``[(lineage, command)]`` for *cmd* and every descendant.

    ``lineage`` is the path of subcommand names from the root (exclusive of
    the unnamed root). The root itself appears as ``([], root)``.
    """
    out: list[tuple[list[str], _Command]] = [([], cmd)]
    stack = [(cmd, [])]
    while stack:
        parent, lineage = stack.pop()
        for sub in parent.subcommands:
            child_lineage = lineage + [sub.name]
            out.append((child_lineage, sub))
            stack.append((sub, child_lineage))
    return out


def _option_flags(cmd: _Command) -> list[str]:
    flags: list[str] = []
    for opt in cmd.options:
        flags.extend(opt.flags)
    return flags


# ---------- bash ----------

def render_bash(parser: argparse.ArgumentParser, prog: str = "talaria") -> str:
    """Render a bash completion script for *parser*."""
    root = collect(parser)
    flat = _flatten(root)

    # Build one case branch per command path. The branch matches on the
    # joined subcommand path (words after the program name, flags skipped).
    # The root itself (lineage=[]) is handled by the trailing *) fallback,
    # so we skip it here — emitting a "*" branch first would shadow the rest.
    branches: list[str] = []
    for lineage, cmd in flat:
        if not lineage:
            continue
        joined = " ".join(lineage)
        flags = _option_flags(cmd)
        subs = [s.name for s in cmd.subcommands]
        body: list[str] = []
        if subs and flags:
            body.append(
                f'            if [[ $cur == -* ]]; then '
                f'COMPREPLY=( $(compgen -W "{" ".join(flags)}" -- "$cur") ); '
                f'else COMPREPLY=( $(compgen -W "{" ".join(subs)}" -- "$cur") ); fi'
            )
        elif subs:
            body.append(
                f'            COMPREPLY=( $(compgen -W "{" ".join(subs)}" -- "$cur") )'
            )
        elif flags:
            body.append(
                f'            COMPREPLY=( $(compgen -W "{" ".join(flags)}" -- "$cur") )'
            )
        else:
            continue
        # Quote multi-word paths so bash treats them as one case pattern.
        label = f'"{joined}"' if " " in joined else joined
        branches.append(f"        {label})")
        branches.extend(body)
        branches.append("            ;;")

    case_body = "\n".join(branches)
    top_flags = " ".join(_option_flags(root))
    top_subs = " ".join(s.name for s in root.subcommands)

    return f"""# bash completion for {prog} — generated by `{prog} completion bash`
_{prog}_completion() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"

    # Collect the subcommand path: non-flag words after the program name.
    local i path=()
    for ((i=1; i<COMP_CWORD; i++)); do
        local w="${{COMP_WORDS[i]}}"
        [[ "$w" == -* ]] && continue
        path+=("$w")
    done
    local joined="${{path[*]}}"

    case "$joined" in
{case_body}
        *)
            # Fallback: top-level subcommands and global flags.
            if [[ $cur == -* ]]; then
                COMPREPLY=( $(compgen -W "{top_flags}" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "{top_subs}" -- "$cur") )
            fi
            ;;
    esac
    return 0
}}

complete -F _{prog}_completion {prog}
"""


# ---------- zsh ----------

def _zsh_argspecs(opt: _Option) -> list[str]:
    """Build zsh ``_arguments`` specs — one per flag spelling.

    Each spec looks like ``'--flag[help]'`` or ``'--flag=[help]:value:'``.
    Brace-grouping (``{-h,--help}``) is avoided so the output stays simple
    and robust across zsh versions.
    """
    # Escape single quotes for single-quoted zsh strings: '...' -> '\''...
    help_text = " ".join(opt.help.split()).replace("'", "'\\''")
    help_part = f"[{help_text}]" if help_text else ""
    specs: list[str] = []
    for flag in opt.flags:
        if opt.takes_arg:
            specs.append(f"'{flag}={help_part}:value:'")
        else:
            specs.append(f"'{flag}{help_part}'")
    return specs


def render_zsh(parser: argparse.ArgumentParser, prog: str = "talaria") -> str:
    """Render a zsh completion script for *parser*."""
    root = collect(parser)
    top_subs = [s.name for s in root.subcommands]
    root_specs: list[str] = []
    for o in root.options:
        root_specs.extend(_zsh_argspecs(o))

    # Per top-level subcommand: nested case dispatching on the second word.
    sub_blocks: list[str] = []
    for sub in root.subcommands:
        leaf_names = [leaf.name for leaf in sub.subcommands]
        leaf_block: list[str] = []
        for leaf in sub.subcommands:
            leaf_specs: list[str] = []
            for o in leaf.options:
                leaf_specs.extend(_zsh_argspecs(o))
            if not leaf_specs:
                continue
            leaf_block.append(f"                {leaf.name})")
            leaf_block.append("                    _arguments -C \\")
            for i, s in enumerate(leaf_specs):
                leaf_block.append(
                    f"                        {s}" + ("" if i == len(leaf_specs) - 1 else " \\")
                )
            leaf_block.append("                    ;;")

        sub_specs: list[str] = []
        for o in sub.options:
            sub_specs.extend(_zsh_argspecs(o))
        sub_block: list[str] = []
        sub_block.append(f"            {sub.name})")
        if leaf_names:
            sub_block.append("                local -a leaves")
            sub_block.append(f'                leaves=({" ".join(leaf_names)})')
            sub_block.append("                _arguments -C \\")
            sub_block.append('                    "1: :->leaf" \\')
            for s in sub_specs:
                sub_block.append(f"                    {s} \\")
            sub_block.append('                    "*::arg:->leafargs"')
            sub_block.append("                case $state in")
            sub_block.append("                    leaf)")
            sub_block.append("                        compadd -- $leaves")
            sub_block.append("                        ;;")
            sub_block.append("                    leafargs)")
            sub_block.append("                        case $line[2] in")
            sub_block.extend(leaf_block)
            sub_block.append("                        esac")
            sub_block.append("                        ;;")
            sub_block.append("                esac")
        elif sub_specs:
            sub_block.append("                _arguments -C \\")
            for i, s in enumerate(sub_specs):
                sub_block.append(
                    f"                    {s}" + ("" if i == len(sub_specs) - 1 else " \\")
                )
        else:
            sub_block.append("                return 0")
        sub_block.append("                ;;")
        sub_blocks.append("\n".join(sub_block))

    subs_body = "\n".join(sub_blocks)
    root_specs_body = ""
    if root_specs:
        joined = " \\\n        ".join(root_specs)
        root_specs_body = "        " + joined + " \\\n"

    return f"""#compdef {prog}
# zsh completion for {prog} — generated by `{prog} completion zsh`

_{prog}() {{
    local -a cmds
    cmds=({' '.join(top_subs)})
    _arguments -C \\
{root_specs_body}        "1: :->cmd" \\
        "*::arg:->args"
    case $state in
        cmd)
            compadd -- $cmds
            ;;
        args)
            case $line[1] in
{subs_body}
            esac
            ;;
    esac
}}

_{prog} "$@"
"""


# ---------- dispatch ----------

def render(parser: argparse.ArgumentParser, shell: str, prog: str = "talaria") -> str:
    """Render a completion script for *shell* (``bash`` or ``zsh``)."""
    if shell == "bash":
        return render_bash(parser, prog=prog)
    if shell == "zsh":
        return render_zsh(parser, prog=prog)
    raise CompletionError(
        f"unsupported shell {shell!r}; choose one of: {', '.join(SHELLS)}"
    )
