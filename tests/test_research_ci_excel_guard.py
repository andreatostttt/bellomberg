"""The research CI Excel guard: direct opens and renames onto spreadsheet names fail."""
import os
from pathlib import Path
import runpy

import pytest

GUARD = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/testing/research_ci.py"))


@pytest.mark.parametrize("target", ["model.xlsx", "MODEL.XLSM", "old.xls"])
def test_rename_or_replace_onto_a_spreadsheet_is_forbidden(target):
    with pytest.raises(GUARD["ExcelGenerationForbidden"]):
        GUARD["forbid_excel_write"]("os.rename", ("scratch.tmp", target, None, None))


def test_writing_open_is_forbidden_and_reads_or_other_renames_are_not():
    flags = os.O_WRONLY | os.O_CREAT
    with pytest.raises(GUARD["ExcelGenerationForbidden"]):
        GUARD["forbid_excel_write"]("open", ("book.xlsx", "wb", flags))
    GUARD["forbid_excel_write"]("open", ("book.xlsx", "rb", os.O_RDONLY))
    GUARD["forbid_excel_write"]("os.rename", ("a.tmp", "report.pdf", None, None))
