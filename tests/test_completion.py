"""Tests for talaria.cli.completion — bash/zsh completion script generation.

Layout:

* TestCollect — parser-tree introspection (options, subcommands, nesting)
* TestBash — bash script structure, subcommand/option coverage, eval
* TestZsh — zsh script structure, subcommand/option coverage, eval
* TestRenderDispatch — shell selection, unsupported shell error
* TestCli — `talaria completion <shell>` via subprocess
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from talaria.cli import build_parser
from talaria.cli import completion

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------- TestCollect ----------

class TestCollect:
    def test_root_has_five_top_level_subcommands(self) -> None:
        root = completion.collect(build_parser())
        names = sorted(s.name for s in root.subcommands)
        assert names == ["completion", "config", "hermes", "paths", "skills"]

    def test_collect_is_idempotent(self) -> None:
        root1 = completion.collect(build_parser())
        root2 = completion.collect(build_parser())
        assert len(root1.subcommands) == len(root2.subcommands)

    def test_hermes_group_has_all_features(self) -> None:
        root = completion.collect(build_parser())
        hermes = next(s for s in root.subcommands if s.name == "hermes")
        names = {s.name for s in hermes.subcommands}
        assert names == {
            "doctor", "benchmark", "refresh-catalog", "serve-stop", "log-rotate",
        }

    def test_config_group_has_three_commands(self) -> None:
        root = completion.collect(build_parser())
        config = next(s for s in root.subcommands if s.name == "config")
        names = {s.name for s in config.subcommands}
        assert names == {"sync", "apply-auxiliary", "sync-env"}

    def test_skills_group_has_four_commands(self) -> None:
        root = completion.collect(build_parser())
        skills = next(s for s in root.subcommands if s.name == "skills")
        names = {s.name for s in skills.subcommands}
        assert names == {"install", "uninstall", "create-category", "prune"}

    def test_latest_extension_flags_are_collected(self) -> None:
        root = completion.collect(build_parser())
        doctor = self._find(root, ["hermes", "doctor"])
        sync = self._find(root, ["config", "sync"])
        assert doctor is not None
        assert sync is not None

        doctor_flags = {flag for option in doctor.options for flag in option.flags}
        assert {
            "--apply-curator-suggestions",
            "--prune-stale-locks",
            "--close-zombies",
            "--prune-ghost-sessions",
            "--apply",
        } <= doctor_flags

        sync_flags = {flag for option in sync.options for flag in option.flags}
        assert "--skip-auth" in sync_flags

    def test_leaf_subcommand_has_options(self) -> None:
        root = completion.collect(build_parser())
        diag = self._find(root, ["hermes", "doctor"])
        assert diag is not None
        flag_set = {f for opt in diag.options for f in opt.flags}
        assert "--json" in flag_set
        assert "--days" in flag_set

    def test_option_takes_arg_detected(self) -> None:
        root = completion.collect(build_parser())
        diag = self._find(root, ["hermes", "doctor"])
        assert diag is not None
        days = next(opt for opt in diag.options if "--days" in opt.flags)
        assert days.takes_arg is True

    def test_store_true_option_takes_no_arg(self) -> None:
        root = completion.collect(build_parser())
        diag = self._find(root, ["hermes", "doctor"])
        assert diag is not None
        json_opt = next(opt for opt in diag.options if "--json" in opt.flags)
        assert json_opt.takes_arg is False

    def test_subparser_action_is_not_listed_as_option(self) -> None:
        root = completion.collect(build_parser())
        # None of the root options should be the subparser pseudo-action
        for opt in root.options:
            for flag in opt.flags:
                assert not flag.startswith("config")
                assert not flag.startswith("hermes")

    @staticmethod
    def _find(cmd: completion._Command, lineage: list[str]) -> completion._Command | None:
        current: completion._Command | None = cmd
        for name in lineage:
            if current is None:
                return None
            current = next((s for s in current.subcommands if s.name == name), None)
        return current


# ---------- TestBash ----------

class TestBash:
    def render(self) -> str:
        return completion.render_bash(build_parser())

    def test_has_completion_function(self) -> None:
        script = self.render()
        assert "_talaria_completion()" in script
        assert "complete -F _talaria_completion talaria" in script

    def test_contains_all_top_level_subcommands(self) -> None:
        script = self.render()
        for name in ["paths", "completion", "hermes", "skills", "config"]:
            assert name in script

    def test_contains_nested_subcommands(self) -> None:
        script = self.render()
        for name in [
            "doctor",
            "benchmark",
            "refresh-catalog",
            "serve-stop",
            "log-rotate",
            "install",
            "uninstall",
            "create-category",
            "prune",
            "sync",
            "apply-auxiliary",
            "sync-env",
        ]:
            assert name in script

    def test_contains_option_flags(self) -> None:
        script = self.render()
        assert "--json" in script
        assert "--profile" in script
        assert "--dry-run" in script

    def test_contains_latest_extension_flags(self) -> None:
        script = self.render()
        for flag in [
            "--apply-curator-suggestions",
            "--prune-stale-locks",
            "--close-zombies",
            "--prune-ghost-sessions",
            "--apply",
            "--skip-auth",
        ]:
            assert flag in script

    def test_multi_word_paths_are_quoted(self) -> None:
        script = self.render()
        # Two-level paths like "config sync" must be quoted case patterns
        assert '"config sync")' in script
        assert '"hermes doctor")' in script

    def test_root_branch_not_emitted_first(self) -> None:
        # The root (lineage=[]) must NOT produce a "*" branch before the
        # subcommand branches — it would shadow them all.
        script = self.render()
        case_start = script.index('case "$joined" in')
        after_case = script[case_start:]
        first_branch = after_case[after_case.index("\n") + 1:after_case.index(")") + 1]
        assert first_branch.strip() != "*)"

    def test_eval_creates_function(self) -> None:
        """The generated script must be eval-able in bash and define the function."""
        script = self.render()
        result = subprocess.run(
            ["bash", "-c", script + "\ntype -t _talaria_completion"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "function" in result.stdout

    def test_bash_syntax_valid(self) -> None:
        """Run `bash -n` on the generated script."""
        pytest.importorskip("subprocess")
        script = self.render()
        result = subprocess.run(
            ["bash", "-n"],
            input=script, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"bash syntax error:\n{result.stderr}"

    def test_bash_completion_offers_top_level_subs(self) -> None:
        """Functional test: source the script and invoke the completion function."""
        script = self.render()
        test_script = """
