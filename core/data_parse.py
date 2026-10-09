# -*- coding: utf-8 -*-
"""
多源数据解析核心（开发方案 第六章 阶段 2）

职责（本模块只做"读进来 + 认出来 + 摆整齐"，不做异常值判断、不做插值、不弹界面）：
    1. 支持 .csv / .xlsx / .txt，编码探测顺序固定为 utf-8-sig → utf-8 → gb18030；
    2. 自动识别三类数据：样方群落 / 传感器时序 / 普通实验统计；
    3. 时间列容错解析（多种文本格式 + Excel 序列号），统一输出 YYYY-MM-DD HH:MM:SS；
    4. 多文件先按类型分组，同类型才合并；
    5. 失败时抛 ParseError，异常信息必须回答"哪个文件 / 哪一列 / 为什么 / 怎么办"。

关键纪律（来自方案第九章避坑表）：
    避坑 1：CSV 必须首选 utf-8-sig。Excel 导出的 CSV 带 BOM，用 utf-8 读会让首列名变成
            "\\ufeff温度"，之后按列名取字段全部 KeyError——这是最高频的失败点。
    避坑 11：混合类型多文件绝不自动合并，必须让用户选择。
    设计取舍：recognize 只做"识别与预填"，不弹任何界面；需要用户确认时把
            recognize.needs_mapping 置为 True，由 ui 层的列映射对话框接管。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ===========================================================================
# 一、常量与数据结构
# ===========================================================================

# 编码探测顺序（方案阶段 2 第 1 条，顺序不可调整）
ENCODING_CANDIDATES: Tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030")

# 支持的文件扩展名
SUPPORTED_SUFFIXES: Tuple[str, ...] = (".csv", ".txt", ".xlsx", ".xls")

# 数据类型标识（写入 app_state 的 data_type 槽，界面按它决定勾选哪些指标组）
TYPE_QUADRAT = "样方群落"
TYPE_SENSOR = "传感器时序"
TYPE_PLAIN = "普通实验统计"

DATA_TYPES: Tuple[str, ...] = (TYPE_QUADRAT, TYPE_SENSOR, TYPE_PLAIN)

# 时间列统一输出格式（方案阶段 2 第 3 条）
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# 数据质量门槛
_MIN_ROWS = 1                 # 少于 1 行视为空文件
_HEADER_SCAN_ROWS = 10        # 探测分隔符 / 表头时最多看多少行
_TIME_NUMERIC_HIT_RATE = 0.6  # 数值列判为"时间"的最低命中率
_TIME_TEXT_HIT_RATE = 0.6     # 文本列判为"时间"的最低命中率
_EXCEL_SERIAL_MIN = 20000.0   # 约 1954 年
_EXCEL_SERIAL_MAX = 60000.0   # 约 2064 年

# 标准字段名与判定角色
#   required ：缺少即视为"未能识别该类型"，必须让用户手工映射
#   optional ：有则采纳，没有不影响识别
_FIELD_ROLE_REQUIRED = "required"
_FIELD_ROLE_OPTIONAL = "optional"

# 样方群落标准字段
# 说明：这里全部是"纯字段名"，不带单位。用户表头常写成「树高（m）」「胸径(cm)」，
#       但 _normalize_name 会去掉括号与空格，所以别名写纯名字就能命中这些写法。
# 测量列（树高/基径/冠幅）只作为测量值保存与导出，**不参与指标计算**：
#       优势度仍只看「胸径」与「盖度」，避免改动已验收的计算口径。
QUADRAT_FIELDS: Dict[str, str] = {
    "样方号": _FIELD_ROLE_REQUIRED,
    "物种": _FIELD_ROLE_REQUIRED,
    "株数": _FIELD_ROLE_REQUIRED,
    "胸径": _FIELD_ROLE_OPTIONAL,
    "盖度": _FIELD_ROLE_OPTIONAL,
    "树高": _FIELD_ROLE_OPTIONAL,
    "基径": _FIELD_ROLE_OPTIONAL,
    "冠幅": _FIELD_ROLE_OPTIONAL,
}

#: 样方数据里"纯测量"的数值列（有则保留、参与清洗校验与导出，不参与指标计算）
QUADRAT_MEASUREMENT_FIELDS: Tuple[str, ...] = ("树高", "基径", "冠幅")
# 传感器时序标准字段
SENSOR_FIELDS: Dict[str, str] = {
    "时间": _FIELD_ROLE_REQUIRED,
    "气温": _FIELD_ROLE_OPTIONAL,
    "相对湿度": _FIELD_ROLE_OPTIONAL,
    "土壤温度": _FIELD_ROLE_OPTIONAL,
}
# 普通实验统计标准字段（数值指标列由用户自定，不参与识别）
PLAIN_FIELDS: Dict[str, str] = {
    "处理组": _FIELD_ROLE_OPTIONAL,
    "重复": _FIELD_ROLE_OPTIONAL,
}

# 各类型的标准字段表
TYPE_FIELDS: Dict[str, Dict[str, str]] = {
    TYPE_QUADRAT: QUADRAT_FIELDS,
    TYPE_SENSOR: SENSOR_FIELDS,
    TYPE_PLAIN: PLAIN_FIELDS,
}

# 列名别名表：键为标准字段，值为可接受的写法（全部按小写去空白比较）
# 说明：别名只做"加分"，不做硬性要求；识别不出来时仍可手工映射（方案 5.5）。
_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    # ---- 样方群落 ----
    "样方号": ("样方号", "样方编号", "样方", "样地号", "样地", "quadrat", "plot", "plotid", "plot_id", "sample_id"),
    # 注意：别名「植物种」已移除——「植物」是它的前 2 个字，按子串规则会被误配成物种列。
    # 别名表宁可少列几个写法（漏了还能手工映射），也不要引入会把用户带偏的模糊命中。
    "物种": ("物种", "种名", "物种种名", "species", "taxon", "sp", "scientificname"),
    "株数": ("株数", "个体数", "个体总数", "数量", "多度", "count", "abundance", "individuals", "number"),
    "胸径": ("胸径", "胸高直径", "胸径cm", "dbh", "diameter", "diameteratbreastheight"),
    "盖度": ("盖度", "覆盖度", "植被盖度", "cover", "coverage", "canopycover"),
    # 测量列：只作为测量值保存与导出，不参与指标计算
    # 注意「基径」与「胸径」只差一个字、归一化后也不同，不会互相误配；
    #      「地径」是「基径」在林学里的常见同义叫法。
    # 别名刻意不写 "width"（太泛，会误配到冠层宽度之类的无关列），
    # 单字母 "h" 会被 _match_columns 的长度规则过滤掉，写了也没用。
    "树高": ("树高", "株高", "高度", "树高度", "treeheight", "height"),
    "基径": ("基径", "地径", "基径cm", "basaldiameter", "grounddiameter", "basediameter"),
    "冠幅": ("冠幅", "冠幅cm", "冠径", "树冠", "crownwidth", "crown", "canopywidth"),
    # ---- 传感器时序 ----
    "时间": ("时间", "日期", "时刻", "监测时间", "采集时间", "记录时间", "datetime", "date", "time", "timestamp", "记录日期"),
    "气温": ("气温", "空气温度", "大气温度", "温度", "airtemp", "air_temperature", "temperature", "temp", "ta", "at"),
    "相对湿度": ("相对湿度", "空气相对湿度", "湿度", "rh", "relativehumidity", "relative_humidity", "humidity"),
    "土壤温度": ("土壤温度", "土温", "地温", "土壤温度℃", "soiltemp", "soil_temperature", "soil_temp", "ts"),
    "光合有效辐射": ("光合有效辐射", "有效辐射", "光强", "辐射", "par", "ppfd", "solarradiation"),
    # ---- 普通实验统计 ----
    "处理组": ("处理组", "处理", "组别", "试验处理", "treatment", "group", "trt"),
    "重复": ("重复", "重复号", "区组", "replicate", "rep", "block"),
    "温度": ("温度", "temp", "temperature"),
    "株高": ("株高", "高度", "苗高", "height", "plantheight"),
    "生物量": ("生物量", "地上生物量", "干重", "biomass", "dryweight"),
    "鲜重": ("鲜重", "鲜质量", "freshweight"),
    "干重": ("干重", "干质量", "dryweight"),
}


def _to_datetime_safe(values: pd.Series) -> pd.Series:
    """通用时间解析兜底：压制 pandas 的"无法推断格式"警告后逐元素解析。

    为什么要压警告：逐元素回退解析会把 UserWarning 打到控制台，用户看到一堆英文警告
    会以为软件出错（方案 5.5 禁止技术性信息裸奔）。解析失败仍由调用方计数上报。
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.to_datetime(values, errors="coerce")


