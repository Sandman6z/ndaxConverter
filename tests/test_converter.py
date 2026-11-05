"""Tests for engine.converter module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest
import xlsxwriter

from engine.converter import (
    STATUS_MAP,
    RECORD_HEADERS,
    STEP_HEADERS,
    CYCLE_HEADERS,
    ConvertResult,
    _ts_str,
    _get_ndax_files,
    _build_record_sheet,
    _build_step_sheet,
    _build_cycle_sheet,
    convert_one,
    convert_batch,
    get_ndax_files,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_timestamp(h: int = 10, m: int = 30, s: int = 0) -> pd.Timestamp:
    return pd.Timestamp("2026-01-15") + pd.Timedelta(hours=h, minutes=m, seconds=s)


def _sample_df(n_cycles: int = 2, steps_per_cycle: int = 2) -> pd.DataFrame:
    """Build a realistic DataFrame matching NewareNDA output schema."""
    rows = []
    for cyc in range(1, n_cycles + 1):
        for step in range(1, steps_per_cycle + 1):
            status = "CC_DChg" if step % 2 == 0 else "CC_Chg"
            for pt in range(3):  # 3 data points per step
                rows.append({
                    "Cycle": cyc,
                    "Step": (cyc - 1) * steps_per_cycle + step,
                    "Status": status,
                    "Current(mA)": -500.0 if status == "CC_DChg" else 500.0,
                    "Voltage": 3.7 - pt * 0.1 if status == "CC_DChg" else 3.5 + pt * 0.1,
                    "Charge_Capacity(mAh)": pt * 10.0 if status == "CC_Chg" else 0.0,
                    "Discharge_Capacity(mAh)": pt * 10.0 if status == "CC_DChg" else 0.0,
                    "Timestamp": _make_timestamp(h=cyc, m=step, s=pt),
                })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_df():
    return _sample_df()


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a clean temp dir."""
    return tmp_path


@pytest.fixture(autouse=False)
def use_threads_for_batch(monkeypatch):
    """Replace ProcessPoolExecutor with ThreadPoolExecutor so mocks work.
    
    ProcessPoolExecutor spawns separate processes that don't inherit mocks.
    ThreadPoolExecutor runs in-process, making @patch effective.
    """
    monkeypatch.setattr("engine.converter.ProcessPoolExecutor", ThreadPoolExecutor)


# ── _ts_str ───────────────────────────────────────────────────────────────

class TestTsStr:
    def test_normal_timestamp(self):
        ts = pd.Timestamp("2026-01-15 10:30:45")
        assert _ts_str(ts) == "2026-01-15 10:30:45"

    def test_with_microseconds(self):
        ts = pd.Timestamp("2026-01-15 10:30:45.123456")
        assert _ts_str(ts) == "2026-01-15 10:30:45"

    def test_with_timezone(self):
        ts = pd.Timestamp("2026-01-15 10:30:45", tz="Asia/Shanghai")
        result = _ts_str(ts)
        assert result == "2026-01-15 10:30:45"

    def test_string_input(self):
        result = _ts_str("2026-01-15 10:30:45")
        assert result == "2026-01-15 10:30:45"

    def test_midnight(self):
        ts = pd.Timestamp("2026-01-01 00:00:00")
        assert _ts_str(ts) == "2026-01-01 00:00:00"

    def test_no_tz_suffix(self):
        """Regression: old code would rfind('-') and cut into the date."""
        ts = pd.Timestamp("2026-12-31 23:59:59")
        assert _ts_str(ts) == "2026-12-31 23:59:59"


# ── STATUS_MAP ────────────────────────────────────────────────────────────

class TestStatusMap:
    def test_required_statuses_present(self):
        for key in ("Rest", "CC_DChg", "CC_Chg", "CV_Chg", "CV_DChg", "Pulse"):
            assert key in STATUS_MAP, f"Missing status: {key}"

    def test_all_values_are_chinese(self):
        for val in STATUS_MAP.values():
            assert val and any("\u4e00" <= c <= "\u9fff" for c in val)

    def test_no_nan_from_map(self):
        """Mapping a Series should not produce NaN for known statuses."""
        s = pd.Series(["CC_DChg", "CC_Chg", "Rest", "Pulse", "CV_Chg"])
        mapped = s.map(STATUS_MAP)
        assert not mapped.isna().any(), f"NaN in mapped values: {mapped.tolist()}"

    def test_fillna_fallback_for_unknown(self):
        """Unknown statuses should fall back to the raw string."""
        s = pd.Series(["CC_DChg", "UNKNOWN_STATUS"])
        mapped = s.map(STATUS_MAP).fillna(s)
        assert mapped.iloc[1] == "UNKNOWN_STATUS"


# ── _get_ndax_files / get_ndax_files ──────────────────────────────────────