source /dev/stdin
COMP_WORDS=(talaria ""); COMP_CWORD=1
_talaria_completion
echo "TOP:${COMPREPLY[*]}"
COMP_WORDS=(talaria config ""); COMP_CWORD=2
_talaria_completion
echo "CONFIG:${COMPREPLY[*]}"
COMP_WORDS=(talaria hermes doctor -); COMP_CWORD=3
_talaria_completion
echo "DIAG_FLAGS:${COMPREPLY[*]}"
"""
        result = subprocess.run(
            ["bash"],
            input=script + test_script, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        lines = result.stdout.strip().splitlines()
        assert "TOP:" in lines[0]
        assert "paths" in lines[0]
        assert "hermes" in lines[0]
        assert "config" in lines[0]
        assert "sync" in lines[1]
        assert "--days" in lines[2]


# ---------- TestZsh ----------

class TestZsh:
    def render(self) -> str:
        return completion.render_zsh(build_parser())

    def test_has_compdef_directive(self) -> None:
        script = self.render()
        # The `#compdef _funcname <cmd>` form (vs the bare `#compdef
        # <cmd>` autoload form) registers the function as the completion
        # for `talaria` when the script is sourced via `eval "$(…)"` in
        # ~/.zshrc. The bare form only works for autoloaded files.
        assert script.startswith("#compdef _talaria talaria")

    def test_defines_function(self) -> None:
        script = self.render()
        assert "_talaria()" in script
        # The script must NOT end with a `_talaria "$@"` call — invoking
        # the function in non-completion context (which `eval` does)
        # raises `_arguments:comparguments:… can only be called from
        # completion function`.
        assert not script.rstrip().endswith('_talaria "$@"')

    def test_contains_all_top_level_subcommands(self) -> None:
        script = self.render()
        # Each top-level subcommand appears in the cmds=() declaration
        for name in ["paths", "completion", "hermes", "skills", "config"]:
            assert name in script
        assert "cmds=(" in script

    def test_contains_nested_subcommands(self) -> None:
        script = self.render()
        for name in [
            "doctor",
            "benchmark",
            "refresh-catalog",
            "serve-stop",
            "log-rotate",
            "install",
            "uninstall",
            "create-category",
            "prune",
            "sync",
            "apply-auxiliary",
            "sync-env",
        ]:
            assert name in script

    def test_contains_option_specs(self) -> None:
        script = self.render()
        assert "'--json" in script
        assert "'--profile" in script
        assert "'--dry-run" in script

    def test_contains_latest_extension_specs(self) -> None:
        script = self.render()
        for flag in [
            "--apply-curator-suggestions",
            "--prune-stale-locks",
            "--close-zombies",
            "--prune-ghost-sessions",
            "--apply",
            "--skip-auth",
        ]:
            assert f"'{flag}" in script

    def test_takes_arg_options_have_value_placeholder(self) -> None:
        script = self.render()
        # --days takes an argument, so its zsh spec must end with :value:'
        assert "'--days=" in script
        assert ":value:'" in script

    def test_apostrophe_in_help_is_escaped(self) -> None:
        script = self.render()
        # "--version" help text contains an apostrophe ("program's")
        # The escaped form splits the single-quoted string: '\''
        assert "program'\\''s" in script

    def test_zsh_syntax_valid(self) -> None:
        """Run `zsh -n` on the generated script if zsh is installed."""
        zsh = Path("/usr/bin/zsh")
        if not zsh.exists():
            pytest.skip("zsh not installed")
        script = self.render()
        result = subprocess.run(
            [str(zsh), "-n"],
            input=script, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"zsh syntax error:\n{result.stderr}"

    def test_zsh_function_loads(self) -> None:
        """Verify the generated script defines a loadable shell function."""
        zsh = Path("/usr/bin/zsh")
        if not zsh.exists():
            pytest.skip("zsh not installed")
        script = self.render()
        result = subprocess.run(
            [str(zsh), "-c", script + "\nwhence -w _talaria"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "_talaria: function" in result.stdout or "shell function" in result.stdout


# ---------- TestRenderDispatch ----------

class TestRenderDispatch:
    def test_bash_dispatch(self) -> None:
        assert "complete -F" in completion.render(build_parser(), "bash")

    def test_zsh_dispatch(self) -> None:
        assert "#compdef" in completion.render(build_parser(), "zsh")

    def test_unsupported_shell_raises(self) -> None:
        with pytest.raises(completion.CompletionError, match="unsupported shell"):
            completion.render(build_parser(), "fish")

    def test_unsupported_shell_lists_valid_options(self) -> None:
        with pytest.raises(completion.CompletionError) as exc_info:
            completion.render(build_parser(), "tcsh")
        assert "bash" in str(exc_info.value)
        assert "zsh" in str(exc_info.value)

    def test_custom_prog_name(self) -> None:
        script = completion.render(build_parser(), "bash", prog="mycli")
        assert "_mycli_completion()" in script
        assert "complete -F _mycli_completion mycli" in script


# ---------- TestCli ----------

class TestCli:
    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "talaria.cli", "completion", *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )

    def test_help_exits_zero(self) -> None:
        result = self._cli("--help")
        assert result.returncode == 0
        assert "bash" in result.stdout
        assert "zsh" in result.stdout

    def test_bash_outputs_script(self) -> None:
        result = self._cli("bash")
        assert result.returncode == 0
        assert "complete -F _talaria_completion talaria" in result.stdout

    def test_zsh_outputs_script(self) -> None:
        result = self._cli("zsh")
        assert result.returncode == 0
        assert result.stdout.startswith("#compdef _talaria talaria")

    def test_invalid_shell_exits_2(self) -> None:
        result = self._cli("fish")
        assert result.returncode == 2
        assert "invalid choice" in result.stderr

    def test_no_shell_arg_exits_2(self) -> None:
        result = self._cli()
        assert result.returncode == 2

    def test_bash_output_is_valid_syntax(self) -> None:
        result = self._cli("bash")
        check = subprocess.run(
            ["bash", "-n"],
            input=result.stdout, capture_output=True, text=True,
        )
        assert check.returncode == 0, check.stderr

    def test_zsh_output_is_valid_syntax(self) -> None:
        zsh = Path("/usr/bin/zsh")
        if not zsh.exists():
            pytest.skip("zsh not installed")
        result = self._cli("zsh")
        check = subprocess.run(
            [str(zsh), "-n"],
            input=result.stdout, capture_output=True, text=True,
        )
        assert check.returncode == 0, check.stderr

    def test_zsh_output_evals_silently_in_zshrc(self) -> None:
        """Regression: `eval "$(talaria completion zsh)"` in ~/.zshrc
        must not raise `_arguments:comparguments:… can only be called
        from completion function`. The bug was the trailing
        `_talaria "$@"` call which invoked `_arguments` outside a
        completion context when the script was sourced.
        """
        zsh = Path("/usr/bin/zsh")
        if not zsh.exists():
            pytest.skip("zsh not installed")
        result = self._cli("zsh")
        # Simulate `eval "$(talaria completion zsh)"` in .zshrc: load
        # compinit, then eval the script. Any stderr from `_arguments`
        # is the regression.
        check = subprocess.run(
            [str(zsh), "-c",
             'autoload -U compinit && compinit -u && eval "$1"',
             "_", result.stdout],
            capture_output=True, text=True,
        )
        assert check.returncode == 0, check.stderr
        assert "can only be called from completion function" not in check.stderr
        assert "_arguments" not in check.stderr
