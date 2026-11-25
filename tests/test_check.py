from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver import check


class MarkdownLinkCheckTests(unittest.TestCase):
    def test_valid_links_code_spans_and_external_urls_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs" / "notes").mkdir(parents=True)
            (root / "mod").mkdir()
            (root / "target.txt").write_text("ok", encoding="utf-8")
            (root / "docs" / "guide.md").write_text("guide", encoding="utf-8")
            (root / "docs" / "space file.md").write_text("space", encoding="utf-8")
            (root / "docs" / "notes" / "ref.md").write_text(
                "[back](../guide.md#section)\n", encoding="utf-8"
            )
            (root / "AGENTS.md").write_text("[readme](README.md)\n", encoding="utf-8")
            (root / "mod" / "README.md").write_text("[top](../README.md)\n", encoding="utf-8")
            (root / "README.md").write_text(
                "\n".join(
                    [
                        "[guide](docs/guide.md)",
                        "[dir](docs/notes/)",
                        '[`guide`](docs/guide.md "title")',
                        "[space](<docs/space file.md>)",
                        "[ext](https://en.wikipedia.org/wiki/Foo_(bar))",
                        "[mail](mailto:test@example.com)",
                        "[anchor](#local-heading)",
                        "`Block->[+0x68](mLegacy)`",
                        "```markdown",
                        "[ignored](does/not/exist.md)",
                        "```",
                        "~~~markdown",
                        "[also ignored](missing.md)",
                        "~~~",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            ok, output = check.check_markdown_links(root)
            self.assertTrue(ok, output)
            self.assertIn("6 files checked", output)

    def test_broken_and_non_relative_links_are_reported_with_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "README.md").write_text(
                "ok\n[missing](docs/nope.md#sec)\n[abs](/docs/nope.md)\n",
                encoding="utf-8",
            )
            errors = check.find_broken_markdown_links(root)
            self.assertEqual(
                errors,
                [
                    "README.md:2: broken link docs/nope.md",
                    "README.md:3: non-relative link /docs/nope.md",
                ],
            )
            ok, output = check.check_markdown_links(root)
            self.assertFalse(ok)
            self.assertIn("README.md:2: broken link docs/nope.md", output)

    def test_gitignored_link_targets_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("refs/\n", encoding="utf-8")
            (root / "refs").mkdir()
            (root / "refs" / "local.h").write_text("// local\n", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[ok](docs/guide.md)\n[ignored](refs/local.h)\n",
                encoding="utf-8",
            )
            errors = check.find_broken_markdown_links(root)
            self.assertEqual(errors, ["README.md:2: gitignored link refs/local.h"])
            ok, output = check.check_markdown_links(root)
            self.assertFalse(ok)
            self.assertIn("README.md:2: gitignored link refs/local.h", output)

    def test_repo_markdown_links_currently_pass(self) -> None:
        ok, output = check.check_markdown_links(check.REPO_ROOT)
        self.assertTrue(ok, output)


class CheckRunnerTests(unittest.TestCase):
    def _make_fake_repo(self, root: Path) -> None:
        (root / "skills" / "bedrock-re" / "checks" / "guards").mkdir(parents=True)
        (root / "skills" / "bedrock-re" / "checks" / "guards" / "check_alpha.py").write_text(
            "", encoding="utf-8"
        )
        (root / "skills" / "bedrock-re" / "checks" / "guards" / "check_beta.py").write_text(
            "", encoding="utf-8"
        )
        (root / "mod" / "probe" / "src" / "mod").mkdir(parents=True)
        (root / "mod" / "probe" / "src" / "mod" / "Commands.cpp").write_text(
            'if (cmd == "ping") return true;\n', encoding="utf-8"
        )
        (root / "mod" / "README.md").write_text("`ping`\n", encoding="utf-8")
        (root / "README.md").write_text("# Root\n", encoding="utf-8")

    def test_build_checks_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_fake_repo(root)

            full = [spec.name for spec in check.build_checks(root=root)]
            self.assertEqual(
                full,
                [
                    "ruff check",
                    "ruff format",
                    "mypy",
                    "lint_public",
                    "lint_skill",
                    "check_alpha",
                    "check_beta",
                    "gen_profiles",
                    "probes ruff",
                    "command docs",
                    "markdown links",
                    "git diff --check",
                    "unittest",
                ],
            )

            fast = [spec.name for spec in check.build_checks(fast=True, root=root)]
            self.assertNotIn("unittest", fast)
            self.assertIn("ruff check", fast)
            self.assertIn("gen_profiles", fast)
            self.assertIn("command docs", fast)

            docs = [spec.name for spec in check.build_checks(docs_only=True, root=root)]
            self.assertEqual(
                docs,
                [
                    "lint_public",
                    "lint_skill",
                    "check_alpha",
                    "check_beta",
                    "command docs",
                    "markdown links",
                    "git diff --check",
                ],
            )

            formatted = [spec.name for spec in check.build_checks(cpp_format=True, root=root)]
            self.assertIn("cpp format", formatted)

    def test_runner_returns_zero_when_all_selected_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_fake_repo(root)
            seen_commands: list[list[str]] = []

            def fake_run(cmd, cwd=None, **_kwargs):
                self.assertEqual(cwd, root)
                seen_commands.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

            buf = io.StringIO()
            with (
                patch.object(check.subprocess, "run", side_effect=fake_run),
                contextlib.redirect_stdout(buf),
            ):
                code, results = check.run_checks(fast=True, root=root)

            self.assertEqual(code, 0)
            self.assertTrue(all(item.passed for item in results))
            self.assertIn(["git", "diff", "--check"], seen_commands)
            self.assertIn("12/12 checks passed", buf.getvalue())

    def test_probes_ruff_checks_syntax_and_changed_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_fake_repo(root)
            ok, out = check.check_probes_ruff(root)
            self.assertTrue(ok, out)

            probe = (
                root
                / "skills"
                / "bedrock-re"
                / "findings"
                / "1.26.51.01"
                / "cases"
                / "demo"
                / "probes"
                / "p.py"
            )
            probe.parent.mkdir(parents=True)
            probe.write_text("print('ok')\n", encoding="utf-8")

            calls: list[list[str]] = []

            def fake_run(cmd, **_kwargs):
                calls.append(list(cmd))
                if cmd[:3] == ["git", "ls-files", "--others"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout="skills/bedrock-re/findings/1.26.51.01/cases/demo/probes/p.py\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch.object(check.subprocess, "run", side_effect=fake_run):
                ok, out = check.check_probes_ruff(root)
            self.assertTrue(ok, out)
            self.assertIn("1 scanned, 1 strict-checked", out)
            self.assertTrue(any("--select" in c and "E9,F63,F7,F82" in c for c in calls))

    def test_runner_continues_after_failure_and_reports_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_fake_repo(root)
            (root / "README.md").write_text("[broken](missing.md)\n", encoding="utf-8")

            def fake_run(cmd, **_kwargs):
                if "lint_skill.py" in " ".join(cmd):
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad skill link\n")
                if cmd == ["git", "diff", "--check"]:
                    raise OSError("git missing")
                return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

            buf = io.StringIO()
            with (
                patch.object(check.subprocess, "run", side_effect=fake_run),
                contextlib.redirect_stdout(buf),
            ):
                code, results = check.run_checks(docs_only=True, root=root)

            self.assertEqual(code, 1)
            by_name = {item.name: item for item in results}
            self.assertTrue(by_name["lint_public"].passed)
            self.assertFalse(by_name["lint_skill"].passed)
            self.assertTrue(by_name["command docs"].passed)
            self.assertFalse(by_name["markdown links"].passed)
            self.assertFalse(by_name["git diff --check"].passed)
            output = buf.getvalue()
            self.assertIn("bad skill link", output)
            self.assertIn("broken link missing.md", output)
            self.assertIn("git missing", output)

    def test_main_forwards_flags(self) -> None:
        with patch.object(check, "run_checks", return_value=(0, [])) as run_checks:
            self.assertEqual(check.main(["--docs-only"]), 0)
            run_checks.assert_called_once_with(
                docs_only=True,
                fast=False,
                cpp_format=False,
                cpp_format_all=False,
                root=check.REPO_ROOT,
            )

        with patch.object(check, "run_checks", return_value=(1, [])) as run_checks:
            self.assertEqual(check.main(["--fast", "--cpp-format", "--cpp-format-all"]), 1)
            run_checks.assert_called_once_with(
                docs_only=False,
                fast=True,
                cpp_format=True,
                cpp_format_all=True,
                root=check.REPO_ROOT,
            )

    def test_command_docs_detects_missing_and_ignores_selftest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_fake_repo(root)
            (root / "mod" / "probe" / "src" / "mod" / "Commands.cpp").write_text(
                'if (cmd == "ping" || cmd == "selftest_secret") return true;\n',
                encoding="utf-8",
            )
            ok, out = check.check_command_docs(root)
            self.assertTrue(ok, out)

            (root / "mod" / "probe" / "src" / "mod" / "CommandExtra.cpp").write_text(
                'if (cmd == "undoc_cmd") return true;\n', encoding="utf-8"
            )
            ok, out = check.check_command_docs(root)
            self.assertFalse(ok)
            self.assertIn("undoc_cmd", out)

    def test_cpp_format_runs_clang_format_on_non_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod_dir = root / "mod" / "probe" / "src" / "mod"
            mod_dir.mkdir(parents=True)
            keep = mod_dir / "Sample.cpp"
            keep.write_text("int main() {}\n", encoding="utf-8")
            (mod_dir / "GeneratedProfiles.h").write_text("// generated\n", encoding="utf-8")

            with (
                patch.object(check, "_find_clang_format", return_value="clang-format"),
                patch.object(
                    check.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                ) as run_mock,
            ):
                ok, out = check.check_cpp_format(root, all_files=True)

            self.assertTrue(ok, out)
            cmd = run_mock.call_args.args[0]
            self.assertEqual(cmd[:3], ["clang-format", "--dry-run", "--Werror"])
            self.assertEqual([Path(p).name for p in cmd[3:]], ["Sample.cpp"])

    def test_find_clang_format_uses_env_or_path_without_hardcoded_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            llvm_dir = Path(tmp)
            exe = llvm_dir / "bin" / "clang-format.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            with (
                patch.dict("os.environ", {"LEVIPILOT_LLVM_ROOT": str(llvm_dir)}, clear=False),
                patch("driver.check.shutil.which", return_value=None),
            ):
                self.assertEqual(check._find_clang_format(), str(exe))

        with (
            patch.dict("os.environ", {"LEVIPILOT_LLVM_ROOT": ""}, clear=False),
            patch("driver.check.shutil.which", return_value="clang-format-on-path"),
        ):
            self.assertEqual(check._find_clang_format(), "clang-format-on-path")

        with (
            patch.dict("os.environ", {"LEVIPILOT_LLVM_ROOT": ""}, clear=False),
            patch("driver.check.shutil.which", return_value=None),
        ):
            self.assertIsNone(check._find_clang_format())


if __name__ == "__main__":
    unittest.main()
