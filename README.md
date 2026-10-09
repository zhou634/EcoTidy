# EcoTidy

> 生态野外多源监测数据全自动清洗与标准化分析系统
> 适用于**野外样方调查**、**传感器连续监测**、**室内控制实验**三类数据的
> 清洗 → 指标计算 → 出图 → 报告导出。

完全离线的 Windows 单机程序：**不联网、不上传任何数据、不需要账号**。
界面为白色单色调 + 直角风格，支持**中英文实时切换**。

---

## 它能解决什么问题

野外数据的通病是"格式杂、脏、算起来烦"：Excel 表头写法五花八门、
缺失值和异常值混在一起、多样性指数和重要值要在多个表之间倒来倒去、
出图还要单独装一堆软件。

EcoTidy 把这条链路做成一条固定流程，五步走完：

```
① 导入数据 → ② 清洗数据 → ③ 计算指标 → ④ 绘制图表 → ⑤ 导出报告
```

- **导入**：csv / xlsx / txt 都能读，自动识别数据类型与列名，
  带单位（`树高（m）`）、英文（`TreeHeight`）都认；认不出来就弹列映射让你手工指定，不会卡死。
- **清洗**：按生态学规则判异常（不是简单的 3σ）、缺失值按「同样方 → 同物种 → 整列」
  三级回退填补，**结构性空白**（灌木本来就没有胸径）绝不用其它物种的值顶替；
  每一步改动都写进清洗日志，可直接引到论文方法部分。
- **计算**：群落结构（密度/频度/优势度/重要值）、多样性（Shannon/Simpson/Pielou/Margalef）、
  环境时序统计、土壤指标、实验生态指标。
- **绘图**：按数据特征推荐图表，灰阶／彩色／**纯黑白（底纹区分系列）** 三种配色，
  导出 PNG（300dpi 可投稿）与 SVG（矢量）。
- **导出**：数据表、指标表、清洗日志、图表、分析报告一次性打包导出。

## 快速开始

```
1. 下载并解压整个文件夹（不要只复制 exe）
2. 双击 EcoTidy.exe
3. 点【载入示例数据】先看完整效果，再导入自己的数据
```

想确认软件在这台机器上是否正常：

```bat
EcoTidy.exe --selftest
```

它会跑一遍完整流程（读数据 → 清洗 → 计算 → 绘图 → 导出 → 建界面），
并把逐项结果写到 `%APPDATA%\EcoTidy\temp\EcoTidy_自检结果.txt`。

## 数据要求

**样方群落数据**——必需三列：

| 样方号 | 物种 | 株数 |
|---|---|---|
| Q1 | 油松 | 12 |
| Q1 | 辽东栎 | 8 |

选填测量列（有就一起导入、清洗、导出，没有不影响计算）：

| 列 | 说明 |
|---|---|
| 胸径 | 乔木胸高直径，**参与优势度计算** |
| 盖度 | 覆盖百分比，`0~100` 或 `0~1` 都可，自动统一量纲 |
| 树高 | 树高 |
| 基径 | 基部直径，常用于灌丛 |
| 冠幅 | 树冠幅度 |

**传感器时序数据**——时间列 + 至少一个数值列（气温／相对湿度／土壤温度等）。

**普通实验统计**——处理组 / 重复 + 自定义数值指标列。

列名不必一模一样，带单位、带括号、中英文都能识别；识别不了可以手工映射。

## 项目结构

```
EcoTidy/
├── main.py                 入口（含 --selftest 自检模式）
├── core/                   业务核心，不含任何界面代码
│   ├── data_parse.py       解析与列名识别
│   ├── data_clean.py       清洗规则与日志
│   ├── eco_index.py        生态指标计算
│   ├── plot_draw.py        绘图
│   ├── report_make.py      报告与 Excel 导出
│   ├── app_state.py        全局状态（页面间唯一通信通道）
│   └── settings.py         偏好持久化
├── ui/                     界面层
│   ├── main_window.py      主窗口、菜单、语言切换
│   ├── page_import.py      ① 数据导入
│   ├── page_clean.py       ② 智能清洗
│   ├── page_calc.py        ③ 指标计算
│   ├── page_plot.py        ④ 可视化绘图
│   ├── page_export.py      ⑤ 报告导出
│   └── widgets/            步骤条、空状态、帮助面板、图标等
├── utils/
│   ├── paths.py            路径与软件标识的唯一来源
│   ├── i18n.py             中英文翻译引擎
│   ├── i18n_strings.py     词条表（300+ 条）
│   └── errors.py           全局异常钩子与中文提示
├── database/               SQLite 历史任务
├── static/                 随包只读资源
│   ├── demo/               内置示例数据
│   ├── fonts/              思源黑体（图表中文，OFL 1.1）
│   ├── help/               帮助内容
│   └── EcoTidy.ico         应用图标
├── tests/                  481 项自动化测试
└── release/                打包说明与图标生成脚本
```