class ParseError(Exception):
    """解析失败异常。

    异常信息必须是"用户可读"的中文说明，并尽量回答四件事：
        哪个文件 / 哪一列 / 为什么 / 怎么办。
    界面直接把它显示在弹窗里，绝不显示 Python 堆栈（方案 5.5）。
    """

    def __init__(self, message: str, *, file: str = "", column: str = "",
                 reason: str = "", suggestion: str = "") -> None:
        self.file = file
        self.column = column
        self.reason = reason
        self.suggestion = suggestion
        super().__init__(message)


@dataclass
class ParsedFile:
    """单个文件的解析结果。"""
    path: Path                     # 文件绝对路径
    data_type: str                 # 识别出的数据类型
    df: pd.DataFrame               # 标准化后的数据表
    encoding: str = ""             # 实际使用的编码（xlsx 为空）
    delimiter: str = ""            # 实际使用的分隔符（xlsx 为空）
    score: float = 0.0             # 类型识别得分
    needs_mapping: bool = False    # 是否必须让用户手工确认列映射
    missing_fields: List[str] = field(default_factory=list)  # 最终类型下仍缺的必需字段
    suggested_missing: List[str] = field(default_factory=list)  # 识别阶段建议补齐的字段
    mapping: Dict[str, str] = field(default_factory=dict)    # 标准字段 -> 原始列名
    warnings: List[str] = field(default_factory=list)        # 中文提示（非致命问题）
    rows_raw: int = 0              # 标准化前的行数
    nat_count: int = 0             # 时间解析失败的个数（仅时序数据有意义）


@dataclass
class ParseResult:
    """整体解析结果（可能由多个同类型文件合并而来）。"""
    df: pd.DataFrame
    data_type: str
    files: List[ParsedFile] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    nat_count: int = 0
    out_of_order_rows: int = 0
    rows_raw_total: int = 0
    time_range: Optional[Tuple[str, str]] = None   # (最早, 最晚)，非时序数据为 None


# ===========================================================================
# 二、编码与分隔符探测
# ===========================================================================
def detect_encoding(path: Path) -> str:
    """按 utf-8-sig → utf-8 → gb18030 顺序探测文本文件编码。

    为什么必须先试 utf-8-sig：带 BOM 的 CSV 用 utf-8 读出来，首列列名会多一个
    不可见的 \\ufeff 字符，后续 df["温度"] 全部 KeyError（方案避坑 1）。
    utf-8-sig 对不带 BOM 的 utf-8 文件同样能正确读取，因此放在第一位是安全的。
    """
    raw = Path(path).read_bytes()
    for encoding in ENCODING_CANDIDATES:
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise ParseError(
        "文件 {0} 的编码无法识别。".format(Path(path).name),
        file=str(path),
        reason="已依次尝试 UTF-8 带 BOM、UTF-8、GB18030（兼容 GBK）三种编码，均无法解码。",
        suggestion="用记事本打开该文件 → 另存为 → 编码选择「UTF-8」或「ANSI（GBK）」→ 保存后重新导入。",
    )


