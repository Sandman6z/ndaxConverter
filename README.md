# NDAX → XLSX 批量转换器

将 Neware（新威）BTS 电池测试系统生成的 `.ndax` 二进制数据文件，批量转换为 `.xlsx` 格式，供 `battery-analysis` 数据分析软件使用。

## 功能特性

- ✅ 批量转换 NDAX 文件为 XLSX 格式
- ✅ 自动跳过已存在的文件
- ✅ 多进程并行转换，提升效率
- ✅ 实时进度显示和日志记录
- ✅ 可取消的转换过程

## 系统要求

- Python 3.10+
- Windows 10/11

## 安装

```bash
# 克隆项目
git clone <repository-url>
cd ndaxConverter

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
.venv\Scripts\activate

# 安装依赖
pip install -e .
```

## 使用方法

### 图形界面

```bash
python main.py
```

1. 点击"📁 选择文件夹"选择包含 `.ndax` 文件的目录
2. 点击"▶ 开始转换"开始批量转换
3. 转换完成后，XLSX 文件将保存在同级 `2_xlsx` 目录

### 命令行接口

```python
from engine.converter import convert_one, convert_batch, get_ndax_files

# 转换单个文件
xlsx_path = convert_one("path/to/file.ndax")

# 批量转换
result = convert_batch("path/to/directory/")
print(result)  # {'success': [...], 'skipped': [...], 'failed': [...]}
```

## 输出格式

转换后的 XLSX 文件包含三个 Sheet：

1. **Record** - 逐条记录（循环序号、工步类型、电流、电压、容量）
2. **Step** - 按工步聚合（循环序号、工步类型、容量）
3. **Cycle** - 按循环聚合（循环序号、开始/结束时间、放电容量）

## 项目结构

```
ndaxConverter/
├── engine/              # 核心引擎（无 GUI 依赖）
│   ├── __init__.py
│   └── converter.py     # 转换逻辑
├── ui/                  # GUI 层（PyQt6）
│   ├── __init__.py
│   └── main_window.py   # 主窗口
├── main.py              # 入口
├── pyproject.toml       # 项目配置
└── README.md            # 本文件
```

## 与 battery-analysis 集成

`engine/` 模块设计为无 GUI 依赖，可直接被 `battery-analysis` 项目导入：

```python
from ndax_converter.engine.converter import convert_one, convert_batch

# 在 battery-analysis 中调用
result = convert_batch("path/to/ndax/directory/")
```

## 许可证

MIT License
