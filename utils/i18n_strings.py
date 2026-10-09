# -*- coding: utf-8 -*-
"""
中英词条表（键 = 源码里的中文原文，值 = 英文）

维护约定：
    · 只放**用户看得见**的文案：界面标题、按钮、提示、报错、帮助正文；
    · **绝不放数据列名**（"相对密度"、"H 香农指数"…）。列名是数据契约，
      英文界面下导出的表头仍须是中文，否则用户对不上自己的数据；
    · 报错文案遵守 "一句话 + How to fix" 结构，英文同样如此，不做长篇解释；
    · 新增文案若暂时没词条，英文界面会回落中文，不会崩；
      用 utils.i18n.missing_translations() 可以列出漏翻的条目。
"""

from __future__ import annotations

from typing import Dict

STRINGS: Dict[str, str] = {
    # =======================================================================
    # 通用
    # =======================================================================
    "确定": "OK",
    "取消": "Cancel",
    "关闭": "Close",
    "知道了": "Got it",
    "是": "Yes",
    "否": "No",
    "保存": "Save",
    "打开": "Open",
    "删除": "Delete",
    "重命名": "Rename",
    "刷新": "Refresh",
    "搜索": "Search",
    "提示": "Note",
    "说明": "Note",
    "原因": "Cause",
    "怎么办": "How to fix",
    "例如": "e.g.",
    "共": "Total",
    "行": "rows",
    "列": "cols",
    # 注意：不要给"个""条"这类量词登记空字符串。空值会让 tr() 返回空串，
    # 直接把句子里的量词抹掉（实测踩坑）。量词已在完整句子里逐条翻译，
    # 这里不登记，未登记时自动回落中文。
    "已取消": "Cancelled",
    "已完成": "Done",
    "未完成": "Not finished",

    # =======================================================================
    # 菜单与主窗口
    # =======================================================================
    # 注意：菜单标题带快捷键标记 (&F)，菜单项带省略号 …，
    #       词条键必须与源码里的字符串**逐字一致**，否则查不到（实测踩坑）。
    "文件(&F)": "File(&F)",
    "视图(&V)": "View(&V)",
    "帮助(&H)": "Help(&H)",
    "文件": "File",
    "编辑": "Edit",
    "视图": "View",
    "帮助": "Help",
    "打开文件…": "Open file…",
    "打开文件": "Open file",
    "载入示例数据": "Load sample data",
    "导出成果": "Export results",
    "退出": "Exit",
    "上一个页面": "Previous page",
    "下一个页面": "Next page",
    "使用手册（F1）": "User guide (F1)",
    "术语表": "Glossary",
    "指标公式速查": "Metric formulas",
    "关于": "About",
    "离线单机版": "Offline edition",
    "分析流程": "Workflow",
    "当前为示例数据，可随时切换为自己的文件":
        "Sample data is loaded — switch to your own files anytime",
    "查看本页相关帮助": "Help for this page",
    "选择要导入的数据文件": "Choose the data file to import",
    "使用软件自带的示例数据": "Use the built-in sample data",
    "批量导出图表、数据表与分析报告": "Export charts, tables and the report in one go",
    "退出软件（自动记住窗口大小与设置）": "Quit (window size and settings are remembered)",
    "跳转到【{0}】页面": "Go to the {0} page",
    "回到上一个流程页面": "Back to the previous step",
    "前往下一个流程页面": "Go to the next step",
    "打开帮助面板：快速上手、数据要求、常见问题": "Open help: quick start, data requirements, FAQ",
    "生态学术语速查": "Ecology glossary",
    "全部指标公式一览": "All metric formulas at a glance",
    "软件用途与版本信息": "Purpose and version information",
    "历史任务…": "History…",
    "历史任务": "History",
    "关于": "About",
    "打开日志文件夹": "Open log folder",
    "帮助与术语速查（F1）": "Help & glossary (F1)",
    "各步骤数据量": "Row counts by step",
    "查看以前的分析任务并恢复现场": "Browse and restore past tasks",
    "准备就绪": "Ready",
    "尚无数据": "No data yet",
    "语言": "Language",
    "切换界面语言": "Switch interface language",
    "界面语言已切换为 {0}": "Interface language switched to {0}",
    "导入 → 清洗 → 计算 → 出图 → 导出": "Import → Clean → Metrics → Charts → Export",

    # 步骤条悬浮说明（一句话说清这一步产出什么）
    "导入 csv / xlsx / txt 文件。产出：标准化数据表。":
        "Import csv / xlsx / txt files. Output: a standardised table.",
    "修正异常值与缺失值。产出：可计算的干净数据。":
        "Fix outliers and missing values. Output: clean data ready for metrics.",
    "计算群落结构、多样性、时序指标。产出：指标表。":
        "Compute structure, diversity and time-series metrics. Output: metric tables.",
    "选图表并导出图片。产出：PNG / SVG 文件。":
        "Pick a chart and export it. Output: PNG / SVG files.",
    "一次性导出全部成果。产出：成果文件夹。":
        "Export everything at once. Output: a results folder.",
    "\n\n· 已完成，当前停在这一步": "\n\n· Done — you are on this step",
    "\n\n· 已完成，点击可回看": "\n\n· Done — click to review",
    "\n\n· 当前所在步骤": "\n\n· Current step",
    "\n\n· 尚未开始，点击可直接前往": "\n\n· Not started — click to go",

    # 页面副标题（做什么 / 需要什么 / 产出什么）
    "支持 csv / xlsx / txt 文件，可多选、也可直接把文件拖进窗口。需要：你的原始数据文件（或点【载入示例数据】直接用软件自带的例子）。产出：标准化后的原始数据表。":
        "Reads csv / xlsx / txt files — multiple selection or drag-and-drop. "
        "Needs: your raw data files (or load the built-in sample). Output: a standardised table.",
    "检查并修正数据中的异常值与缺失值。需要：已导入的原始数据。产出：可直接用于指标计算的干净数据表。":
        "Detects and fixes outliers and missing values. "
        "Needs: imported raw data. Output: clean data ready for metrics.",
    "计算群落结构、多样性与环境时序统计指标。需要：清洗后的数据。产出：行级指标表与汇总指标表。":
        "Computes community structure, diversity and time-series metrics. "
        "Needs: cleaned data. Output: row-level and summary metric tables.",
    "按数据类型选择图表并导出高清图片。需要：指标计算结果或清洗后的数据。产出：PNG / SVG 图片。":
        "Choose a chart for your data type and export high-resolution images. "
        "Needs: metrics or cleaned data. Output: PNG / SVG images.",
    "把数据表、指标表、清洗日志、图片与报告一次性导出。需要：至少完成前面任一步骤。产出：完整成果文件夹与成果说明。":
        "Exports tables, metrics, the log, charts and the report in one go. Output: a results folder.",

    # 步骤条
    # 注意：下面既有"② 清洗数据"这种整体形式，也有"清洗"这种单词形式。
    # 后者是必需的 —— tr("第 {0} 步：{1}", 2, "清洗") 会先翻译参数再拼装，
    # 只登记整体形式会出现 "Step 2: 清洗" 这种半截英文（实测踩坑）。
    "导入": "Import",
    "清洗": "Clean",
    "计算": "Metrics",
    "绘图": "Charts",
    "导出": "Export",
    "一键完成全部分析": "Run everything",
    # 各步的短名（英文界面用短标签，否则步骤条与导航栏会挤爆 —— 实测 83 处溢出）
    "① 导入数据": "1 Import",
    "② 清洗数据": "2 Clean",
    "③ 计算指标": "3 Metrics",
    "④ 绘制图表": "4 Charts",
    "⑤ 导出报告": "5 Export",
    "1. 数据导入": "1. Import",
    "2. 智能清洗": "2. Clean",
    "3. 指标计算": "3. Metrics",
    "4. 可视化绘图": "4. Charts",
    "5. 报告导出": "5. Export",
    "第 {0} 步：{1}": "Step {0}: {1}",
    "第 {0} 步 / 共 5 步：{1}": "Step {0} of 5: {1}",
    "下一步：导入数据": "Next: import",
    "下一步：清洗数据": "Next: clean",
    "下一步：计算指标": "Next: metrics",
    "下一步：绘制图表": "Next: charts",
    "下一步：导出报告": "Next: export",
    "尚未开始，点击可直接前往": "Not started — click to go",
    "当前所在步骤": "Current step",
    "点击可直接前往该步骤": "Click to jump to this step",

    # =======================================================================
    # 数据导入页
    # =======================================================================
    "数据导入": "Import",
    "待导入的文件（数据类型已自动识别，若不对可以直接在下拉框里改）":
        "Files to import (type detected automatically — change it in the dropdown if wrong)",
    "还没有数据。可以把 csv / xlsx / txt 文件直接拖到这里，或点击下方按钮选择文件。":
        "No data yet. Drag csv / xlsx / txt files here, or click a button below.",
    "暂无可导出的成果，请先完成前面的步骤。":
        "Nothing to export yet.",
    "选择文件…": "Choose files…",
    "载入示例数据（Ctrl+L）": "Load sample data (Ctrl+L)",
    "清空列表": "Clear list",
    "列映射…": "Column mapping…",
    "确认导入": "Import",
    "一键完成全部分析": "Run everything",
    "选择文件": "Choose files",
    "可多选同类型文件（Ctrl+O）": "Multiple files of the same type (Ctrl+O)",
    "用内置示例数据试跑（Ctrl+L）": "Try the built-in sample (Ctrl+L)",
    "清空已选文件": "Clear the file list",
    "手工指定列名对应关系": "Map columns manually",
    "解析并生成标准化数据表（Ctrl+R）": "Parse and build a standardised table (Ctrl+R)",
    "自动跑完清洗 → 计算 → 出图 → 导出": "Run clean → metrics → charts → export automatically",
    "不对可在此改": "Change here if wrong",
    "请先选择文件，或点【载入示例数据】": "Choose files first, or load the sample data",
    "请先导入数据": "Import data first",
    "前往【数据导入】": "Go to Import",
    "正在解析 {0} 个文件，请稍候…": "Parsing {0} file(s)…",
    "解析完成：共 {0} 行 × {1} 列。": "Parsed: {0} rows × {1} columns.",
    "已加入 {0} 个文件{1}。确认无误后点【确认导入】。":
        "Added {0} file(s){1}. Click Import when ready.",
    "文件解析完成，但没有得到任何数据行。": "The file parsed successfully but contains no data rows.",
    "文件可能只有表头，或所有行都是空行。": "It may have only a header row, or all rows are blank.",
    "用 Excel 打开确认数据是否存在后重新导入。":
        "Open it in Excel to check the data, then import again.",
    "还没有选择文件": "No files selected",
    "请先点【选择文件…】选择要导入的数据文件，或点【载入示例数据】直接使用示例。":
        "Click Choose files… to pick your data, or load the built-in sample data.",
    "选中的文件类型不一致": "Selected files have different data types",
    "不同数据类型的文件不能合并成一张表。": "Files of different data types cannot be merged into one table.",
    "怎么办：请二选一 —— ① 只保留同一类型的文件；② 分两批导入，先分析一类。":
        "How to fix: either keep files of one type, or import them in two batches.",
    "列映射": "Column mapping",
    "重新自动识别": "Auto-detect again",
    "恢复自动识别结果": "Restore auto-detection result",

    # =======================================================================
    # 智能清洗页
    # =======================================================================
    "智能清洗": "Clean",
    "启用生态异常值检测": "Detect ecological outliers",
    "启用缺失值填充": "Fill missing values",
    "启用自动去重": "Remove duplicate rows",
    "高级设置 ▸": "Advanced ▸",
    "高级设置": "Advanced",
    "一键执行清洗": "Run cleaning",
    "查看清洗日志": "View cleaning log",
    "保存清洗后数据": "Save cleaned data",
    "前往【智能清洗】": "Go to Clean",
    "负值、超范围值，以及时序单点突变": "Negative or out-of-range values, and single-point spikes",
    "时序插值，样方按分组中位数填补": "Time-series interpolation; quadrat data by group median",
    "删除完全重复的行，保留首条": "Drop exact duplicate rows, keep the first",
    "突变阈值、最大插值间隙等（默认即可）": "Spike threshold, max gap, etc. (defaults are fine)",
    "按当前选项清洗（Ctrl+R）": "Clean with current options (Ctrl+R)",
    "查看清洗说明与明细": "View the cleaning log and details",
    "另存为 csv 或 xlsx": "Save as csv or xlsx",
    "0 = 不插值": "0 = no interpolation",
    "逐条说明改了什么、为什么": "Explains what changed and why",
    "导出修改明细为 csv": "Export the change list as csv",
    "清洗前": "Before",
    "清洗后": "After",
    "清洗日志": "Cleaning log",
    "修改明细": "Change details",
    "行号": "Row",
    "列名": "Column",
    "原值": "Original",
    "处理方式": "Action",
    "请先导入数据。": "Import data first.",
    "清洗需要先有原始数据。": "Cleaning needs raw data first.",
    "数据本身是干净的，没有需要修改的地方。":
        "The data is already clean — nothing to change.",

    # =======================================================================
    # 指标计算页
    # =======================================================================
    "指标计算": "Metrics",
    "群落结构指标": "Community structure",
    "多样性指标": "Diversity",
    "环境时序统计": "Environment time series",
    "土壤指标": "Soil",
    "实验生态指标": "Experiment",
    "执行指标计算": "Compute metrics",
    "前往【指标计算】": "Go to Metrics",
    "要计算哪些指标：": "Metrics to compute:",
    "样方面积、昼夜划分等（默认即可）": "Quadrat area, day/night split, etc. (defaults are fine)",
    "按勾选的指标组计算（Ctrl+R）": "Compute checked groups (Ctrl+R)",
    "仅影响密度指标": "Affects density only",
    "样方面积": "Quadrat area",
    "行级指标结果": "Row-level metrics",
    "汇总指标结果": "Summary metrics",
    "请先完成数据清洗。": "Finish cleaning first.",
    "指标计算需要先完成数据清洗。": "Metrics need cleaned data first.",
    "密度、频度、优势度、重要值（每行 = 某样方中的某物种）":
        "Density, frequency, dominance, importance value (one row = one species in one quadrat)",
    "物种数、香农指数、辛普森指数、均匀度（每样方一行）":
        "Richness, Shannon, Simpson, evenness (one row per quadrat)",
    "均值、极值、昼夜均值差（需时间列）": "Mean, extremes, day/night difference (needs a time column)",
    "质量／体积含水率（需鲜重、干重列）": "Gravimetric / volumetric water content (needs fresh & dry weight)",
    "萌发率、萌发势、凋落物分解速率（需相应实验字段）":
        "Germination rate, germination energy, litter decay rate (needs experiment fields)",

    # =======================================================================
    # 可视化绘图页
    # =======================================================================
    "可视化绘图": "Charts",
    "绘制图表": "Draw chart",
    "导出图片": "Export images",
    "① 数据来源：": "1 Source:",
    "② 图表类型：": "2 Chart type:",
    "③ 分类 / X 轴：": "3 Category / X axis:",
    "④ 数值 / Y 轴（可多选）：": "4 Values / Y axis (multi-select):",
    "⑤ 配色方案：": "5 Colour scheme:",
    "⑥ 输出格式：": "6 Output format:",
    "先选表，再选字段": "Pick a table, then the fields",
    "默认已按数据类型推荐": "A sensible default is already chosen",
    "横轴分类，如样方号、物种、日期": "X-axis categories, e.g. quadrat, species, date",
    "可多选": "Multi-select",
    "绘制图表（Ctrl+R）": "Draw chart (Ctrl+R)",
    "按所选格式导出，自动带时间戳、不覆盖": "Exports in the chosen format; timestamped, never overwrites",
    "灰阶（默认）／彩色／纯黑白（底纹区分）": "Grayscale (default) / colour / pure black-and-white (hatched)",
    "PNG 位图 300dpi／SVG 矢量图／两者都要": "PNG 300dpi / SVG vector / both",
    "还没有可绘制的数据，请先完成指标计算；也可以直接用清洗后的数据绘图。":
        "No data to plot yet. Compute metrics first, or plot the cleaned data directly.",
    "请先准备数据": "Prepare data first",
    "还没有图可以导出": "Nothing to export yet",
    "请先点【绘制图表】，看到图之后再导出。":
        "Click Draw chart first, then export.",
    "还没有选择输出格式": "No output format selected",
    "请在「⑥ 输出格式」里选择要导出的格式（PNG、SVG 或两者都要）。":
        "Choose an output format under step 6 (PNG, SVG or both).",
    "选择图片保存位置": "Choose where to save the images",
    "灰阶": "Grayscale",
    "彩色": "Colour",
    "纯黑白": "Black & white",
    "PNG + SVG（推荐）": "PNG + SVG (recommended)",
    "仅 PNG 位图（300dpi）": "PNG only (300dpi)",
    "仅 SVG 矢量图": "SVG only",
    "导出成功": "Export complete",
    "打开输出文件夹": "Open output folder",

    # =======================================================================
    # 报告导出页
    # =======================================================================
    "报告导出": "Export",
    "一键导出": "Export all",
    "选择文件夹…": "Choose folder…",
    "任务名称：": "Task name:",
    "输出文件夹：": "Output folder:",
    "要导出的成果：": "Export:",
    "用于文件命名": "Used in file names",
    "导出勾选的成果（Ctrl+E）": "Export checked (Ctrl+E)",
    "请先导入并清洗数据": "Import and clean data first",
    "清洗数据表": "Cleaned data",
    "行级指标表": "Row metrics",
    "汇总指标表": "Summary",
    "绘图图片": "Charts",
    "分析报告": "Report",
    "暂无可导出的成果": "Nothing to export yet",
    "还没有勾选成果项": "No items checked",
    "还没有选择输出文件夹": "No output folder chosen",
    "导出完成": "Export complete",
    "导出失败": "Export failed",

    # =======================================================================
    # 历史任务
    # =======================================================================
    "载入选中任务": "Load selected task",
    "重新指定文件…": "Pick files again…",
    "还没有历史任务。完成一次分析后会自动记录在这里。":
        "No history yet. Tasks are recorded automatically after an analysis.",
    "这里保存着以前的分析任务。双击某一行即可把当时的数据与结果重新载入软件，继续分析或重新导出。":
        "Past analyses are listed below. Double-click a row to reload its data and results.",
    "输入任务名或数据类型的关键词…": "Search task name or data type…",
    "请先在列表里选择一条任务": "Select a task in the list first",
    "载入该任务（也可双击列表）": "Load this task (or double-click the row)",
    "删除记录与存档，不动原始文件": "Delete the record and snapshot; your data files are untouched",
    "存档丢失时重新选择数据文件": "Re-pick the data file when the snapshot is gone",
    "存档已丢失，请用【重新指定文件…】": "Snapshot missing — use Pick files again…",
    "任务名": "Task",
    "创建时间": "Created",
    "数据类型": "Data type",
    "原始行数": "Raw rows",
    "清洗后行数": "Cleaned rows",
    "存档状态": "Snapshot",
    "可恢复": "Available",
    "存档已丢失": "Missing",
    "确认删除这条历史任务？": "Delete this task?",
    "该任务的存档已丢失": "This task's snapshot is missing",
    "请先选择任务": "Select a task first",
    "重命名任务": "Rename task",
    "重命名失败": "Rename failed",
    "删除失败": "Delete failed",
    "任务名不能为空": "Task name cannot be empty",
    "请输入一个名字后再确定。": "Enter a name, then confirm.",
    "新的任务名：": "New task name:",

    # =======================================================================
    # 帮助面板
    # =======================================================================
    "帮助与术语": "Help & glossary",
    "快速上手": "Quick start",
    "三步走完整个流程（约 3 分钟）": "Three steps to a result (~3 minutes)",
    "数据格式要求": "Data format requirements",
    "生态学术语速查": "Ecology glossary",
    "常见问题": "FAQ",
    "附录与版次": "Appendix",
    "快捷键一览": "Keyboard shortcuts",
    "功能": "Function",
    "术语": "Term",
    "怎么算": "Formula",
    "怎么理解": "Meaning",
    "指标": "Metric",
    "公式": "Formula",
    "快捷键": "Shortcut",

    # =======================================================================
    # 启动、更名与通用弹窗
    # =======================================================================
    "欢迎使用": "Welcome",
    "离线生态数据清洗与分析工具": "Offline ecology data cleaning & analysis tool",
    "五步完成一份分析成果：": "Five steps to a finished analysis:",
    "① 导入　② 清洗　③ 计算指标　④ 出图　⑤ 导出报告":
        "1 Import　2 Clean　3 Metrics　4 Charts　5 Export",
    "不确定从哪开始？点【载入示例数据】试试。":
        "Not sure where to start? Try Load sample data.",
    "载入示例数据，先看效果": "Load sample data and see",
    "我自己有数据，直接开始": "I have my own data",
    "软件已更名为 {0}": "This app is now called {0}",
    "打开旧目录": "Open old folder",
    "还在读取数据": "Still reading data",
    "正在读取数据文件，现在关闭会中断这次读取（已导入的数据不受影响）。":
        "A file is still being read. Closing now will cancel it (already imported data is safe).",
    "要关闭软件吗？": "Close the app?",
    "全部分析已完成": "All steps completed",
    "自动分析已停止": "Automatic run stopped",
    "数据内容不符合预期，无法继续计算。":
        "The data is not in the expected shape, so the calculation cannot continue.",
    "可能存在空数据表、整列都是空白、或该列不是数值格式。":
        "The table may be empty, a column may be all blank, or a column is not numeric.",
    "回到【智能清洗】页检查该列数据；若整列为空，请从数据表中删除该列后重新导入。":
        "Check that column on the Clean page; if it is entirely empty, remove it and import again.",
    "功能暂时用不了": "Temporarily unavailable",

    # =======================================================================
    # 导出说明（成果说明 / 报告）
    # =======================================================================
    "成果说明": "What's in this folder",
    "分析报告": "Analysis report",
    "用途：": "Purpose: ",
    "文件：": "Files:",
    "怎么打开这些文件": "How to open these files",
    "任务名称": "Task name",
    "导出时间": "Exported at",
    "导出位置": "Location",
    "数据基本信息": "Data overview",
    "清洗统计摘要": "Cleaning summary",
    "生态指标汇总": "Ecological metrics",
    "数据质量评价": "Data quality",
    "报告结束。本报告由软件自动生成，数值均可回溯至同目录下的数据表与日志文件。":
        "End of report. Generated automatically; all figures trace back to the data tables and logs in this folder.",

    # =======================================================================
    # 顶部提示条与面板脚注
    # =======================================================================
    "提示：{0}。历史任务与设置都保存在这个位置。":
        "Note: {0}. History and settings live here.",
    "提示：系统「我的文档」不可写，输出文件夹已自动设为 {0}，可以点【选择文件夹…】改成其它位置。":
        "Note: your Documents folder is not writable, so output goes to {0}. "
        "Use Choose folder… to change it.",
    "提示：每个页面右上角的「?」可以直接打开该页对应的帮助章节。":
        "Tip: the ? at the top-right of each page opens its help section.",
    "提示：": "Note: ",
    "提示：数据保存在 {0}，历史任务与设置也在这里。":
        "Note: data is stored in {0}; history and settings live there too.",
    "说明：": "Note:",

    # =======================================================================
    # 清洗页 / 计算页的细节文案
    # =======================================================================
    "查看：": "View:",
    "显示进入清洗前的原始数据": "Show the raw data before cleaning",
    "显示清洗后的数据，被修改的单元格用浅灰色标出":
        "Show the cleaned data; modified cells are shaded",
    "只看被修改的单元格": "Show only modified cells",
    "只列出被修改过的单元格，便于逐条核对":
        "List only the cells that changed",
    "请先在【智能清洗】页完成数据清洗": "Finish cleaning first",
    "日": "Day",
    "时": "Hour",
    "每行 = 一个样方": "One row per quadrat",
    "每行 = 某样方中的某物种": "One row per species per quadrat",
    # 带「?」问号图标的参数标签（源码里带 HTML span，键必须逐字一致）
    "样方面积（m²） <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Quadrat area (m²) ?",
    "每个样方的实际面积，只用于计算密度 = 株数 ÷ 样方面积。默认 1。\n例如 10 m × 10 m 的样方请填 100。填多少不影响其它指标。":
        "Area of one quadrat. Used only for density = plants ÷ area. "
        "Default 1; for a 10 m × 10 m quadrat enter 100.",
    "白天开始时刻 <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Day starts ?",
    "昼夜均值差按这里的划分计算。默认 6 点。":
        "Day/night difference uses these hours. Default 06:00.",
    "白天结束时刻 <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Day ends ?",
    "默认 18 点（即 06:00–18:00 为白天，其余为夜间）。":
        "Default 18:00 (06:00–18:00 counts as day).",
    "时序统计分组 <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Time-series grouping ?",
    "环境时序统计按「日」还是「时」汇总。默认按日。":
        "Aggregate the time series by day or by hour. Default: day.",
    "萌发势统计天数 <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Germination energy day ?",
    "萌发势取第几天的累计萌发数。默认 7 天。\n只影响实验生态指标里的「萌发势」一项。":
        "Which day to count cumulative germination on. Default 7. "
        "Affects only germination energy.",
    # 导出页与成果说明
    "用于文件命名，例如「2024年6月样方调查」": "Used in file names, e.g. June 2024 survey",
    "默认「我的文档」下的 EcoTidy 文件夹": "Defaults to EcoTidy in your Documents folder",
    "搜索：": "Search:",

    # =======================================================================
    # 高级设置里的参数说明与导出成果的具体说明
    # =======================================================================
    "请先在【数据导入】页载入数据": "Load data on the Import page first",
    "高级设置（不确定就用默认值）": "Advanced (defaults are fine)",
    "突变检测阈值（倍 MAD） <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Spike threshold (× MAD) ?",
    "偏离当日中位数超过几倍 MAD 才算异常。默认 3。\n调小会更严格（可能把真实极值也改掉），调大会更宽松。\n软件用 MAD 而不是标准差，因为生态数据不正态，用标准差会误杀季节性峰值。":
        "How far from the daily median counts as a spike, in MAD units. Default 3. "
        "Lower is stricter, higher is looser. MAD is used instead of standard "
        "deviation because ecological data is not normally distributed.",
    "最大插值间隙（个点） <span style='color:#5A5F66;font-weight:bold'>?</span>":
        "Max interpolation gap (points) ?",
    "数据中断不超过几个点时才自动补值。默认 3。\n缺口更长时保留空白，避免凭空造数据。设为 0 表示完全不插值。":
        "Fill gaps no longer than this. Default 3; longer gaps stay blank rather "
        "than inventing data. Set 0 to disable interpolation.",
    "逐条列出被修改的单元格（点击表头可排序）：":
        "Every modified cell (click a header to sort):",
    "导出修改明细…": "Export change list…",
    "显示清洗后的数据，被修改的单元格用浅橙色标出":
        "Show the cleaned data; modified cells are shaded",
    # 导出成果说明（与 core/report_make.py 的 ITEM_PURPOSE 逐字一致）
    "清洗后的数据表，后续分析的基础。": "The cleaned table — the basis for later analysis.",
    "行级指标（每行 = 某样方中的某物种）。": "Row-level metrics (one row per species per quadrat).",
    "汇总指标（每行 = 一个样方或分组）。": "Summary metrics (one row per quadrat or group).",
    "清洗日志：改了什么、为什么改，写方法部分可直接引用。":
        "Cleaning log: what changed and why — cite it directly in your Methods.",
    "分析图表（PNG 300dpi / SVG 矢量图）。": "Charts (PNG 300dpi / SVG vector).",
    "分析报告（数据信息 / 清洗统计 / 指标汇总 / 质量评价）。":
        "Report (overview / cleaning / metrics / data quality).",
}