def detect_delimiter(text_head: str) -> str:
    """探测文本分隔符：在表头行里出现次数最多的候选分隔符胜出。

    为什么不能写死逗号：方案第八章的 sample_plain_gbk.txt 就是制表符分隔，
    写死逗号会把整行当成一列，后面识别列名必然失败。
    """
    first_line = ""
    for line in text_head.splitlines():
        if line.strip():
            first_line = line
            break
    if not first_line:
        return ","

    counts = {sep: first_line.count(sep) for sep in (",", "\t", ";", "|")}
    best_sep, best_count = max(counts.items(), key=lambda kv: kv[1])
    return best_sep if best_count > 0 else ","


def read_table(path: Path) -> Tuple[pd.DataFrame, str, str]:
    """读取一个数据文件，返回 (数据表, 实际编码, 实际分隔符)。

    说明：xlsx 走 openpyxl，不需要编码与分隔符；其余按编码探测 + 分隔符探测读取。
    读取失败一律转成 ParseError，保证界面拿到的是中文说明。
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ParseError(
            "找不到文件 {0}。".format(file_path.name),
            file=str(file_path),
            reason="文件可能已被移动、重命名或删除。",
            suggestion="确认文件仍在原位置，或重新选择文件后导入。",
        )
    if file_path.stat().st_size == 0:
        raise ParseError(
            "文件 {0} 是空文件。".format(file_path.name),
            file=str(file_path),
            reason="文件大小为 0 字节，没有任何内容可以读取。",
            suggestion="请确认导出数据时是否成功写入，或换一个文件重试。",
        )

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(
            "文件 {0} 的格式暂不支持。".format(file_path.name),
            file=str(file_path),
            reason="当前只支持 csv（逗号或制表符分隔）、txt（文本表格）、xlsx（Excel 工作簿）。",
            suggestion="用 Excel 打开后另存为「xlsx」或「CSV UTF-8（逗号分隔）」，再重新导入。",
        )

    # ---- Excel ----
    if suffix in (".xlsx", ".xls"):
        try:
            df = pd.read_excel(file_path, sheet_name=0, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001 - 统一转成中文说明
            raise ParseError(
                "读取 Excel 文件 {0} 失败。".format(file_path.name),
                file=str(file_path),
                reason="文件可能已损坏、被其他程序占用，或不是真正的 Excel 工作簿（例如把 csv 改名成 xlsx）。",
                suggestion="先关闭 Excel 中打开的同名文件；若仍失败，请用 Excel 打开后「另存为 xlsx」再试。",
            ) from exc
        return df, "", ""

    # ---- 文本（csv / txt）----
    encoding = detect_encoding(file_path)
    text = file_path.read_text(encoding=encoding, errors="strict")
    head = "\n".join(text.splitlines()[:_HEADER_SCAN_ROWS])
    delimiter = detect_delimiter(head)

    try:
        df = pd.read_csv(io.StringIO(text), sep=delimiter, engine="python")
    except Exception as exc:  # noqa: BLE001
        raise ParseError(
            "解析文件 {0} 失败。".format(file_path.name),
            file=str(file_path),
            reason="按「{0}」分隔符读取时出错，表格行列数可能不一致。".format(
                "制表符" if delimiter == "\t" else delimiter),
            suggestion="用 Excel 打开该文件检查是否有错行的单元格，另存为「CSV UTF-8」后重试。",
        ) from exc
    return df, encoding, delimiter


# ===========================================================================
# 三、列名匹配与类型识别
# ===========================================================================
def _normalize_name(name: Any) -> str:
    """把列名规范化为小写、去空白、去常见符号的字符串，便于与别名比较。

    例："T(℃)" → "t℃"；" 空气温度 " → "空气温度"；"Air_Temp" → "airtemp"
    """
    text = str(name).strip().lower()
    for ch in (" ", "\t", "\n", "_", "-", "（", "）", "(", ")", "[", "]", "/", "\\"):
        text = text.replace(ch, "")
    return text


def _is_meaningful_substring(shorter: str, longer: str) -> bool:
    """判断 short 作为 long 的子串是否"有意义"，用于列名的模糊匹配。

    为什么要设门槛（实测踩坑）：
        只做 in 判断会把「地块」匹配到别名「样地号」、「植物」匹配到别名「植物种」，
        结果是样方数据被误判成"缺少样方号与物种"，用户被迫去做无意义的列映射。
    规则：
        1. 别名本身至少 2 个字（单字别名如「种」「数」噪声太大，直接不参与）；
        2. 被匹配的名字不能只是别名的开头一小截——至少占别名的 60%。
    这样「空气温度℃」→「空气温度」（100%）、「样方」→「样方号」（67%）都能命中，
    而「地块」→「样地号」（50%）、「植物」→「植物种」（67% 但别名仅 2 字）会被拒绝。
    """
    if len(shorter) < 2 or len(longer) < 2:
        return False
    if shorter not in longer:
        return False
    return len(shorter) / float(len(longer)) >= 0.6


def _match_columns(columns: Sequence[Any], fields: Dict[str, str]) -> Dict[str, Optional[Any]]:
    """把标准字段匹配到实际列名，返回 {标准字段: 实际列名或 None}。

    匹配策略（从严到宽，宁可漏配也不要错配 —— 漏配由列映射兜底，错配会让用户困惑）：
        1. 规范化后完全相等（标准字段名本身或任一别名）；
        2. 实际列名包含完整别名，例如「空气温度℃」包含「空气温度」；
        3. 别名包含实际列名，例如「样方号」包含「样方」（受 _is_meaningful_substring 约束）。
    """
    normalized = {col: _normalize_name(col) for col in columns}
    result: Dict[str, Optional[Any]] = {}

    for field_name in fields:
        aliases = [_normalize_name(alias) for alias in _FIELD_ALIASES.get(field_name, (field_name,))]
        aliases = [alias for alias in aliases if len(alias) >= 2] or [_normalize_name(field_name)]

        # 1：完全相等
        matched: Optional[Any] = None
        for col, norm in normalized.items():
            if norm in aliases:
                matched = col
                break

        # 2：列名包含完整别名（列名更长，信息更多，通常更可靠）
        if matched is None:
            for col, norm in normalized.items():
                if any(alias in norm for alias in aliases):
                    matched = col
                    break

        # 3：别名包含列名（列名更短，风险更高，需通过意义性检查）
        if matched is None:
            for col, norm in normalized.items():
                if any(_is_meaningful_substring(norm, alias) for alias in aliases):
                    matched = col
                    break

        result[field_name] = matched
    return result


def _score_type(columns: Sequence[Any], data_type: str) -> Tuple[float, Dict[str, Optional[Any]], List[str]]:
    """给"某类型"打分，同时返回列匹配情况与缺失的必需字段。

    打分规则：必需字段每个 2 分，可选字段每个 1 分。
    这样只要有一个必需字段对不上，就无法与其他类型并列，避免误判。
    """
    fields = TYPE_FIELDS[data_type]
    matched = _match_columns(columns, fields)
    score = 0.0
    missing_required: List[str] = []
    for name, role in fields.items():
        if matched.get(name) is None:
            if role == _FIELD_ROLE_REQUIRED:
                missing_required.append(name)
            continue
        score += 2.0 if role == _FIELD_ROLE_REQUIRED else 1.0
    return score, matched, missing_required


def _looks_like_time_column(series: pd.Series) -> float:
    """判断一列像不像时间列，返回命中率（0~1）。

    用途：当列名完全不认识（例如 "col1"）时，仍有机会认出时间列。
    判定手段与 parse_time_column 保持一致，避免"能认出来却解析不了"。
    """
    sample = series.dropna()
    if sample.empty:
        return 0.0
    sample = sample.head(200)

    if pd.api.types.is_numeric_dtype(sample):
        values = pd.to_numeric(sample, errors="coerce")
        in_range = values.between(_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX)
        return float(in_range.mean())

    text = sample.astype(str).str.strip()
    parsed = _to_datetime_safe(text)
    return float(parsed.notna().mean())


def recognize(df: pd.DataFrame) -> Dict[str, Any]:
    """识别数据表类型，返回识别结果字典。

    返回键：
        data_type       ：得分最高的类型（得分过低时为 None）
        score           ：该类型得分
        scores          ：各类型得分明细，便于调试
        mapping         ：该类型的「标准字段 -> 实际列名」
        missing_fields  ：缺失的必需字段
        needs_mapping   ：是否需要用户手工确认列映射
        suggestions     ：给用户的中文建议（用于弹窗与提示标签）
    """
    columns = list(df.columns)

    scores: Dict[str, float] = {}
    matches: Dict[str, Dict[str, Optional[Any]]] = {}
    missing: Dict[str, List[str]] = {}
    for data_type in (TYPE_QUADRAT, TYPE_SENSOR):
        score, matched, missing_required = _score_type(columns, data_type)
        # 时序数据没有温湿度列时，若存在"内容像时间"的列，额外加分，
        # 避免同一个文件因为列名特殊而被判成普通统计。
        if data_type == TYPE_SENSOR and matched.get("时间") is None:
            for col in columns:
                if _looks_like_time_column(df[col]) >= _TIME_TEXT_HIT_RATE:
                    matched["时间"] = col
                    score += 2.0
                    missing_required = [name for name in missing_required if name != "时间"]
                    break
        scores[data_type] = score
        matches[data_type] = matched
        missing[data_type] = missing_required

    # 普通统计是兜底类型：两个候选都不成立时使用
    plain_score, plain_matched, _ = _score_type(columns, TYPE_PLAIN)
    scores[TYPE_PLAIN] = plain_score
    matches[TYPE_PLAIN] = plain_matched
    missing[TYPE_PLAIN] = []

    # ---- 选择最佳类型 ----
    # 关键规则：只要有必需字段缺失，该类型就不能被自动采纳（绝不擅自猜测）。
    viable = [t for t in (TYPE_QUADRAT, TYPE_SENSOR) if not missing[t]]
    if viable:
        best = max(viable, key=lambda t: scores[t])
        # 两个类型都成立且得分相同 → 说明数据既有时间列又有物种列，必须问用户
        tied = [t for t in viable if scores[t] == scores[best]]
        needs_mapping = len(tied) > 1
        suggestions = ["数据特征同时符合「{0}」，请确认数据类型。".format("」「".join(tied))] if needs_mapping else []
        return {
            "data_type": best,
            "score": scores[best],
            "scores": scores,
            "mapping": matches[best],
            "missing_fields": [],
            "needs_mapping": needs_mapping,
            "suggestions": suggestions,
        }

    # 没有任何类型成立 → 需要用户手工指定（绝不猜）
    # 选"最接近"的类型时按**命中的必需字段个数**而不是总分：
    # 传感器时序全是可选字段，总分容易虚高，会把明显的样方数据带偏。
    def _required_hits(data_type: str) -> int:
        fields = TYPE_FIELDS[data_type]
        return sum(1 for name, role in fields.items()
                   if role == _FIELD_ROLE_REQUIRED and matches[data_type].get(name) is not None)

    closest = max((TYPE_QUADRAT, TYPE_SENSOR), key=_required_hits)
    if _required_hits(closest) == 0:
        # 两个候选一个必需字段都没命中（例如 col1/col2 这种表头），
        # 此时说"像样方群落"没有任何依据，直接按普通统计处理并请用户指定列映射。
        suggestions = [
            "未能从表头识别出已知的数据类型。",
            "请用【列映射】手工指定各标准字段对应的列；若不是样方群落或传感器时序数据，"
            "请把数据类型改为「{0}」。".format(TYPE_PLAIN),
        ]
        return {
            "data_type": TYPE_PLAIN,
            "score": scores[TYPE_PLAIN],
            "scores": scores,
            "mapping": matches[TYPE_PLAIN],
            "missing_fields": [],
            "needs_mapping": True,
            "suggestions": suggestions,
        }

    suggestions = [
        "数据中识别到部分「{0}」特征，但缺少必需列：{1}。".format(closest, "、".join(missing[closest])),
        "如果它确实是{0}数据，请用【列映射】把缺少的字段对应到你的列；".format(closest),
        "如果它只是普通的实验统计数据，请把数据类型改为「{0}」。".format(TYPE_PLAIN),
    ]
    return {
        "data_type": TYPE_PLAIN,
        "score": scores[TYPE_PLAIN],
        "scores": scores,
        "mapping": matches[closest],
        "missing_fields": missing[closest],
        "needs_mapping": True,
        "suggestions": suggestions,
    }


# ===========================================================================
# 四、时间列容错解析
# ===========================================================================
# 方案阶段 2 第 3 条要求的格式，外加几种常见写法；
# %Y%m%d 这类"纯数字"格式在下面的函数里单独处理，避免把年份数字当时间。
_TIME_FORMATS: Tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日 %H:%M",
    "%Y年%m月%d日",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_text_datetimes(text: pd.Series) -> pd.Series:
    """把文本时间列解析成 datetime，逐种格式尝试，最后再用通用解析兜底。

    为什么要逐种格式试：pandas 的自动推断在"日/月"顺序上有歧义
    （2024/5/3 可能被当成 5 月 3 日或 3 月 5 日），显式给格式可以保证结果稳定可复现。
    """
    result = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    remaining = text.notna()
    if not remaining.any():
        return result

    for fmt in _TIME_FORMATS:
        if not remaining.any():
            break
        subset = text[remaining]
        parsed = pd.to_datetime(subset, format=fmt, errors="coerce")
        hit = parsed.notna()
        if hit.any():
            result.loc[parsed.index[hit]] = parsed[hit]
            remaining.loc[parsed.index[hit]] = False

    # 剩余的交给通用解析：覆盖上面没列到的写法（例如 "2024-5-3 14:00"）
    if remaining.any():
        parsed = _to_datetime_safe(text[remaining])
        hit = parsed.notna()
        if hit.any():
            result.loc[parsed.index[hit]] = parsed[hit]
    return result


def parse_time_column(series: pd.Series) -> Tuple[pd.Series, int]:
    """时间列容错解析，返回 (datetime 序列, 无法解析的个数)。

    依次处理四种情况（方案阶段 2 第 3 条）：
        1. 数值型且落在合理年份区间 → 按 Excel 序列号转换（origin=1899-12-30, unit="D"）；
        2. 8 位整数形式的 20240503 → 按 %Y%m%d 解析；
        3. 文本型 → 逐种格式解析；
        4. 仍失败 → 置 NaT 并计数上报（绝不静默丢弃）。
    统一取整到秒，保证输出 "YYYY-MM-DD HH:MM:SS" 时没有 "01:00:00.028799" 这种尾巴。
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype="datetime64[ns]"), 0

    values = series.copy()

    # ---- 情况 1：数值型（Excel 序列号）----
    if pd.api.types.is_numeric_dtype(values):
        numeric = pd.to_numeric(values, errors="coerce")
        in_serial_range = numeric.between(_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX)
        parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
        if in_serial_range.any():
            parsed.loc[in_serial_range] = pd.to_datetime(
                numeric[in_serial_range], origin="1899-12-30", unit="D", errors="coerce")
        # 不落在序列号区间的整数：可能是 20240503 这种紧凑写法
        rest = numeric.notna() & ~in_serial_range
        if rest.any():
            as_text = numeric[rest].astype("int64").astype(str)
            parsed.loc[rest] = _parse_text_datetimes(as_text)
        nat_count = int(parsed.isna().sum())
        return _finalize_time(parsed), nat_count

    # ---- 情况 2/3：文本型 ----
    text = values.astype(str).str.strip()
    text = text.replace({"": None, "nan": None, "NaT": None, "None": None, "-": None})

    # 先处理 8 位纯数字（20240503 或 202405031400）
    eight = text.str.fullmatch(r"\d{8}", na=False)
    twelve = text.str.fullmatch(r"\d{12}", na=False)
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    if eight.any():
        parsed.loc[eight] = pd.to_datetime(text[eight], format="%Y%m%d", errors="coerce")
    if twelve.any():
        parsed.loc[twelve] = pd.to_datetime(text[twelve], format="%Y%m%d%H%M", errors="coerce")

    rest = text.notna() & ~eight & ~twelve
    if rest.any():
        parsed.loc[rest] = _parse_text_datetimes(text[rest])

    nat_count = int(parsed.isna().sum())
    return _finalize_time(parsed), nat_count


