from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, TypedDict

import NewareNDA as nda
import numpy as np
import pandas as pd
import xlsxwriter


# ---------- types ----------

class ConvertResult(TypedDict):
    """Result of a batch conversion."""
    success: list[str]
    skipped: list[str]
    failed: list[tuple[str, str]]


# Status mapping from NewareNDA to Chinese display names
STATUS_MAP: dict[str, str] = {
    "Rest": "搁置",
    "CC_DChg": "恒流放电",
    "CC_Chg": "恒流充电",
    "CV_Chg": "恒压充电",
    "CV_DChg": "恒压放电",
    "Pulse": "脉冲",
}

RECORD_HEADERS = ["循环序号", "工步类型", "电流(A)", "电压(V)", "容量(mAh)"]
STEP_HEADERS = ["循环序号", "工步类型", "容量(mAh)"]
CYCLE_HEADERS = ["循环序号", "起始绝对时间(hh:mm:ss)", "结束绝对时间(hh:mm:ss)", "放电容量(mAh)"]


# ---------- helpers ----------

def _ts_str(ts: Any) -> str:
    """Format a timestamp as 'YYYY-MM-DD HH:MM:SS' (no microseconds/tz)."""
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _get_ndax_files(input_dir: Path) -> list[Path]:
    """Get all .ndax files in a directory, sorted by name."""
    return sorted(input_dir.glob("*.ndax"))


def _calc_capacity(statuses: np.ndarray, chg: np.ndarray, dchg: np.ndarray) -> np.ndarray:
    """Compute per-row capacity from charge/discharge columns.

    Logic:
      - CC_DChg → discharge capacity (dchg)
      - other   → net charge: chg - dchg
    """
    return np.where(statuses == "CC_DChg", dchg, chg - dchg).round(6)


def _write_sheet_header(ws: xlsxwriter.Worksheet, filename: str, headers: list[str]) -> None:
    """Write the standard 2-row header (filename + column names)."""
    ws.write_row(0, 0, [filename])
    ws.write_row(1, 0, headers)


# ---------- sheet builders ----------

def _build_record_sheet(wb: xlsxwriter.Workbook, df: pd.DataFrame, filename: str) -> None:
    """Build the Record sheet with all individual data points."""
    ws = wb.add_worksheet(name="Record")
    _write_sheet_header(ws, filename, RECORD_HEADERS)

    cycles = df["Cycle"].values
    steps = df["Status"].map(STATUS_MAP).fillna(df["Status"]).values
    currents = (df["Current(mA)"].values / 1000.0).round(9)
    voltages = df["Voltage"].values.round(4)
    capacities = _calc_capacity(
        df["Status"].values,
        df["Charge_Capacity(mAh)"].values,
        df["Discharge_Capacity(mAh)"].values,
    )

    for i, row_data in enumerate(zip(cycles, steps, currents, voltages, capacities), start=2):
        ws.write_row(i, 0, row_data)


def _build_step_sheet(wb: xlsxwriter.Workbook, df: pd.DataFrame, filename: str) -> None:
    """Build the Step sheet with aggregated data per cycle+step."""
    ws = wb.add_worksheet(name="Step")
    _write_sheet_header(ws, filename, STEP_HEADERS)

    row = 2
    for (cycle, _step, status), grp in df.groupby(["Cycle", "Step", "Status"], sort=False):
        first = grp.iloc[0]
        last = grp.iloc[-1]
        chg_delta = last["Charge_Capacity(mAh)"] - first["Charge_Capacity(mAh)"]
        dchg_delta = last["Discharge_Capacity(mAh)"] - first["Discharge_Capacity(mAh)"]
        # Same logic as _calc_capacity: CC_DChg → dchg; else → chg - dchg
        cap = dchg_delta if status == "CC_DChg" else chg_delta - dchg_delta

        label = STATUS_MAP.get(status, status)
        ws.write_row(row, 0, [cycle, label, round(float(cap), 3)])
        row += 1


def _build_cycle_sheet(wb: xlsxwriter.Workbook, df: pd.DataFrame, filename: str) -> None:
    """Build the Cycle sheet with aggregated data per cycle."""
    ws = wb.add_worksheet(name="Cycle")
    _write_sheet_header(ws, filename, CYCLE_HEADERS)

    # Pre-compute per-step discharge deltas to avoid df.copy() on full frame
    step_dchg = df.groupby(["Cycle", "Step"], sort=False)["Discharge_Capacity(mAh)"]
    step_delta = step_dchg.transform(lambda x: x.iloc[-1] - x.iloc[0])

    row = 2
    for cycle, cyc_mask in df.groupby("Cycle", sort=True):
        # Sum the first delta of each step within this cycle
        cyc_delta = step_delta[cyc_mask.index]
        cyc_steps = df.loc[cyc_mask.index, "Step"]
        total_dchg = cyc_delta.groupby(cyc_steps).first().sum()

        ts = df.loc[cyc_mask.index, "Timestamp"]
        ws.write_row(row, 0, [
            cycle,
            _ts_str(ts.iloc[0]),
            _ts_str(ts.iloc[-1]),
            round(float(total_dchg), 3),
        ])
        row += 1


