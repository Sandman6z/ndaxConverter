import sys
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QTextEdit, QFileDialog, QMessageBox,
    QLabel,
)

from engine.converter import convert_one, get_ndax_files


class ConvertWorker(QThread):
    progress = pyqtSignal(int, int)  # current, total
    log = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)

    def __init__(self, ndax_files: list[Path], out_dir: Path):
        super().__init__()
        self.ndax_files = ndax_files
        self.out_dir = out_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.ndax_files)
        result: dict = {"success": [], "skipped": [], "failed": []}

        for i, ndax_path in enumerate(self.ndax_files):
            if self._cancelled:
                self.log.emit("⚠ 已取消转换")
                break

            xlsx_path = self.out_dir / f"{ndax_path.stem}.xlsx"
            if xlsx_path.exists():
                result["skipped"].append(ndax_path.name)
                self.log.emit(f"⏭ {ndax_path.name} 已存在，跳过")
                self.progress.emit(i + 1, total)
                continue

            try:
                convert_one(ndax_path, xlsx_path)
                result["success"].append(ndax_path.name)
                self.log.emit(f"✔ {ndax_path.name} 转换成功")
            except Exception as e:
                result["failed"].append((ndax_path.name, str(e)))
                self.log.emit(f"✘ {ndax_path.name} 转换失败: {e}")

            self.progress.emit(i + 1, total)

        self.finished_signal.emit(result)


class MainWindow(QMainWindow):
    COL_NAME = 0
    COL_STATUS = 1

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDAX → XLSX 批量转换器")
        self.setMinimumSize(700, 500)

        self.input_dir: Path | None = None
        self.out_dir: Path | None = None
        self.ndax_files: list[Path] = []
        self.worker: ConvertWorker | None = None

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Folder selection row
        folder_row = QHBoxLayout()
        self.btn_select = QPushButton("📁 选择文件夹")
        self.btn_select.clicked.connect(self._select_folder)
        self.edit_path = QLineEdit()
        self.edit_path.setReadOnly(True)
        self.edit_path.setPlaceholderText("选择包含 .ndax 文件的文件夹...")
        folder_row.addWidget(self.btn_select)
        folder_row.addWidget(self.edit_path)
        layout.addLayout(folder_row)

        # File list
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["文件名", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Start button + status summary
        bottom_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ 开始转换")
        self.btn_start.clicked.connect(self._start_convert)
        self.btn_start.setEnabled(False)
        self.lbl_summary = QLabel("")
        bottom_row.addWidget(self.btn_start)
        bottom_row.addWidget(self.lbl_summary, stretch=1)
        layout.addLayout(bottom_row)

        # Log area
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)
        layout.addWidget(self.log_area)

    def _select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 NDAX 文件夹")
        if not folder:
            return

        self.input_dir = Path(folder)
        self.out_dir = self.input_dir.parent / "2_xlsx"
        self.edit_path.setText(str(self.input_dir))

        self.ndax_files = [self.input_dir / p for p in get_ndax_files(folder)]
        self._refresh_file_list()
        self.btn_start.setEnabled(len(self.ndax_files) > 0)
        self.log_area.clear()

    def _refresh_file_list(self):
        self.table.setRowCount(len(self.ndax_files))
        for i, f in enumerate(self.ndax_files):
            name_item = QTableWidgetItem(f.name)
            self.table.setItem(i, self.COL_NAME, name_item)

            xlsx_path = self.out_dir / f"{f.stem}.xlsx" if self.out_dir else None
            if xlsx_path and xlsx_path.exists():
                status_item = QTableWidgetItem("已存在")
                status_item.setForeground(Qt.GlobalColor.gray)
            else:
                status_item = QTableWidgetItem("就绪")
            self.table.setItem(i, self.COL_STATUS, status_item)

        self.lbl_summary.setText(f"共 {len(self.ndax_files)} 个文件")

    def _update_status_ui(self, converting: bool):
        self.btn_select.setEnabled(not converting)
        self.btn_start.setEnabled(not converting and len(self.ndax_files) > 0)
        self.progress_bar.setVisible(converting)

    def _start_convert(self):
        if not self.input_dir or not self.out_dir:
            return

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log_area.clear()
        self.log_area.append(f"[{_now()}] 开始批量转换...")

        # Update all "就绪" items to "转换中..."
        for i in range(self.table.rowCount()):
            item = self.table.item(i, self.COL_STATUS)
            if item and item.text() == "就绪":
                item.setText("等待中...")

        self._update_status_ui(True)
        self.progress_bar.setMaximum(len(self.ndax_files))
        self.progress_bar.setValue(0)

        self.worker = ConvertWorker(self.ndax_files, self.out_dir)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self.log_area.append)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

        self.btn_start.setText("■ 取消")
        self.btn_start.clicked.disconnect()
        self.btn_start.clicked.connect(self._cancel_convert)

    def _cancel_convert(self):
        if self.worker:
            self.worker.cancel()
        self.btn_start.setEnabled(False)
        self.btn_start.setText("正在取消...")

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)
        # Update file list status
        if current > 0 and current <= self.table.rowCount():
            item = self.table.item(current - 1, self.COL_STATUS)
            if item and item.text() == "等待中...":
                item.setText("转换中...")

    def _on_finished(self, result: dict):
        self._update_status_ui(False)
        self.btn_start.setText("▶ 开始转换")
        self.btn_start.clicked.disconnect()
        self.btn_start.clicked.connect(self._start_convert)
        self._refresh_file_list()

        succ = len(result["success"])
        skip = len(result["skipped"])
        fail = len(result["failed"])
        self.lbl_summary.setText(f"成功 {succ}  跳过 {skip}  失败 {fail}")

        self.log_area.append(f"[{_now()}] 转换完成: 成功 {succ}, 跳过 {skip}, 失败 {fail}")

        if fail > 0:
            QMessageBox.warning(
                self, "转换完成",
                f"转换完成\n✔ 成功: {succ}\n⏭ 跳过: {skip}\n✘ 失败: {fail}"
            )
        else:
            QMessageBox.information(
                self, "转换完成",
                f"全部完成!\n✔ 成功: {succ}\n⏭ 跳过: {skip}"
            )


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def run():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
