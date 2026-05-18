# NDAX → XLSX 批量转换器 — 设计文档

## 1. 概述

将 Neware（新威）BTS 电池测试系统生成的 `.ndax` 二进制数据文件，批量转换为 `.xlsx` 格式，供 `battery-analysis` 数据分析软件使用。

## 2. 架构

```
ndax_converter/
├── engine/              # 核心引擎（无 GUI 依赖，后续可直接导入 battery-analysis）
│   ├── __init__.py
│   └── converter.py     # 读取 NDAX → 生成 XLSX
├── ui/                  # GUI 层（PyQt6）
│   ├── __init__.py
│   └── main_window.py   # 主窗口
├── main.py              # 入口
└── requirements.txt
```

**关键设计原则**：`engine/` 层不依赖任何 GUI 框架，后续可直接作为 Python 模块被 `battery-analysis` 项目导入调用。

## 3. NDAX 文件结构解析

`.ndax` 文件本质是一个 ZIP 压缩包，包含以下内部文件：

| 内部文件名 | 说明 |
|---|---|
| `TestInfo.xml` | 测试元数据（设备、通道、起始时间等），GB2312 编码 |
| `VersionInfo.xml` | 版本信息 |
| `Step.xml` | 工步定义（充电、放电、静置、脉冲参数） |
| `data.ndc` | 主测试数据（二进制，逐条记录） |
| `data_step.ndc` | 工步级数据 |
| `data_runInfo.ndc` | 运行信息 |
| `data_log.ndc` | 日志数据 |
| `data_es.ndc` | 事件/统计数据 |

## 4. 依赖

- Python 3.10+
- `NewareNDA` — 解析 NDAX 二进制格式，返回 pandas DataFrame
- `pandas` — 数据聚合处理
- `openpyxl` — 生成 XLSX 文件
- `PyQt6` — GUI 界面

## 5. 核心转换逻辑 (engine/converter.py)

### 5.1 输入/输出

- **输入**：单个 `.ndax` 文件路径
- **输出**：单个 `.xlsx` 文件（同名，扩展名替换为 `.xlsx`）

### 5.2 数据读取

```python
import NewareNDA as nda

df = nda.read(ndax_path, software_cycle_number=False)
```

返回的 DataFrame 包含以下列：

| 列名 | 类型 | 说明 |
|---|---|---|
| `Index` | uint32 | 记录序号 |
| `Cycle` | uint32 | 循环序号 |
| `Step` | uint32 | 步骤序号 |
| `Step_Index` | uint32 | 步骤内记录序号 |
| `Status` | category | 工步类型：`Rest`, `CC_DChg`, `Pulse` |
| `Time` | float32 | 步骤内耗时（秒） |
| `Voltage` | float32 | 电压（V） |
| `Current(mA)` | float32 | 电流（mA） |
| `Charge_Capacity(mAh)` | float32 | 充电容量累积 |
| `Discharge_Capacity(mAh)` | float32 | 放电容量累积 |
| `Charge_Energy(mWh)` | float32 | 充电能量累积 |
| `Discharge_Energy(mWh)` | float32 | 放电能量累积 |
| `Timestamp` | datetime64 | 绝对时间戳 |

### 5.3 Status 映射表

| NewareNDA 值 | XLSX 中显示 |
|---|---|
| `Rest` | 搁置 |
| `CC_DChg` | 恒流充电 |
| `Pulse` | 脉冲 |

### 5.4 三个 Sheet 的生成

#### Sheet 1: Record（逐条记录）

| 列 | 位置 | 数据来源 |
|---|---|---|
| 文件名 | Row 1, Col A | 原始 .ndax 文件名（不含路径） |
| `循环序号` | Row 2, Col A | 静态文本 |
| `工步` | Row 2, Col B | 静态文本 |
| `电流(A)` | Row 2, Col C | 静态文本 |
| `电压(V)` | Row 2, Col D | 静态文本 |
| `容量(mAh)` | Row 2, Col E | 静态文本 |
| 数据 | Row 3+ | 逐条写入 |

字段计算：

```
循环序号 = df['Cycle']
工步     = map(df['Status'], 映射表)
电流(A)  = df['Current(mA)'] / 1000
电压(V)  = df['Voltage']
容量(mAh) = df['Charge_Capacity(mAh)'] - df['Discharge_Capacity(mAh)']
```

#### Sheet 2: Step（按循环+工步聚合）

按 `(Cycle, Step, Status)` 分组聚合，每组取一条汇总记录：

| 列 | 数据来源 |
|---|---|
| 循环序号 | `Cycle` |
| 工步 | `Status`（映射为中文） |
| 容量(mAh) | net_capacity 最后一条 - 第一条 |

```
net_capacity = Charge_Capacity - Discharge_Capacity
step_capacity = groupby(['Cycle', 'Step', 'Status']) 的 net_capacity 差值
```

#### Sheet 3: Cycle（按循环聚合）

按 `Cycle` 分组聚合：