# ---------- public API ----------

def convert_one(ndax_path: str | Path, xlsx_path: str | Path | None = None) -> str:
    """Convert a single .ndax file to .xlsx.

    Args:
        ndax_path: Path to the .ndax file.
        xlsx_path: Output path. If None, derived by replacing extension
                   and placing in ../2_xlsx/ directory.

    Returns:
        Path to the generated .xlsx file.

    Raises:
        FileNotFoundError: ndax_path does not exist.
        ValueError: File parsing failed.
    """
    ndax_path = Path(ndax_path)
    if not ndax_path.exists():
        raise FileNotFoundError(f"NDAX file not found: {ndax_path}")

    if xlsx_path is None:
        out_dir = ndax_path.parent.parent / "2_xlsx"
        out_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = out_dir / f"{ndax_path.stem}.xlsx"
    else:
        xlsx_path = Path(xlsx_path)
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        df = nda.read(str(ndax_path), software_cycle_number=False)
    except Exception as e:
        raise ValueError(f"Failed to parse {ndax_path}: {e}") from e

    filename = ndax_path.name
    wb = xlsxwriter.Workbook(str(xlsx_path), {"constant_memory": True})
    _build_cycle_sheet(wb, df, filename)
    _build_step_sheet(wb, df, filename)
    _build_record_sheet(wb, df, filename)
    wb.close()
    return str(xlsx_path)


def convert_batch(
    input_dir: str | Path,
    max_workers: int | None = None,
    *,
    on_skip: Callable[[str], None] | None = None,
    on_success: Callable[[str], None] | None = None,
    on_fail: Callable[[str, str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ConvertResult:
    """Convert all .ndax files in a directory using parallel processes.

    Args:
        input_dir: Directory containing .ndax files.
        max_workers: Number of parallel worker processes. Defaults to CPU count.
        on_skip: Optional callback(filename) when a file is skipped.
        on_success: Optional callback(filename) when a file converts successfully.
        on_fail: Optional callback(filename, error) when a file fails.
        on_progress: Optional callback(current, total) after each file.
        is_cancelled: Optional callback() returning True to abort.

    Returns:
        {'success': [file...], 'skipped': [file...], 'failed': [(file, error)...]}
    """
    input_dir = Path(input_dir)
    ndax_files = _get_ndax_files(input_dir)
    out_dir = input_dir.parent / "2_xlsx"
    out_dir.mkdir(parents=True, exist_ok=True)

    result: ConvertResult = {"success": [], "skipped": [], "failed": []}
    total = len(ndax_files)
    done_count = 0

    # Phase 1: check existing
    pending: list[tuple[Path, Path]] = []
    for ndax_path in ndax_files:
        if is_cancelled and is_cancelled():
            return result

        xlsx_path = out_dir / f"{ndax_path.stem}.xlsx"
        if xlsx_path.exists():
            result["skipped"].append(ndax_path.name)
            done_count += 1
            if on_skip:
                on_skip(ndax_path.name)
            if on_progress:
                on_progress(done_count, total)
        else:
            pending.append((ndax_path, xlsx_path))

    if not pending or (is_cancelled and is_cancelled()):
        return result

    # Phase 2: parallel conversion
    max_workers = max_workers or os.cpu_count()
    executor = ProcessPoolExecutor(max_workers=max_workers)
    try:
        fut_map = {
            executor.submit(convert_one, ndax_path, xlsx_path): ndax_path
            for ndax_path, xlsx_path in pending
        }
        for future in as_completed(fut_map):
            if is_cancelled and is_cancelled():
                for f in fut_map:
                    f.cancel()
                break

            ndax_path = fut_map[future]
            try:
                future.result()
                result["success"].append(ndax_path.name)
                if on_success:
                    on_success(ndax_path.name)
            except Exception as e:
                result["failed"].append((ndax_path.name, str(e)))
                if on_fail:
                    on_fail(ndax_path.name, str(e))

            done_count += 1
            if on_progress:
                on_progress(done_count, total)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return result


def get_ndax_files(directory: str | Path) -> list[str]:
    """Get all .ndax file names in a directory (non-recursive)."""
    return [p.name for p in _get_ndax_files(Path(directory))]
