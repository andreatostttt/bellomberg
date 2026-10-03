"""Run the explicit research CI suite without legacy workbook generation.

Only stdlib is loaded here. The existing offline runner installs isolation before
pytest collection or any application import, and preserves its measured receipt.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys
import tempfile
import uuid


SUITE = (
    "tests/test_offline_runner_dir_fd.py",
    "tests/test_trade_idea_policy_runtime.py",
    "tests/test_trade_idea_execution_policy.py",
    "tests/test_research_admission_api.py",
    "tests/test_trade_idea_capo_finalization_dispatch.py",
    "tests/test_trade_idea_final_delivery_gate.py",
    "tests/test_trade_idea_report_v2.py",
    "tests/test_trade_idea_paid_capo_checkpoint.py",
    "tests/test_trade_idea_store.py",
    "tests/test_trade_idea_partial_settlement_reuse.py",
    "tests/test_trade_idea_response_recovery_store.py",
    "tests/test_trade_idea_capo_finalization_grant.py",
    "tests/test_company_document_stabilization.py",
    "tests/test_company_source_research.py",
    "tests/test_trade_idea_sec_cover.py",
    "tests/test_trade_idea_publication_independent_review.py",
    "tests/test_trade_idea_pm_sources.py",
    "tests/test_trade_idea_pm_sources_independent_review.py",
    "tests/test_trade_idea_v2_no_workbook_e2e.py",
)
# Legacy paths also prepare or write workbook fixtures. Keep them out of CI.
SELECTION = (
    "not actual_http_preflight and not mismatch_blocks_worker "
    "and not active_html_and_document_instructions_are_passive_evidence "
    "and not email_ambiguous_restart_requires_explicit_retry"
)


class ExcelGenerationForbidden(BaseException):
    """A spreadsheet write is a hard failure, including inside application code."""


def forbid_excel_write(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes)):
        return
    path, mode, flags = args
    writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if writing and Path(os.fsdecode(path)).suffix.lower() in {
        ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm",
    }:
        raise ExcelGenerationForbidden("Excel generation is forbidden in research CI: " + os.fsdecode(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root")
    args = parser.parse_args(argv)
    if not sys.flags.isolated:
        parser.error("use python -I tools/testing/research_ci.py")
    root = Path(__file__).resolve().parents[2]
    output = Path(args.output_root).resolve() if args.output_root else (
        Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
        / ("bellomberg-research-ci-" + uuid.uuid4().hex)
    ).resolve()
    os.chdir(root)
    sys.addaudithook(forbid_excel_write)
    runner = runpy.run_path(str(root / "tools/testing/offline_pytest.py"))["main"]
    return runner([
        "--output-root", str(output), *SUITE, "-k", SELECTION, "-q",
        "--junitxml=" + str(output / "junit.xml"),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
