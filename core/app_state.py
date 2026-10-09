# -*- coding: utf-8 -*-
"""
全局状态容器（开发方案 第三章 规格）—— 阶段 1 完整实现

为什么必须有这个文件：
    5 个页面之间禁止互相 import、禁止互相持有引用。若没有独立的全局容器，
    "共享数据"只能塞进 main_window.py，结果是 5 个页面全部反向依赖主窗口，
    后期必然循环导入、改一处崩三处。本文件是模块之间唯一合法的通信通道。

强制纪律（违反会导致难以排查的问题，务必遵守）：
    1. 写数据必须调 set_*，读数据必须调 get_*，禁止在其它模块直接访问 _data 字典。
    2. 数据槽键名全程固定（见 SLOTS），禁止新增临时键；写错键名会当场抛 KeyError。
    3. 任何页面收到 data_changed 信号后自行刷新自身预览表，
       禁止发送方去操作别人的控件。
    4. 清洗与计算遵循"输入只读、输出新对象"，本类只负责存取，不做任何数据加工。
    5. 页面的构造函数只接收 app_state 参数，不接收兄弟页面引用。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

try:  # pandas 仅用于类型标注；缺失依赖时不影响模块被导入
    from pandas import DataFrame
except Exception:  # pragma: no cover - 依赖缺失时的降级占位
    DataFrame = Any  # type: ignore[assignment,misc]


class AppState(QObject):
    """全局数据容器单例。

    单例获取方式：AppState.instance()
    页面构造函数只接收 app_state 参数，不接收兄弟页面引用。
    """

    # 参数 = 发生变化的键名。页面各自连接本信号并按需刷新自身预览。
    # 特殊值 "*" 表示全部槽位都变了（reset 时发出）。
    data_changed = Signal(str)

    # ---------------------------------------------------------------------
    # 固定数据槽（开发方案 3.1，禁止新增临时键）
    # ---------------------------------------------------------------------
    # 表类槽：值为 pandas.DataFrame
    TABLE_SLOTS: Tuple[str, ...] = (
        "raw",            # 标准化后的原始数据          （阶段 2 产生）
        "clean",          # 清洗后数据                  （阶段 3 产生）
        "index_row",      # 行级指标表，粒度：样方×物种  （阶段 4 产生）
        "index_summary",  # 汇总级指标表，粒度：样方/分组（阶段 4 产生）
    )
    # 文本类槽：值为 str
    TEXT_SLOTS: Tuple[str, ...] = (
        "clean_log",      # 清洗日志文本                （阶段 3 产生）
        "data_type",      # 识别出的数据类型            （阶段 2 产生）
    )
    # 其它槽
    OTHER_SLOTS: Tuple[str, ...] = (
        "figures",        # 已生成图片的路径列表 list[Path]（阶段 5 产生）
        "task_meta",      # 任务元信息 dict（阶段 2 起累积：任务名、行数、耗时等）
    )

    # 列表类与字典类槽：两者共用 set_table 写入（它们的值本来就不是数据表）
    LIST_SLOTS: Tuple[str, ...] = ("figures",)
    DICT_SLOTS: Tuple[str, ...] = ("task_meta",)

    # 全部合法键名，供校验使用
    SLOTS: Tuple[str, ...] = TABLE_SLOTS + TEXT_SLOTS + OTHER_SLOTS

    # 单例持有
    _instance: Optional["AppState"] = None

    # ---------------------------------------------------------------------
    # 构造与单例
    # ---------------------------------------------------------------------
    def __init__(self, parent: Optional[QObject] = None) -> None:
        """初始化容器。

        注意：本方法不建目录、不读数据库、不加载任何业务数据，
              只准备空的数据槽，保证程序启动即处于"无数据"的干净状态。
        """
        super().__init__(parent)
        # 内部存储：键名 -> 值。外部一律经 set_*/get_* 访问，禁止直接读写。
        self._data: Dict[str, Any] = {key: None for key in self.SLOTS}
        # 任务元信息在构造时就建好空字典，保证 set_table 累计时不会踩到 None。
        self._data["task_meta"] = {}
        # 任务开始时间，供报告计算总耗时
        self._task_start: float = time.time()

    @classmethod
    def instance(cls) -> "AppState":
        """获取全局唯一实例（首次调用时创建）。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---------------------------------------------------------------------
    # 私有工具
    # ---------------------------------------------------------------------
    @classmethod
    def _validate_key(cls, key: str) -> None:
        """校验键名是否属于固定数据槽。

        为什么要显式校验：写错键名（例如把 "clean" 写成 "cleaned"）不会报错，
        但会导致下游页面永远取不到数据，属于最难排查的一类问题，必须当场拦截。
        """
        if key not in cls.SLOTS:
            raise KeyError(
                "非法的数据槽键名：{0!r}。允许的键名只有：{1}".format(key, "、".join(cls.SLOTS))
            )

    @staticmethod
    def _row_count(value: Any) -> Optional[int]:
        """安全读取表对象的行数；不是表对象时返回 None。"""
        try:
            return int(len(value))
        except Exception:
            return None

    # ---------------------------------------------------------------------
    # 接口（开发方案 3.2）
    # ---------------------------------------------------------------------
    def set_table(self, key: str, df: Any, meta: Optional[dict] = None) -> None:
        """写入数据槽，累计 task_meta，并发出 data_changed 信号。

        参数：
            key  ：TABLE_SLOTS 之一（raw / clean / index_row / index_summary）
                   也接受 figures（图片路径列表）——方案 3.1 明确规定该槽的值是
                   list[Path] 而不是数据表，两者共用本接口写入，以保持接口与规格一致。
            df   ：要写入的内容；表类槽必须是 pandas DataFrame，figures 槽应为列表
            meta ：本次写入的元信息（来源文件、耗时、动作说明等），可为空
        纪律：
            每一次 set_table 都必须同时记录 task_meta 中的行数变化与耗时，
            供步骤条与报告使用（方案第三章 3.3 第 5 条）。
        """
        self._validate_key(key)

        is_table_slot = key in self.TABLE_SLOTS
        is_list_slot = key in self.LIST_SLOTS
        if not (is_table_slot or is_list_slot):
            raise ValueError(
                "数据槽 {0!r} 不接受 set_table 写入：表类槽为 {1}，列表类槽为 {2}，"
                "文本请用 set_text，其它请直接写入 task_meta。".format(
                    key, "、".join(self.TABLE_SLOTS), "、".join(self.LIST_SLOTS))
            )

        # 类型校验：写错类型（例如把字符串当数据表）必须当场报错，
        # 否则会到绘图或导出阶段才以"看不懂的错误"暴露出来。
        if is_list_slot and df is not None and not isinstance(df, (list, tuple)):
            raise ValueError("数据槽 {0!r} 期望列表（图片路径），实际收到 {1}。".format(key, type(df).__name__))
        if is_table_slot and df is not None and DataFrame is not Any and not isinstance(df, DataFrame):
            raise ValueError("数据槽 {0!r} 期望数据表，实际收到 {1}。".format(key, type(df).__name__))

        old_rows = self._row_count(self._data.get(key))
        new_rows = self._row_count(df)

        self._data[key] = df

        # ---- 累计任务元信息（行数变化 + 耗时）----
        task_meta: Dict[str, Any] = self._data.get("task_meta") or {}
        task_meta.setdefault("steps", {})[key] = {
            "rows": new_rows,                       # 本次写入后的行数
            "rows_before": old_rows,                # 写入前的行数（首次为 None）
            "rows_delta": (new_rows - old_rows) if (new_rows is not None and old_rows is not None) else None,
            "at": time.time(),                      # 写入时刻
            "elapsed_total_s": round(time.time() - self._task_start, 3),  # 相对任务开始的累计耗时
        }
        # 报告与状态栏直接取用的常用字段
        if new_rows is not None:
            task_meta["rows_{0}".format(key)] = new_rows
        task_meta["last_key"] = key
        task_meta["last_at"] = time.time()
        if meta:
            task_meta.update(meta)
        self._data["task_meta"] = task_meta

        self.data_changed.emit(key)

    def get_table(self, key: str) -> Optional["DataFrame"]:
        """读取表类数据槽。无数据时返回 None（不抛异常，便于按钮置灰判断）。"""
        self._validate_key(key)
        return self._data.get(key)

    def set_text(self, key: str, text: str) -> None:
        """写入文本类数据槽（clean_log / data_type），并发出 data_changed 信号。

        传入 None 时按空字符串处理，避免界面拼接文案时踩到 None。
        """
        self._validate_key(key)
        if key not in self.TEXT_SLOTS:
            raise ValueError("数据槽 {0!r} 不是文本类数据槽，请改写入 clean_log / data_type。".format(key))
        self._data[key] = "" if text is None else str(text)
        self.data_changed.emit(key)

    def get_text(self, key: str) -> str:
        """读取文本类数据槽。无数据时返回空字符串（保证界面拼接文案不报错）。"""
        self._validate_key(key)
        value = self._data.get(key)
        return "" if value is None else str(value)

    def has(self, key: str) -> bool:
        """判断某数据槽是否已有可用数据，供按钮置灰逻辑调用。

        语义（务必按此实现，避免两处界面判断不一致）：
            只要该槽的值不是 None 即返回 True。
            因此"空的数据表"也算有数据——是否可用由业务层自行判断。
        """
        self._validate_key(key)
        return self._data.get(key) is not None

    def set_list(self, key: str, values: Sequence[Any]) -> None:
        """写入列表类数据槽（figures），并发出 data_changed 信号。

        说明：方案 3.1 规定 figures 槽存放"已生成图片的路径列表"，
              它既不是数据表也不是文本，因此单列一个接口，语义清晰、类型可校验。
        """
        self._validate_key(key)
        if key not in self.LIST_SLOTS:
            raise ValueError("数据槽 {0!r} 不是列表类数据槽，请改用 set_table/set_text。".format(key))
        self._data[key] = list(values)
        self.data_changed.emit(key)

    def get_list(self, key: str) -> List[Any]:
        """读取列表类数据槽（figures）。无数据时返回空列表，调用方不必判空。"""
        self._validate_key(key)
        value = self._data.get(key)
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple)) else []

    def set_meta(self, meta: Dict[str, Any]) -> None:
        """写入任务元信息字典（task_meta 槽）。

        为什么单列一个接口：task_meta 按方案 3.1 的定义是**任务元信息字典**，
        既不是数据表也不是文本，用 set_table/set_text 都不合适（类型校验会直接拒绝）。
        步骤条、报告与历史任务都要读写它，因此给它一对语义明确的接口。
        """
        self._validate_key("task_meta")
        self._data["task_meta"] = dict(meta or {})
        self.data_changed.emit("task_meta")

    def get_meta(self) -> Dict[str, Any]:
        """读取任务元信息字典（返回副本，避免外部误改内部状态）。"""
        self._validate_key("task_meta")
        value = self._data.get("task_meta")
        return dict(value) if isinstance(value, dict) else {}

    def update_meta(self, **fields: Any) -> Dict[str, Any]:
        """增量更新任务元信息（只覆盖传入的键），返回更新后的字典。"""
        meta = self.get_meta()
        meta.update(fields)
        self.set_meta(meta)
        return meta

    def clear(self, key: str) -> None:
        """清空指定数据槽，并发出 data_changed 信号。

        清空 task_meta 时重建为空字典而不是 None，
        否则后续 set_table 累计元信息时会踩到 None。
        """
        self._validate_key(key)
        self._data[key] = {} if key == "task_meta" else None
        self.data_changed.emit(key)

    # ---------------------------------------------------------------------
    # 重置
    # ---------------------------------------------------------------------
    def reset(self) -> None:
        """清空全部数据槽（用于"新建任务"或测试用例前置清理）。

        本方法只重置数据，不重置用户偏好；用户偏好由 core/settings.py 负责。
        """
        for key in self.SLOTS:
            self._data[key] = {} if key == "task_meta" else None
        self._task_start = time.time()
        self.data_changed.emit("*")