class TestGetNdaxFiles:
    def test_finds_ndax_files(self, tmp_dir):
        (tmp_dir / "a.ndax").write_bytes(b"x")
        (tmp_dir / "b.ndax").write_bytes(b"x")
        (tmp_dir / "c.txt").write_text("x")
        result = _get_ndax_files(tmp_dir)
        assert len(result) == 2
        assert all(p.suffix == ".ndax" for p in result)

    def test_sorted(self, tmp_dir):
        (tmp_dir / "z.ndax").write_bytes(b"x")
        (tmp_dir / "a.ndax").write_bytes(b"x")
        result = _get_ndax_files(tmp_dir)
        assert result[0].name == "a.ndax"
        assert result[1].name == "z.ndax"

    def test_empty_dir(self, tmp_dir):
        assert _get_ndax_files(tmp_dir) == []

    def test_get_ndax_files_returns_strings(self, tmp_dir):
        (tmp_dir / "x.ndax").write_bytes(b"x")
        result = get_ndax_files(tmp_dir)
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)


# ── _build_record_sheet ───────────────────────────────────────────────────

class TestBuildRecordSheet:
    def test_headers_written(self, sample_df, tmp_path):
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_record_sheet(wb, sample_df, "test.ndax")
        wb.close()
        assert xlsx.exists()
        assert xlsx.stat().st_size > 0

    def test_capacity_cc_dchg(self, tmp_path):
        """CC_DChg rows should use Discharge_Capacity."""
        df = pd.DataFrame({
            "Cycle": [1], "Step": [1], "Status": ["CC_DChg"],
            "Current(mA)": [-500.0], "Voltage": [3.7],
            "Charge_Capacity(mAh)": [0.0],
            "Discharge_Capacity(mAh)": [100.0],
            "Timestamp": [_make_timestamp()],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_record_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Record", header=1)
        assert result.iloc[0]["容量(mAh)"] == pytest.approx(100.0, abs=0.001)

    def test_capacity_pulse(self, tmp_path):
        """Pulse rows should use chg - dchg."""
        df = pd.DataFrame({
            "Cycle": [1], "Step": [1], "Status": ["Pulse"],
            "Current(mA)": [200.0], "Voltage": [3.8],
            "Charge_Capacity(mAh)": [50.0],
            "Discharge_Capacity(mAh)": [10.0],
            "Timestamp": [_make_timestamp()],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_record_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Record", header=1)
        assert result.iloc[0]["容量(mAh)"] == pytest.approx(40.0, abs=0.001)

    def test_cc_chg_status(self, tmp_path):
        """CC_Chg rows should show Chinese label, not NaN."""
        df = pd.DataFrame({
            "Cycle": [1], "Step": [1], "Status": ["CC_Chg"],
            "Current(mA)": [500.0], "Voltage": [3.5],
            "Charge_Capacity(mAh)": [20.0],
            "Discharge_Capacity(mAh)": [0.0],
            "Timestamp": [_make_timestamp()],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_record_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Record", header=1)
        assert result.iloc[0]["工步类型"] == "恒流充电"


# ── _build_step_sheet ─────────────────────────────────────────────────────

class TestBuildStepSheet:
    def test_basic(self, sample_df, tmp_path):
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_step_sheet(wb, sample_df, "test.ndax")
        wb.close()
        assert xlsx.exists()

    def test_step_aggregation(self, tmp_path):
        """Step sheet should aggregate per cycle+step."""
        df = pd.DataFrame({
            "Cycle": [1, 1, 1, 1],
            "Step": [1, 1, 2, 2],
            "Status": ["CC_Chg", "CC_Chg", "CC_DChg", "CC_DChg"],
            "Current(mA)": [500, 500, -500, -500],
            "Voltage": [3.5, 3.6, 3.8, 3.7],
            "Charge_Capacity(mAh)": [0, 100, 0, 0],
            "Discharge_Capacity(mAh)": [0, 0, 0, 80],
            "Timestamp": [_make_timestamp(s=i) for i in range(4)],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_step_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Step", header=1)
        # CC_Chg step: chg_delta(100-0) - dchg_delta(0-0) = 100
        assert result.iloc[0]["容量(mAh)"] == pytest.approx(100.0, abs=0.01)
        # CC_DChg step: dchg_delta(80-0) = 80
        assert result.iloc[1]["容量(mAh)"] == pytest.approx(80.0, abs=0.01)

    def test_unknown_status_label(self, tmp_path):
        """Unknown status should show raw string, not KeyError."""
        df = pd.DataFrame({
            "Cycle": [1, 1], "Step": [1, 1], "Status": ["CV_Chg", "CV_Chg"],
            "Current(mA)": [200, 200], "Voltage": [4.2, 4.1],
            "Charge_Capacity(mAh)": [0, 50],
            "Discharge_Capacity(mAh)": [0, 0],
            "Timestamp": [_make_timestamp(s=0), _make_timestamp(s=30)],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_step_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Step", header=1)
        assert result.iloc[0]["工步类型"] == "恒压充电"


# ── _build_cycle_sheet ────────────────────────────────────────────────────

class TestBuildCycleSheet:
    def test_basic(self, sample_df, tmp_path):
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_cycle_sheet(wb, sample_df, "test.ndax")
        wb.close()
        assert xlsx.exists()

    def test_cycle_aggregation(self, tmp_path):
        """Cycle sheet should sum discharge deltas across steps in a cycle."""
        df = pd.DataFrame({
            "Cycle": [1, 1, 1, 1],
            "Step": [1, 1, 2, 2],
            "Status": ["CC_DChg", "CC_DChg", "CC_DChg", "CC_DChg"],
            "Current(mA)": [-500] * 4,
            "Voltage": [3.8, 3.7, 3.6, 3.5],
            "Charge_Capacity(mAh)": [0] * 4,
            "Discharge_Capacity(mAh)": [0, 50, 0, 120],
            "Timestamp": [_make_timestamp(s=i) for i in range(4)],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_cycle_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Cycle", header=1)
        # step1: 50-0=50, step2: 120-0=120, total=170
        assert result.iloc[0]["放电容量(mAh)"] == pytest.approx(170.0, abs=0.01)

    def test_time_format(self, tmp_path):
        """Timestamps should be formatted without microseconds/tz."""
        df = pd.DataFrame({
            "Cycle": [1, 1], "Step": [1, 1], "Status": ["CC_DChg", "CC_DChg"],
            "Current(mA)": [-500, -500], "Voltage": [3.7, 3.6],
            "Charge_Capacity(mAh)": [0, 0],
            "Discharge_Capacity(mAh)": [0, 100],
            "Timestamp": [_make_timestamp(s=0), _make_timestamp(s=30)],
        })
        xlsx = tmp_path / "test.xlsx"
        wb = xlsxwriter.Workbook(str(xlsx))
        _build_cycle_sheet(wb, df, "test.ndax")
        wb.close()
        result = pd.read_excel(str(xlsx), sheet_name="Cycle", header=1)
        time_str = str(result.iloc[0]["起始绝对时间(hh:mm:ss)"])
        # Should not contain microseconds
        assert "." not in time_str


# ── convert_one ───────────────────────────────────────────────────────────

class TestConvertOne:
    @patch("engine.converter.nda.read")
    def test_success(self, mock_read, tmp_path):
        mock_read.return_value = _sample_df()
        ndax = tmp_path / "test.ndax"
        ndax.write_bytes(b"fake")
        xlsx = tmp_path / "out.xlsx"
        result = convert_one(ndax, xlsx)
        assert Path(result).exists()
        mock_read.assert_called_once()

    @patch("engine.converter.nda.read")
    def test_default_output_path(self, mock_read, tmp_path):
        """Default output should be ../2_xlsx/stem.xlsx."""
        mock_read.return_value = _sample_df()
        ndax = tmp_path / "sub" / "test.ndax"
        ndax.parent.mkdir()
        ndax.write_bytes(b"fake")
        result = convert_one(ndax)
        expected = tmp_path / "2_xlsx" / "test.xlsx"
        assert Path(result) == expected
        assert expected.exists()

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            convert_one(tmp_path / "nonexistent.ndax")

    @patch("engine.converter.nda.read")
    def test_parse_error(self, mock_read, tmp_path):
        mock_read.side_effect = RuntimeError("corrupt file")
        ndax = tmp_path / "bad.ndax"
        ndax.write_bytes(b"fake")
        with pytest.raises(ValueError, match="Failed to parse"):
            convert_one(ndax, tmp_path / "out.xlsx")

    @patch("engine.converter.nda.read")
    def test_three_sheets_created(self, mock_read, tmp_path):
        """Should create Record, Step, and Cycle sheets."""
        mock_read.return_value = _sample_df()
        ndax = tmp_path / "test.ndax"
        ndax.write_bytes(b"fake")
        xlsx = tmp_path / "out.xlsx"
        convert_one(ndax, xlsx)
        xls = pd.ExcelFile(str(xlsx))
        assert set(xls.sheet_names) == {"Record", "Step", "Cycle"}

    @patch("engine.converter.nda.read")
    def test_constant_memory_mode(self, mock_read, tmp_path):
        """Verify xlsxwriter is opened with constant_memory=True."""
        mock_read.return_value = _sample_df()
        ndax = tmp_path / "test.ndax"
        ndax.write_bytes(b"fake")
        xlsx = tmp_path / "out.xlsx"
        with patch("engine.converter.xlsxwriter.Workbook") as mock_wb:
            mock_wb_inst = MagicMock()
            mock_wb.return_value = mock_wb_inst
            convert_one(ndax, xlsx)
            mock_wb.assert_called_once()
            call_args = mock_wb.call_args
            # Check constant_memory=True in kwargs or second positional arg
            found = False
            if "constant_memory" in (call_args.kwargs or {}):
                found = call_args.kwargs["constant_memory"] is True
            elif len(call_args.args) > 1 and isinstance(call_args.args[1], dict):
                found = call_args.args[1].get("constant_memory") is True
            assert found, f"constant_memory not set: {call_args}"


# ── convert_batch ─────────────────────────────────────────────────────────

class TestConvertBatch:
    @patch("engine.converter.convert_one")
    def test_basic(self, mock_convert, tmp_path, use_threads_for_batch):
        mock_convert.return_value = "/tmp/out.xlsx"
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        for name in ("a.ndax", "b.ndax"):
            (input_dir / name).write_bytes(b"fake")
        result = convert_batch(input_dir)
        assert len(result["success"]) == 2
        assert len(result["skipped"]) == 0
        assert len(result["failed"]) == 0

    @patch("engine.converter.convert_one")
    def test_skip_existing(self, mock_convert, tmp_path):
        mock_convert.return_value = "/tmp/out.xlsx"
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.ndax").write_bytes(b"fake")
        # Pre-create the output xlsx
        out_dir = tmp_path / "2_xlsx"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "a.xlsx").write_bytes(b"fake")
        result = convert_batch(input_dir)
        assert "a.ndax" in result["skipped"]
        mock_convert.assert_not_called()

    @patch("engine.converter.convert_one")
    def test_callbacks_invoked(self, mock_convert, tmp_path, use_threads_for_batch):
        mock_convert.return_value = "/tmp/out.xlsx"
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.ndax").write_bytes(b"fake")
        (input_dir / "b.ndax").write_bytes(b"fake")

        skipped, succeeded, failed = [], [], []
        progresses = []

        result = convert_batch(
            input_dir,
            on_skip=lambda n: skipped.append(n),
            on_success=lambda n: succeeded.append(n),
            on_fail=lambda n, e: failed.append((n, e)),
            on_progress=lambda c, t: progresses.append((c, t)),
        )
        assert len(succeeded) == 2
        assert len(progresses) == 2
        assert progresses[-1][0] == 2  # final progress = 2

    @patch("engine.converter.convert_one")
    def test_cancel_stops_processing(self, mock_convert, tmp_path, use_threads_for_batch):
        mock_convert.return_value = "/tmp/out.xlsx"
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        for name in ("a.ndax", "b.ndax", "c.ndax"):
            (input_dir / name).write_bytes(b"fake")

        call_count = 0
        def cancel_after_one():
            nonlocal call_count
            call_count += 1
            return call_count > 1  # cancel after first file check

        result = convert_batch(input_dir, is_cancelled=cancel_after_one)
        total = len(result["success"]) + len(result["skipped"]) + len(result["failed"])
        assert total <= 1

    @patch("engine.converter.convert_one")
    def test_failure_reported(self, mock_convert, tmp_path, use_threads_for_batch):
        mock_convert.side_effect = RuntimeError("parse error")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "bad.ndax").write_bytes(b"fake")
        result = convert_batch(input_dir)
        assert len(result["failed"]) == 1
        assert "bad.ndax" in result["failed"][0][0]

    def test_empty_dir(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        result = convert_batch(input_dir)
        assert result == {"success": [], "skipped": [], "failed": []}

    @patch("engine.converter.convert_one")
    def test_result_type_is_convertresult(self, mock_convert, tmp_path, use_threads_for_batch):
        mock_convert.return_value = "/tmp/out.xlsx"
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.ndax").write_bytes(b"fake")
        result = convert_batch(input_dir)
        assert isinstance(result, dict)
        assert "success" in result
        assert "skipped" in result
        assert "failed" in result

    @patch("engine.converter.convert_one")
    def test_max_workers_passed(self, mock_convert, tmp_path, use_threads_for_batch):
        mock_convert.return_value = "/tmp/out.xlsx"
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.ndax").write_bytes(b"fake")
        convert_batch(input_dir, max_workers=1)
        mock_convert.assert_called_once()


# ── Headers ───────────────────────────────────────────────────────────────

class TestHeaders:
    def test_record_headers(self):
        assert len(RECORD_HEADERS) == 5
        assert "循环序号" in RECORD_HEADERS

    def test_step_headers(self):
        assert len(STEP_HEADERS) == 3

    def test_cycle_headers(self):
        assert len(CYCLE_HEADERS) == 4
