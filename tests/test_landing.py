from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from driver import landing

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "bedrock-re"


def run_landing(root: Path, *args: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return landing.main(["--findings-root", str(root), *args])


def begin(root: Path, case: str = "demo") -> int:
    return run_landing(root, "begin", "--case", case, "--goal", "unit test")


def probe(root: Path, case: str = "demo", scope: str = "unit") -> int:
    return run_landing(
        root,
        "probe",
        "--case",
        case,
        "--scope",
        scope,
        "--",
        sys.executable,
        "-c",
        "print('ok')",
    )


def write_claims(root: Path, claims: list[dict[str, object]]) -> Path:
    path = root / "claims.json"
    path.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    return path


class LandingTests(unittest.TestCase):
    def test_begin_probe_claim_end_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(begin(root), 0)
            self.assertEqual(probe(root), 0)
            case_dir = root / "1.26.51.01" / "cases" / "demo"
            self.assertTrue((case_dir / "old-account.md").exists())
            claims = write_claims(
                root,
                [
                    {
                        "hypothesis": "probe writes stdout",
                        "result": "confirmed",
                        "evidence_tier": "code",
                        "static_closure": "probe branch mirrors stdout into .out",
                        "evidence": "[代码] branch; [实测] output contains ok",
                        "probe_ref": "r01-001",
                    }
                ],
            )
            self.assertEqual(run_landing(root, "claim", "--case", "demo", "--file", str(claims)), 0)
            self.assertEqual(run_landing(root, "end", "--case", "demo"), 0)
            case_dir = root / "1.26.51.01" / "cases" / "demo"
            facts = (case_dir / "facts.md").read_text(encoding="utf-8")
            resume = (case_dir / "resume.md").read_text(encoding="utf-8")
            self.assertIn("static closure", facts)
            self.assertIn("Confirmed", resume)
            self.assertEqual(
                run_landing(root, "close", "--case", "demo", "--skip-tests", "--skip-public"),
                0,
            )
            self.assertTrue((case_dir / "report.md").exists())

    def test_confirmed_with_dynamic_only_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(begin(root), 0)
            self.assertEqual(probe(root), 0)
            claims = write_claims(
                root,
                [
                    {
                        "hypothesis": "only observed",
                        "result": "confirmed",
                        "evidence_tier": "live",
                        "evidence": "[实测] saw it",
                    }
                ],
            )
            self.assertEqual(
                run_landing(root, "claim", "--case", "demo", "--file", str(claims)),
                2,
            )

    def test_pending_gate_and_abandon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(begin(root), 0)
            self.assertEqual(probe(root), 0)
            self.assertEqual(begin(root), 2)
            self.assertEqual(
                run_landing(root, "abandon", "--case", "demo", "--reason", "unit cleanup"),
                0,
            )
            self.assertEqual(begin(root), 0)

    def test_probe_records_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(begin(root), 0)
            self.assertEqual(
                run_landing(
                    root,
                    "probe",
                    "--case",
                    "demo",
                    "--scope",
                    "scenario test",
                    "--scenario",
                    "out/demo/scenario.jsonl",
                    "--",
                    sys.executable,
                    "-c",
                    "print('ok')",
                ),
                0,
            )
            journal = root / "1.26.51.01" / "cases" / "demo" / "journal.jsonl"
            record = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["probe"]["scenario"], "out/demo/scenario.jsonl")

    def test_probe_requires_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(begin(root), 0)
            self.assertEqual(
                run_landing(
                    root,
                    "probe",
                    "--case",
                    "demo",
                    "--",
                    sys.executable,
                    "-c",
                    "print('ok')",
                ),
                2,
            )

    def test_lint_report_rejects_confirmed_without_static_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "1.26.51.01" / "cases" / "bad"
            case_dir.mkdir(parents=True)
            record = {
                "id": "r01-001",
                "round": 1,
                "seq": 1,
                "ts": "2026-10-28T00:00:00Z",
                "version": "1.26.51.01",
                "case": "bad",
                "kind": "claim",
                "origin": "manual",
                "hypothesis": "only observed",
                "probe": None,
                "result": "confirmed",
                "evidence_tier": "code",
                "static_closure": None,
                "evidence": "[代码] nothing",
                "roles": {},
            }
            (case_dir / "journal.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            lint = SKILL / "checks" / "lint_report.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(lint),
                    "--findings-root",
                    str(root),
                    "--version",
                    "1.26.51.01",
                    "--case",
                    "bad",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("static_closure", completed.stderr)

    def test_lint_report_rejects_pending_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(begin(root), 0)
            self.assertEqual(probe(root), 0)
            lint = SKILL / "checks" / "lint_report.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(lint),
                    "--findings-root",
                    str(root),
                    "--version",
                    "1.26.51.01",
                    "--case",
                    "demo",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("pending", completed.stderr)


class SkillChecksTests(unittest.TestCase):
    def test_lint_skill(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SKILL / "checks" / "lint_skill.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_lint_public(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SKILL / "checks" / "lint_public.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_roles_check(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SKILL / "roles" / "resolve.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("roles resolved", completed.stdout)

    def test_ida_dry_run(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SKILL / "tools" / "ida.py"),
                "--dry-run",
                "call",
                "decompile",
                "--args",
                '{"address":"0x141177690"}',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("decompile", completed.stdout)


if __name__ == "__main__":
    unittest.main()