| 列 | 数据来源 |
|---|---|
| 循环序号 | `Cycle` |
| 开始充电时间(hh:mm:ss) | 该 Cycle 内第一条 `Status='CC_DChg'` 记录的 `Timestamp`，格式 `YYYY-MM-DD hh:mm:ss` |
| 结束充电时间(hh:mm:ss) | 该 Cycle 内最后一条 `Status='CC_DChg'` 记录的 `Timestamp` |
| 放电容量(mAh) | 该 Cycle 内 `Discharge_Capacity(mAh)` 的最大值 |

### 5.5 文件命名与输出目录

- **输入目录**：用户选择的文件夹（含 `.ndax` 文件），例如 `...\BattData_normal\1_ndax\`
- **输出目录**：输入目录的同级 `2_xlsx\` 文件夹，即 `...\BattData_normal\2_xlsx\`，**自动创建**
- **文件名**：与 `.ndax` 同名，仅扩展名改为 `.xlsx`
- **已存在处理**：自动跳过已存在的 `.xlsx` 文件（不覆盖）

### 5.6 批量处理流程

```
1. 扫描输入目录，获取所有 *.ndax 文件列表
2. 创建输出目录（如不存在）
3. 对每个文件：
   a. 检查目标 .xlsx 是否已存在 → 跳过
   b. NewareNDA 读取 .ndax → DataFrame
   c. 创建 ExcelWriter
   d. 写入 Record Sheet（逐条）
   e. 聚合写入 Step Sheet
   f. 聚合写入 Cycle Sheet
   g. 保存并关闭
4. 汇总统计：成功/跳过/失败数量
```

## 6. GUI 界面设计 (ui/main_window.py)

### 6.1 窗口布局

```
┌──────────────────────────────────────────────────┐
│  NDAX → XLSX 批量转换器                   [_][□][X] │
├──────────────────────────────────────────────────┤
│                                                   │
│  [📁 选择文件夹]  ┌──────────────────────────┐    │
│                   │ C:\...\BattData\1_ndax\   │    │
│                   └──────────────────────────┘    │
│                                                   │
│  ┌──────────────────────────────────────────┐     │
│  │ 文件名                       状态        │     │
│  │ ──────────────────────────────────────── │     │
│  │ BTS83_40_5_1_....ndax        就绪        │     │
│  │ BTS83_40_5_2_....ndax        就绪        │     │
│  │ BTS83_40_5_3_....ndax        就绪        │     │
│  │ ...                                     │     │
│  └──────────────────────────────────────────┘     │
│                                                   │
│  [▶ 开始转换]                                     │
│  ████████████░░░░░░ 80%    (3/5)                  │
│                                                   │
│  日志:                                            │
│  [2025-06-10 10:01] 开始批量转换...                │
│  [2025-06-10 10:02] ✔ BTS83_40_5_1 转换成功       │
│  [2025-06-10 10:02] ⏭ BTS83_40_5_2 已存在跳过     │
│                                                   │
└──────────────────────────────────────────────────┘
```

### 6.2 交互逻辑

1. 点击 **"选择文件夹"** → 弹出 `QFileDialog` 选择目录
2. 自动扫描所有 `*.ndax` 文件（不递归子目录），显示在文件列表中
3. 每个文件显示：文件名、大小、状态（就绪/已存在/成功/失败）
4. 点击 **"开始转换"** → 逐个处理，进度条实时更新
5. 处理完成后弹出完成提示

### 6.3 关键状态与提示

- 文件列表中以颜色标识状态：灰色=已存在跳过，绿色=成功，红色=失败
- 处理中途可取消（下次重新运行会跳过已生成的文件）
- 日志区域实时显示转换详情

## 7. 入口 (main.py)

```python
import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
```

## 8. 后续合并到 battery-analysis 的接口

当需要将转换功能合并到主项目时，`engine/converter.py` 提供以下接口：

```python
def convert_one(ndax_path: str, xlsx_path: str | None = None) -> str:
    """转换单个 NDAX 文件。
    
    Args:
        ndax_path: .ndax 文件路径
        xlsx_path: 输出 .xlsx 路径，None 则自动在 ../2_xlsx/ 生成
    
    Returns:
        生成的 .xlsx 路径
    
    Raises:
        FileNotFoundError: ndax_path 不存在
        ValueError: 文件解析失败
    """

def convert_batch(input_dir: str) -> dict:
    """批量转换目录下所有 .ndax 文件。
    
    Args:
        input_dir: 包含 .ndax 文件的目录
    
    Returns:
        {'success': [file...], 'skipped': [file...], 'failed': [(file, error)...]}
    """

def get_ndax_files(directory: str) -> list[str]:
    """获取目录下所有 .ndax 文件列表。"""
```

主项目只需 `from ndax_converter.engine.converter import convert_one, convert_batch` 即可调用。

## 9. 注意事项

1. **NewareNDA 库版本**：目前最新版 `2026.2.2`，建议固定版本
2. **编码问题**：NDAX 内部 XML 使用 GB2312 编码，NewareNDA 已处理
3. **性能**：单个 NDAX 约 50 万条记录，生成 Record sheet 时建议使用 `write_rows` 逐批写入而非逐行，避免内存暴涨
4. **时区**：Timestamp 含 `+08:00` 时区信息，XLSX 中写入本地时间字符串即可
5. **电流单位**：NewareNDA 默认 mA，XLSX 中需转为 A（÷1000）