def _finalize_time(parsed: pd.Series) -> pd.Series:
    """统一时间列的收尾处理：取整到秒，避免 Excel 序列号的浮点尾差。"""
    try:
        return parsed.dt.round("s")
    except Exception:  # pragma: no cover - 极端脏数据兜底
        return parsed


# ===========================================================================
# 五、标准化
# ===========================================================================
def _rename_by_mapping(df: pd.DataFrame, mapping: Dict[str, Any], data_type: str) -> pd.DataFrame:
    """按映射把原始列名改成标准字段名，未匹配的列原样保留。

    重要限制：只重命名"该数据类型标准字段表里存在的字段"。
    否则普通实验统计数据里的「温度」会被悄悄改成「气温」——那类数据本来就没有
    气温/相对湿度这些字段，改名会让用户在预览表里看不懂自己的列（方案 5.5）。
    """
    allowed = set(TYPE_FIELDS.get(data_type, {}).keys())
    # 时序数据允许把任意数值列原样分析，这里额外放行常见补充字段
    allowed |= {"光合有效辐射", "土壤湿度", "风速", "降水量"}

    rename_map: Dict[Any, str] = {}
    for field_name, column in mapping.items():
        if column is None or column not in df.columns:
            continue
        if field_name not in allowed:
            continue
        # 同名不重命名，避免 pandas 产生重复列
        if str(column) == field_name:
            continue
        # 若目标标准名已被别的列占用，则跳过，防止覆盖真实数据
        if field_name in df.columns:
            continue
        rename_map[column] = field_name

    result = df.rename(columns=rename_map)
    # 去掉重命名后可能产生的重复列（保留第一个）
    if result.columns.duplicated().any():
        result = result.loc[:, ~result.columns.duplicated()]
    return result


