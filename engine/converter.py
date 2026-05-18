from pathlib import Path
import numpy as np
import NewareNDA as nda
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment

STATUS_MAP = {
    "Rest": "搁置",
    "CC_DChg": "恒流放电",
    "Pulse": "脉冲",
}

# In the Record sheet, Pulse rows display as 搁置 (matching BTSDA behavior)
RECORD_STATUS_MAP = {
    "Rest": "搁置",
    "CC_DChg": "恒流放电",
    "Pulse": "搁置",
}

HEADER_FILENAME_COL = 1

RECORD_HEADERS = ["循环序号", "工步类型", "电流(A)", "电压(V)", "容量(mAh)"]
STEP_HEADERS = ["循环序号", "工步类型", "容量(mAh)"]
CYCLE_HEADERS = ["循环序号", "起始绝对时间(hh:mm:ss)", "结束绝对时间(hh:mm:ss)", "放电容量(mAh)"]

COL_WIDTHS = {
    "Record": {"A": 10, "B": 10, "C": 10, "D": 10, "E": 10},
    "Step": {"A": 10, "B": 10, "C": 10},
    "Cycle": {"A": 10, "B": 23, "C": 23, "D": 14},
}

NUMBER_FORMATS = {
    ("Record", 3): "0.000000000",
    ("Record", 4): "0.0000",
    ("Record", 5): "0.000000",
    ("Step", 3): "0.000",
    ("Cycle", 4): "0.000",
}


def _ts_str(ts) -> str:
    """Format a timestamp without microseconds and timezone offset."""
    s = str(ts)
    # Chop microseconds and timezone
    idx = s.find(".")
    if idx != -1:
        s = s[:idx]
    # Remove +08:00 type timezone suffix
    if "+" in s:
        s = s[: s.rfind("+")]
    elif "-" in s[10:]:  # only look after date portion for TZ
        s = s[: s.rfind("-")]
    return s


def _get_ndax_files(input_dir: Path) -> list[Path]:
    return sorted(input_dir.glob("*.ndax"))


def _write_sheet_header(ws, headers: list[str], filename: str):
    ws.cell(1, 1, filename)
    for i, h in enumerate(headers, 1):
        ws.cell(2, i, h)


def _build_record_sheet(wb: Workbook, name: str, df: pd.DataFrame, filename: str):
    ws = wb.create_sheet(title=name)
    _write_sheet_header(ws, RECORD_HEADERS, filename)

    for col_letter, width in COL_WIDTHS["Record"].items():
        ws.column_dimensions[col_letter].width = width

    cycles = df["Cycle"].values
    steps = df["Status"].map(RECORD_STATUS_MAP).values
    currents = (df["Current(mA)"].values / 1000.0).round(9)
    voltages = df["Voltage"].values.round(4)

    # Capacity formula varies by Status (matching BTSDA behavior):
    #   CC_DChg → Discharge_Capacity (positive)
    #   Pulse    → Charge - Discharge (net, can be negative)
    #   Rest     → 0
    statuses = df["Status"].values
    chg = df["Charge_Capacity(mAh)"].values
    dchg = df["Discharge_Capacity(mAh)"].values
    capacities = np.where(
        statuses == "CC_DChg", dchg,
        np.where(statuses == "Pulse", chg - dchg, 0.0)
    ).round(6)

    rows = zip(cycles, steps, currents, voltages, capacities)
    for i, row_data in enumerate(rows, 3):
        for j, val in enumerate(row_data, 1):
            cell = ws.cell(i, j, val)
            fmt = NUMBER_FORMATS.get(("Record", j))
            if fmt:
                cell.number_format = fmt


def _build_step_sheet(wb: Workbook, name: str, df: pd.DataFrame, filename: str):
    ws = wb.create_sheet(title=name)
    _write_sheet_header(ws, STEP_HEADERS, filename)

    for col_letter, width in COL_WIDTHS["Step"].items():
        ws.column_dimensions[col_letter].width = width

    row_idx = 3
    for (cycle, step, status), grp in df.groupby(["Cycle", "Step", "Status"], sort=False):
        first = grp.iloc[0]
        last = grp.iloc[-1]
        chg_delta = last["Charge_Capacity(mAh)"] - first["Charge_Capacity(mAh)"]
        dchg_delta = last["Discharge_Capacity(mAh)"] - first["Discharge_Capacity(mAh)"]

        if status == "CC_DChg":
            cap = dchg_delta
        else:
            cap = chg_delta - dchg_delta

        ws.cell(row_idx, 1, cycle)
        ws.cell(row_idx, 2, STATUS_MAP[status])
        cell = ws.cell(row_idx, 3, round(float(cap), 3))
        cell.number_format = "0.000"
        row_idx += 1


def _build_cycle_sheet(wb: Workbook, name: str, df: pd.DataFrame, filename: str):
    ws = wb.create_sheet(title=name)
    _write_sheet_header(ws, CYCLE_HEADERS, filename)

    for col_letter, width in COL_WIDTHS["Cycle"].items():
        ws.column_dimensions[col_letter].width = width

    row_idx = 3
    for cycle, grp in df.groupby("Cycle", sort=True):
        total_dchg = 0.0
        for _, step_grp in grp.groupby("Step", sort=True):
            dchg_delta = (
                step_grp["Discharge_Capacity(mAh)"].iloc[-1]
                - step_grp["Discharge_Capacity(mAh)"].iloc[0]
            )
            total_dchg += dchg_delta

        ws.cell(row_idx, 1, cycle)
        ws.cell(row_idx, 2, _ts_str(grp["Timestamp"].iloc[0]))
        ws.cell(row_idx, 3, _ts_str(grp["Timestamp"].iloc[-1]))
        cell = ws.cell(row_idx, 4, round(total_dchg, 3))
        cell.number_format = "0.000"
        row_idx += 1


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
    wb = Workbook()
    wb.remove(wb.active)  # Remove default sheet

    _build_cycle_sheet(wb, "Cycle", df, filename)
    _build_step_sheet(wb, "Step", df, filename)
    _build_record_sheet(wb, "Record", df, filename)

    wb.save(str(xlsx_path))
    return str(xlsx_path)


def convert_batch(input_dir: str | Path) -> dict:
    """Convert all .ndax files in a directory.

    Args:
        input_dir: Directory containing .ndax files.

    Returns:
        {'success': [file...], 'skipped': [file...], 'failed': [(file, error)...]}
    """
    input_dir = Path(input_dir)
    ndax_files = _get_ndax_files(input_dir)
    out_dir = input_dir.parent / "2_xlsx"
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict = {"success": [], "skipped": [], "failed": []}

    for ndax_path in ndax_files:
        xlsx_path = out_dir / f"{ndax_path.stem}.xlsx"
        if xlsx_path.exists():
            result["skipped"].append(ndax_path.name)
            continue

        try:
            convert_one(ndax_path, xlsx_path)
            result["success"].append(ndax_path.name)
        except Exception as e:
            result["failed"].append((ndax_path.name, str(e)))

    return result


def get_ndax_files(directory: str | Path) -> list[str]:
    """Get all .ndax file names in a directory (non-recursive)."""
    return [p.name for p in _get_ndax_files(Path(directory))]