**架构约定**：页面之间**只通过 `core/app_state.py` 的 `AppState` 单例 + `data_changed` 信号**
通信，禁止跨页面互相 import。这样任何页面都能独立测试、独立替换。

## 技术栈

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.9 ~ 3.11 | 实测 3.9.13；3.12+ 不支持 |
| PySide6 | 6.5.3 | 界面框架 |
| pandas | 2.0.3 | 数据处理 |
| numpy | 1.24.4 | 数值计算 |
| matplotlib | 3.7.3 | 绘图 |
| openpyxl | 3.1.2 | Excel 读写 |
| pytest | 7.4.4 | 测试 |
| pyinstaller | 5.13.2 | 打包 |

依赖清单见 [`requirements.txt`](requirements.txt)，中文说明见
[`requirements-notes.md`](requirements-notes.md)。

```bat
python -m pip install -r requirements.txt
python main.py
```

## 开发与测试

```bat
:: 全量测试
python -m pytest tests/ -q

:: 静态检查（未定义名字会被当成测试失败，见 tests/test_ui_behaviour.py）
python -m pyflakes main.py core ui utils database

:: 不显示界面，跑一遍完整链路
python main.py --selftest
```

测试覆盖：解析（含各种脏表头）、清洗规则、每个生态指标的公式、
绘图与配色、报告生成、路径回退、SQLite、中英文词条完整性与界面切换、
以及若干"回归防线"（例如禁止再写出会被 `except` 吞掉的未定义名字）。

## 打包发布

见 [`release/打包说明.md`](release/打包说明.md)，含可直接复制的 PyInstaller 命令。

两个容易踩的坑已写在文档里：

1. matplotlib 的导出后端是**运行时动态加载**的，不加 `--hidden-import` 打包后
   导出 SVG 会直接报 `ModuleNotFoundError`；
2. 应用图标要用 `--icon`，否则 exe 用的是 PyInstaller 默认图标。

图标由代码生成（`release/make_icon.py`），改绘制逻辑后重跑即可同步。

## 数据存放在哪

| 内容 | 位置 |
|---|---|
| 原始数据文件 | **软件从不修改，只读取** |
| 历史任务与设置 | `%APPDATA%\EcoTidy`（换电脑时一并带走） |
| 导出成果 | 默认「我的文档\EcoTidy」，可自选 |
| 出错日志 | `%APPDATA%\EcoTidy\temp\error.log` |

若 `%APPDATA%` 不可写（受管控的电脑），程序会自动回退到其它可写目录，
并在界面顶部提示实际位置——这是刻意设计，不是缺陷。

## 已知无害现象

- 控制台可能出现 `QPainter::end: Painter ended with N saved states`：
  这是 Qt 给 `QSpinBox` 画样式时的内部缺陷（已实测确认与本项目代码无关），
  不影响显示与功能，打包后没有控制台窗口，用户看不到。

## 许可

本项目代码采用 [MIT 许可](LICENSE) —— 可自由使用、修改、分发（含商业用途），
只需保留版权声明。

随包分发的 **思源黑体 / Noto Sans SC** 采用
[SIL Open Font License 1.1](https://scripts.sil.org/OFL)，允许随软件再分发
（含商业分发），详见 [`static/fonts/README.txt`](static/fonts/README.txt)。

> 注意：`static/fonts/` 里**不得**放入 Windows 自带的微软雅黑、黑体、宋体等字体，
> 这类字体的授权不允许随第三方软件再分发。