def _numbers(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """把指定列转成数值型（无法转换的置 NaN），供清洗阶段按规则处理。

    注意：这里**不删行、不填值**。数据质量问题留给阶段 3 清洗，本阶段只保证类型正确。
    """
    result = df.copy()
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def normalize_frame(df: pd.DataFrame, data_type: str, mapping: Dict[str, Any]) -> pd.DataFrame:
    """把原始数据表标准化成"列名统一、类型正确"的表。

    标准化包含三件事：
        1. 列名换成标准字段名；
        2. 时间列解析成 YYYY-MM-DD HH:MM:SS 文本（便于界面显示与导出，且不含时区歧义）；
        3. 数值列转成数值类型。
    绝不在这里做量纲换算、异常值判断、缺失值填充——那是阶段 3 的职责。
    """
    _ = data_type  # 保留参数以便后续按类型做差异化处理
    result = _rename_by_mapping(df, mapping, data_type)

    # ---- 时间列 ----
    if "时间" in result.columns:
        parsed, _nat = parse_time_column(result["时间"])
        # 输出为固定格式文本；NaT 保持为空字符串，便于清洗阶段识别为缺失
        result["时间"] = parsed.dt.strftime(TIME_FORMAT).where(parsed.notna(), None)
        # 时间列放第一列（列顺序稳定，便于所有页面预览与对比）
        ordered = ["时间"] + [c for c in result.columns if c != "时间"]
        result = result[ordered]

    # ---- 数值列 ----
    # 直接由 QUADRAT_FIELDS 派生，避免"加了字段忘了加成数值列"（实测踩坑：
    # 新加的测量列若不在这里转数值，后续清洗与绘图都会当成文本处理）。
    numeric_candidates = ["株数", "胸径", "盖度", "气温", "相对湿度", "土壤温度", "光合有效辐射",
                          "温度", "株高", "生物量", "鲜重", "干重", "重复"]
    numeric_candidates += [name for name in QUADRAT_MEASUREMENT_FIELDS
                           if name not in numeric_candidates]
    result = _numbers(result, [c for c in numeric_candidates if c in result.columns])
    return result


def _drop_empty_rows(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """删除整行全空的行（Excel 常见的尾部空行），返回 (新表, 删除行数)。"""
    before = len(df)
    cleaned = df.dropna(how="all").reset_index(drop=True)
    return cleaned, before - len(cleaned)


def _dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """处理重名列：pandas 会自动加 .1 后缀，这里仅确保列名唯一可读。"""
    if not df.columns.duplicated().any():
        return df
    seen: Dict[str, int] = {}
    new_columns: List[str] = []
    for column in df.columns:
        name = str(column)
        if name in seen:
            seen[name] += 1
            new_columns.append("{0}_{1}".format(name, seen[name]))
        else:
            seen[name] = 0
            new_columns.append(name)
    result = df.copy()
    result.columns = new_columns
    return result


def probe_file(path: Path) -> Dict[str, Any]:
    """只读表头与前几行，快速探测文件类型（供导入页的文件列表预填下拉框）。

    为什么不直接 parse_file：用户一次可能选十几个文件，逐个完整解析会让界面明显卡顿。
    这里只取前若干行，代价极小；真正的类型确认仍以 parse_file 的结果为准。
    探测失败不抛异常，返回 data_type=None，由界面提示用户手工指定。
    """
    file_path = Path(path)
    try:
        df, encoding, delimiter = read_table(file_path)
    except ParseError as exc:
        return {"data_type": None, "columns": [], "encoding": "", "delimiter": "",
                "error": str(exc), "reason": exc.reason, "suggestion": exc.suggestion}
    except Exception as exc:  # noqa: BLE001 - 探测阶段绝不因脏文件中断整批导入
        return {"data_type": None, "columns": [], "encoding": "", "delimiter": "",
                "error": "读取文件 {0} 的表头时出错。".format(file_path.name),
                "reason": str(exc)[:120], "suggestion": "请确认该文件是完整的 csv / xlsx / txt 表格。"}

    df = _dedupe_columns(df).head(_HEADER_SCAN_ROWS)
    recognized = recognize(df)
    return {
        "data_type": recognized["data_type"],
        "needs_mapping": recognized["needs_mapping"],
        "columns": [str(col) for col in df.columns],
        "encoding": encoding,
        "delimiter": delimiter,
        "error": "",
        "reason": "",
        "suggestion": "",
    }


# ===========================================================================
# 六、对外主接口
# ===========================================================================
def parse_file(path: Path, data_type: Optional[str] = None,
               mapping: Optional[Dict[str, Any]] = None) -> ParsedFile:
    """解析单个文件。

    参数：
        path      ：文件路径
        data_type ：用户指定的数据类型（None 表示自动识别）
        mapping   ：用户手工指定的「标准字段 -> 原始列名」映射（None 表示自动匹配）
    返回：
        ParsedFile（含标准化后的数据表与识别信息）
    异常：
        ParseError —— 信息为中文，可直接展示给用户
    """
    file_path = Path(path)
    df, encoding, delimiter = read_table(file_path)
    df = _dedupe_columns(df)
    df, dropped_empty = _drop_empty_rows(df)
    rows_raw = len(df)

    if rows_raw < _MIN_ROWS:
        raise ParseError(
            "文件 {0} 中没有可用数据行。".format(file_path.name),
            file=str(file_path),
            reason="读取到的表格只有表头、没有数据，或所有行都是空行。",
            suggestion="用 Excel 打开确认数据是否真的存在；若数据在第 2 个工作表，请先把它移到第 1 个表再导入。",
        )

    # 一次读完就解析时间列，避免后续重复解析
    recognized = recognize(df)
    recognized = dict(recognized)  # 复制，避免污染调用方传入的对象

    final_type = data_type or recognized["data_type"]
    warnings: List[str] = []
    if data_type is None:
        warnings.extend(recognized.get("suggestions", []))

    effective_mapping: Dict[str, Any] = {}
    if mapping:
        effective_mapping = {k: v for k, v in mapping.items() if v}
    else:
        effective_mapping = {k: v for k, v in recognized["mapping"].items() if v}

    # 用户改了类型但没给映射时，按新类型重新匹配一次列名
    if data_type is not None and data_type != recognized["data_type"] and not mapping:
        _score, re_matched, _missing = _score_type(df.columns, data_type)
        effective_mapping = {k: v for k, v in re_matched.items() if v}

    df_norm = normalize_frame(df, final_type, effective_mapping)

    nat_count = 0
    if "时间" in df_norm.columns:
        _parsed, nat_count = parse_time_column(df["时间"] if "时间" in df.columns else df_norm["时间"])
        if nat_count:
            warnings.append(
                "有 {0} 个时间无法识别，已留空（不会被丢弃，清洗阶段会一并在日志中列出）。".format(nat_count))

    if dropped_empty:
        warnings.append("已忽略 {0} 行完全空白的记录。".format(dropped_empty))

    # 判断是否需要用户确认列映射：识别置信度不足，或必需字段缺失
    # 重要区分：用户**显式指定**了数据类型时，只按该类型的必需字段判断；
    # 自动识别失败时的"建议映射"（例如识别到温度列但确实没有时间列）不该再打扰用户。
    final_fields = TYPE_FIELDS.get(final_type, {})
    missing_fields: List[str] = []
    for name, role in final_fields.items():
        if role != _FIELD_ROLE_REQUIRED:
            continue
        column = effective_mapping.get(name)
        if column is None:
            missing_fields.append(name)
    missing_fields = sorted(set(missing_fields))
    # 仅当用户没有显式指定类型时，才让自动识别的模糊结果也要求人工确认
    needs_mapping = bool(missing_fields) or (data_type is None and bool(recognized["needs_mapping"]))

    return ParsedFile(
        path=file_path,
        data_type=final_type,
        df=df_norm,
        encoding=encoding,
        delimiter="制表符" if delimiter == "\t" else delimiter,
        score=float(recognized["score"]),
        needs_mapping=needs_mapping,
        missing_fields=missing_fields,
        suggested_missing=sorted(set(recognized.get("missing_fields") or [])),
        mapping={k: str(v) for k, v in effective_mapping.items() if v is not None},
        warnings=warnings,
        rows_raw=rows_raw,
        nat_count=nat_count,
    )


def parse_files(paths: Sequence[Path], data_type: Optional[str] = None,
                mapping: Optional[Dict[str, Any]] = None) -> ParseResult:
    """解析一个或多个文件；多文件必须先按类型分组，同类型才合并。

    关键规则（方案阶段 2 第 5 条 + 避坑 11）：
        样方群落与传感器时序绝不允许自动拼接成一张表——那会产出一张荒谬的数据表。
        因此这里只合并"识别结果相同"的文件；出现混合类型时抛 ParseMixedTypesError，
        由界面提示用户选择其中一类或分两批导入。
    """
    if not paths:
        raise ParseError(
            "还没有选择任何文件。",
            reason="导入需要至少一个数据文件。",
            suggestion="点【选择文件】选择 csv / xlsx / txt 文件，或点【载入示例数据】直接使用软件自带的示例。",
        )

    parsed: List[ParsedFile] = [parse_file(path, data_type=data_type, mapping=mapping) for path in paths]

    # ---- 检查类型是否一致 ----
    types = {item.data_type for item in parsed}
    if len(types) > 1:
        detail = "；".join("{0} → {1}".format(item.path.name, item.data_type) for item in parsed)
        raise ParseMixedTypesError(
            "选中的文件属于不同类型，不能合并成一张表。\n\n" + detail,
            types=sorted(types),
            parsed=parsed,
            reason="样方群落数据（记录某样方有哪些物种）与传感器时序数据（按时间连续记录）"
                   "结构完全不同，强行拼接会产生一张无法分析的表。",
            suggestion="请二选一：① 只选择同一类型的文件再导入；② 分两批导入，"
                       "先把一类分析完，再重新选择另一类。",
        )

    merged = pd.concat([item.df for item in parsed], ignore_index=True)
    data_type_final = parsed[0].data_type

    # ---- 时间戳排序（仅时序数据）----
    out_of_order = 0
    time_range: Optional[Tuple[str, str]] = None
    nat_total = sum(item.nat_count for item in parsed)
    if "时间" in merged.columns:
        sortable = pd.to_datetime(merged["时间"], format=TIME_FORMAT, errors="coerce")
        out_of_order = int((sortable.diff().dropna() < pd.Timedelta(0)).sum())
        valid = sortable.dropna()
        if not valid.empty:
            time_range = (valid.min().strftime(TIME_FORMAT), valid.max().strftime(TIME_FORMAT))
        merged = merged.assign(_排序时间=sortable).sort_values(
            "_排序时间", kind="stable", na_position="last").drop(columns=["_排序时间"]).reset_index(drop=True)

    warnings: List[str] = []
    for item in parsed:
        for message in item.warnings:
            if message not in warnings:
                warnings.append(message)
    if out_of_order:
        warnings.append("检测到 {0} 处时间倒退，已按时间升序重新排列。".format(out_of_order))
    if len(parsed) > 1:
        warnings.append("已把 {0} 个同类型文件合并为一张数据表（共 {1} 行）。".format(len(parsed), len(merged)))

    # 合并后同一"样方号 + 物种"出现多次，通常意味着两份调查表覆盖了同一批样方。
    # 这不会让软件算错（清洗阶段仍会逐行处理），但必须提醒用户，否则会得到偏大的
    # 总株数与重复计入的重要值——这类错误在结果里几乎看不出来，必须当场提示。
    if data_type_final == TYPE_QUADRAT and {"样方号", "物种"}.issubset(merged.columns):
        duplicate_pairs = int(merged.duplicated(subset=["样方号", "物种"]).sum())
        if duplicate_pairs:
            warnings.append(
                "合并后发现有 {0} 行属于同一个「样方号 + 物种」组合，"
                "通常说明多个文件调查了同一批样方。请确认是否应该只导入其中一份。".format(duplicate_pairs))

    return ParseResult(
        df=merged,
        data_type=data_type_final,
        files=parsed,
        warnings=warnings,
        nat_count=nat_total,
        out_of_order_rows=out_of_order,
        rows_raw_total=sum(item.rows_raw for item in parsed),
        time_range=time_range,
    )


class ParseMixedTypesError(ParseError):
    """混合类型多文件异常：界面据此弹出"选择其中一类"的对话框。"""

    def __init__(self, message: str, *, types: List[str], parsed: List[ParsedFile], **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.types = types
        self.parsed = parsed


# ===========================================================================
# 七、内置示例数据
# ===========================================================================
def demo_files() -> List[Path]:
    """返回内置示例数据文件列表（static/demo 下的干净数据）。

    为什么默认只给一套：样方群落与传感器时序不能合并（见避坑 11），
    而阶段 4/5/6 的主流程（重要值、多样性指数、报告）以样方群落数据最完整，
    因此"载入示例数据"默认载入样方群落数据，保证用户一次点击就能跑完整条链路。
    时序示例数据在导入页提供单独按钮，供想看时序图表的用户另外载入。
    """
    from utils import paths  # 延迟导入：本模块的纯解析部分不需要依赖路径配置

    quadrat = paths.DEMO_DIR / "demo_quadrat.csv"
    sensor = paths.DEMO_DIR / "demo_sensor.csv"
    return [path for path in (quadrat, sensor) if path.exists()]


def demo_quadrat_file() -> Path:
    """内置示例：样方群落数据（默认载入这一套）。"""
    from utils import paths

    return paths.DEMO_DIR / "demo_quadrat.csv"


def demo_sensor_file() -> Path:
    """内置示例：传感器时序数据。"""
    from utils import paths

    return paths.DEMO_DIR / "demo_sensor.csv"


def load_demo_data(data_type: Optional[str] = None) -> ParseResult:
    """载入内置示例数据（方案 5.1「载入示例数据」按钮的后端实现）。

    默认只载入干净的样方群落数据；若显式指定 data_type，则载入对应的那一套。
    """
    if data_type == TYPE_SENSOR:
        target = demo_sensor_file()
        kind = TYPE_SENSOR
    else:
        target = demo_quadrat_file()
        kind = TYPE_QUADRAT

    if not target.exists():
        from utils import paths

        raise ParseError(
            "找不到内置示例数据文件 {0}。".format(target.name),
            file=str(target),
            reason="示例数据随软件一起分发，当前安装目录下没有找到它（{0}）。".format(paths.DEMO_DIR),
            suggestion="若是自行打包运行，请确认打包时用 --add-data 把 static 目录一起带上；"
                       "也可以直接导入自己的 csv / xlsx / txt 文件。",
        )
    return parse_files([target], data_type=kind)


# ===========================================================================
# 八、数据质量概览（供导入页提示标签与后续报告使用）
# ===========================================================================
def quality_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """统计数据表的基本情况，供界面提示与报告第一章使用。

    返回：行数、列数、各列缺失率、时间跨度（若有时间列）。
    """
    rows = int(len(df))
    columns = int(len(df.columns))
    missing: Dict[str, float] = {}
    for column in df.columns:
        if rows == 0:
            missing[str(column)] = 0.0
        else:
            missing[str(column)] = round(float(df[column].isna().mean() * 100.0), 2)

    span: Optional[Tuple[str, str]] = None
    if "时间" in df.columns:
        parsed = pd.to_datetime(df["时间"], format=TIME_FORMAT, errors="coerce").dropna()
        if not parsed.empty:
            span = (parsed.min().strftime(TIME_FORMAT), parsed.max().strftime(TIME_FORMAT))

    return {
        "rows": rows,
        "columns": columns,
        "missing_rate": missing,
        "time_span": span,
        "total_missing": int(df.isna().sum().sum()),
    }


def is_empty_frame(df: Optional[pd.DataFrame]) -> bool:
    """判断数据表是否"空到不能继续处理"（阶段 8 会用它拦截空数据进入清洗/计算）。"""
    return df is None or len(df) == 0 or len(df.columns) == 0
