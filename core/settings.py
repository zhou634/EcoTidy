# -*- coding: utf-8 -*-
"""
用户偏好持久化（开发方案 第五章 5.1、5.4，阶段 1 交付）

为什么必须有这个文件：
    用户第二次打开软件时，不应该再重新设置导出目录、清洗参数、窗口大小。
    所有偏好经 QSettings 落地，Windows 下写入：
        注册表 HKEY_CURRENT_USER\\Software\\EcoTidy\\EcoTidy
        文件   %APPDATA%\\EcoTidy\\EcoTidy.ini（同时写，便于排查与手工清理）

强制纪律：
    1. 只有"用户偏好"走本模块；业务数据一律走 core/app_state.py，两者不得混用。
    2. 本模块在缺少 QApplication 的情况下也能工作（QSettings 只要求 QCoreApplication），
       但首次使用前必须已创建 QApplication，否则 Qt 会告警。
    3. 读写都必须带默认值，首次运行不得因为取不到值而报错。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QByteArray, QSettings

from utils import paths

# QSettings 的组织名 / 应用名，统一取自 utils/paths.py（全工程单一来源）
ORG_NAME = paths.APP_NAME
APP_NAME = paths.APP_NAME

# ---------------------------------------------------------------------------
# 偏好键名（集中定义，禁止在页面里手写字符串，避免拼错导致"记住了但读不回来"）
# ---------------------------------------------------------------------------
KEY_GEOMETRY = "window/geometry"          # 窗口尺寸与位置（QByteArray）
KEY_WINDOW_STATE = "window/state"         # 窗口最大化等状态（QByteArray）
KEY_HELP_GEOMETRY = "help/geometry"       # 帮助面板尺寸
KEY_WELCOME_DONE = "startup/welcome_done"  # 首次启动引导是否已完成
KEY_MIGRATION_NOTICE_DONE = "startup/migration_notice_done"  # 更名提示是否已展示
KEY_LANGUAGE = "ui/language"               # 界面语言（zh / en）
KEY_LAST_DIR = "paths/last_dir"           # 最近一次打开文件所在目录
KEY_LAST_OPEN_FILES = "paths/last_open_files"  # 最近一次选择的文件列表（重启后回填列表）
KEY_EXPORT_DIR = "paths/export_dir"       # 上次导出目录
KEY_LAST_PAGE = "ui/last_page"            # 上次停留的页面序号
KEY_CLEAN_PARAMS = "params/clean"         # 上次清洗参数（JSON 字符串）
KEY_CALC_PARAMS = "params/calc"           # 上次指标计算参数（JSON 字符串）
KEY_PLOT_CHOICES = "params/plot"          # 上次绘图选择（JSON 字符串）

# 清洗参数默认值（开发方案 5.4：默认参数必须开箱即用）
DEFAULT_CLEAN_PARAMS: Dict[str, Any] = {
    "check_outlier": True,      # 启用生态异常值检测
    "fill_missing": True,       # 启用缺失值填充
    "drop_duplicate": True,     # 启用自动去重
    "mad_k": 3.0,               # 突变检测阈值倍数（默认 3 倍 MAD）
    "max_gap": 3,               # 最大插值间隙（连续缺测不超过几个点才插值）
}

# 指标计算参数默认值（阶段 4）
# 样方面积只影响"密度"一项；昼夜划分影响环境时序统计的昼夜均值差。
DEFAULT_CALC_PARAMS: Dict[str, Any] = {
    "quadrat_area": 1.0,        # 样方面积（m²）
    "day_start": 6,             # 白天开始时刻（含）
    "day_end": 18,              # 白天结束时刻（不含）
    "germination_day": 7,       # 萌发势统计天数
    "group_by": "日",            # 环境时序统计分组维度：日 / 时
}


class Settings:
    """用户偏好的读写门面。单例由 instance() 提供。"""

    _instance: Optional["Settings"] = None

    def __init__(self) -> None:
        # QSettings 的默认格式在 Windows 下是注册表；这里显式指定 IniFormat，
        # 让配置文件落在我们自己的数据目录里，出问题时用户能直接看到并删除。
        #
        # 为什么用 setPath 指定目录、而不是让 Qt 去猜 %APPDATA%（实测踩坑）：
        #     %APPDATA% 不可写的机器上（本机就是），QSettings 静默放弃写入，
        #     status() 仍返回 NoError，用户表现为"软件记不住我的设置"。
        #     而我们的数据目录是**探测过可写性**的（见 utils/paths.py），
        #     把配置放到同一个目录，配置与数据的可写性就永远一致。
        try:
            from utils import paths

            paths.ensure_dirs()
            QSettings.setPath(QSettings.Format.IniFormat,
                              QSettings.Scope.UserScope, str(paths.DATA_DIR))
        except Exception:  # noqa: BLE001 - 路径模块不可用时退回 Qt 默认位置
            pass
        self._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG_NAME, APP_NAME)
        # 只提示一次：避免每写一项偏好就刷一行警告
        self._warned_persist_failure = False

    @classmethod
    def instance(cls) -> "Settings":
        """获取全局唯一实例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---------------------------------------------------------------------
    # 落盘失败检测
    # ---------------------------------------------------------------------
    def _sync_or_warn(self) -> None:
        """落盘偏好，并在真正写不进去时给出可操作的提示。

        为什么需要这段检查（实测踩坑）：
            QSettings 在目录不可写（被安全软件限制、权限不足、注册表策略锁定）时，
            status() 仍可能返回 NoError，写操作被静默丢弃。用户的表现就是
            "软件记不住我的设置"，而且完全没有任何线索。这里主动读取 status()，
            一旦不是 NoError 就写日志并在控制台提示一次，便于定位。
        """
        self._qs.sync()
        try:
            status = self._qs.status()
        except Exception:
            return
        if status == QSettings.Status.NoError:
            return
        if self._warned_persist_failure:
            return
        self._warned_persist_failure = True
        message = (
            "偏好设置无法保存（QSettings 状态：{0}）。\n"
            "预计原因：配置目录 {1} 不可写，或被安全软件限制。\n"
            "影响：上次导出目录、清洗参数、窗口大小位置将不会被记住（软件仍可正常使用）。\n"
            "怎么办：确认当前用户对该目录有写入权限，"
            "或把安全软件的拦截规则中对本软件的写入限制解除后重启软件。".format(
                status, Path(self._qs.fileName()).parent))
        try:
            from utils import paths

            paths.ensure_dirs()
            with open(paths.TEMP_DIR / "settings_warning.log", "a", encoding="utf-8") as handle:
                handle.write(message + "\n" + "-" * 60 + "\n")
        except Exception:
            pass
        print("[偏好保存失败] " + message, file=sys.stderr)

    # ---------------------------------------------------------------------
    # 基础读写
    # ---------------------------------------------------------------------
    def get(self, key: str, default: Any = None, value_type: Optional[type] = None) -> Any:
        """读取偏好。

        参数：
            key        ：偏好键名，建议使用本模块顶部的 KEY_* 常量
            default    ：取不到时的默认值（首次运行必须靠它兜底）
            value_type ：需要类型转换时传入（如 int / bool）
        """
        if value_type is not None:
            value = self._qs.value(key, default, type=value_type)
        else:
            value = self._qs.value(key, default)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        """写入偏好并立即落盘。

        立即 sync() 的原因：程序被强制结束（任务管理器结束进程）时，
        未 sync 的写入会丢失，用户会觉得"软件没记住我的设置"。
        """
        self._qs.setValue(key, value)
        self._sync_or_warn()

    def remove(self, key: str) -> None:
        """删除某个偏好项。"""
        self._qs.remove(key)
        self._sync_or_warn()

    def clear_all(self) -> None:
        """清空全部偏好（供"恢复默认设置"使用，阶段 8 接入菜单）。"""
        self._qs.clear()
        self._sync_or_warn()

    # ---------------------------------------------------------------------
    # 窗口状态
    # ---------------------------------------------------------------------
    def save_geometry(self, geometry: QByteArray) -> None:
        """保存主窗口尺寸与位置。"""
        self.set(KEY_GEOMETRY, geometry)

    def load_geometry(self) -> Optional[QByteArray]:
        """读取主窗口尺寸与位置；从未保存过时返回 None。

        说明：显式指定 type=QByteArray，避免 IniFormat 下把二进制值当字符串读回来
              导致 restoreGeometry 无声失败（窗口大小"记不住"的典型原因）。
        """
        try:
            value = self._qs.value(KEY_GEOMETRY, None, type=QByteArray)
        except TypeError:
            # 未保存过该键时，部分 Qt 版本对 type 转换会抛 TypeError，此时按"没有记录"处理
            return None
        if isinstance(value, QByteArray) and not value.isEmpty():
            return value
        return None

    def save_window_state(self, state: QByteArray) -> None:
        """保存窗口状态（是否最大化）。"""
        self.set(KEY_WINDOW_STATE, state)

    def load_window_state(self) -> Optional[QByteArray]:
        """读取窗口状态（是否最大化）；从未保存过时返回 None。"""
        try:
            value = self._qs.value(KEY_WINDOW_STATE, None, type=QByteArray)
        except TypeError:
            return None
        if isinstance(value, QByteArray) and not value.isEmpty():
            return value
        return None

    def save_help_geometry(self, geometry: QByteArray) -> None:
        """保存帮助面板尺寸（用户不希望每次都被重置）。"""
        self.set(KEY_HELP_GEOMETRY, geometry)

    def load_help_geometry(self) -> Optional[QByteArray]:
        """读取帮助面板尺寸；从未保存过时返回 None。"""
        try:
            value = self._qs.value(KEY_HELP_GEOMETRY, None, type=QByteArray)
        except TypeError:
            return None
        if isinstance(value, QByteArray) and not value.isEmpty():
            return value
        return None

    # ---------------------------------------------------------------------
    # 首次启动引导
    # ---------------------------------------------------------------------
    @property
    def welcome_done(self) -> bool:
        """首次启动引导是否已经完成过。"""
        return bool(self.get(KEY_WELCOME_DONE, False, bool))

    @welcome_done.setter
    def welcome_done(self, value: bool) -> None:
        self.set(KEY_WELCOME_DONE, bool(value))

    @property
    def migration_notice_done(self) -> bool:
        """更名提示（旧数据目录仍在）是否已经展示过。"""
        return bool(self.get(KEY_MIGRATION_NOTICE_DONE, False, bool))

    @migration_notice_done.setter
    def migration_notice_done(self, value: bool) -> None:
        self.set(KEY_MIGRATION_NOTICE_DONE, bool(value))

    @property
    def language(self) -> str:
        """界面语言（zh / en）。非法值回落到中文。"""
        from utils import i18n

        value = str(self.get(KEY_LANGUAGE, i18n.DEFAULT_LANGUAGE, str) or "")
        return value if value in i18n.ALL_LANGUAGES else i18n.DEFAULT_LANGUAGE

    @language.setter
    def language(self, value: str) -> None:
        from utils import i18n

        self.set(KEY_LANGUAGE, value if value in i18n.ALL_LANGUAGES
                 else i18n.DEFAULT_LANGUAGE)

    # ---------------------------------------------------------------------
    # 目录记忆（5.1：最近打开的文件目录、上次导出目录）
    # ---------------------------------------------------------------------
    @property
    def last_dir(self) -> str:
        """最近一次打开文件所在目录。取不到时返回空串，由调用方决定回退到哪儿。"""
        return str(self.get(KEY_LAST_DIR, ""))

    @last_dir.setter
    def last_dir(self, value: str) -> None:
        self.set(KEY_LAST_DIR, str(value))

    @property
    def export_dir(self) -> str:
        """上次导出目录。取不到时返回空串，由调用方回退到 utils.paths.EXPORT_DIR。"""
        return str(self.get(KEY_EXPORT_DIR, ""))

    @export_dir.setter
    def export_dir(self, value: str) -> None:
        self.set(KEY_EXPORT_DIR, str(value))

    def save_last_open_files(self, files: list) -> None:
        """保存最近一次选择的文件绝对路径列表（阶段 2 的导入页重启后回填用）。"""
        self.set(KEY_LAST_OPEN_FILES, json.dumps([str(p) for p in files], ensure_ascii=False))

    def load_last_open_files(self) -> list:
        """读取最近一次选择的文件列表；解析失败时返回空列表，不抛异常。"""
        raw = self.get(KEY_LAST_OPEN_FILES, "")
        if not raw:
            return []
        try:
            value = json.loads(str(raw))
            return value if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []

    # ---------------------------------------------------------------------
    # 页面与参数记忆
    # ---------------------------------------------------------------------
    @property
    def last_page(self) -> int:
        """上次停留的页面序号（0~4）；非法值一律回落到 0。"""
        try:
            index = int(self.get(KEY_LAST_PAGE, 0, int))
        except (TypeError, ValueError):
            return 0
        return index if 0 <= index <= 4 else 0

    @last_page.setter
    def last_page(self, value: int) -> None:
        self.set(KEY_LAST_PAGE, int(value))

    def save_clean_params(self, params: Dict[str, Any]) -> None:
        """保存清洗参数（阶段 3 使用）。缺失的键由 load_clean_params 补齐默认值。"""
        self.set(KEY_CLEAN_PARAMS, json.dumps(params, ensure_ascii=False))

    def load_clean_params(self) -> Dict[str, Any]:
        """读取清洗参数；与默认值合并，保证新增参数在旧配置下也有值。"""
        return self._load_params(KEY_CLEAN_PARAMS, DEFAULT_CLEAN_PARAMS)

    def save_calc_params(self, params: Dict[str, Any]) -> None:
        """保存指标计算参数（阶段 4 使用：样方面积、昼夜时段划分、萌发天数、分组维度）。"""
        self.set(KEY_CALC_PARAMS, json.dumps(params, ensure_ascii=False))

    def load_calc_params(self) -> Dict[str, Any]:
        """读取指标计算参数；解析失败时返回空字典，由阶段 4 的默认值兜底。"""
        return self._load_params(KEY_CALC_PARAMS, DEFAULT_CALC_PARAMS)

    def save_plot_choices(self, choices: Dict[str, Any]) -> None:
        """保存绘图选择（阶段 5 使用：数据源、图表类型、X/Y 字段）。"""
        self.set(KEY_PLOT_CHOICES, json.dumps(choices, ensure_ascii=False))

    def load_plot_choices(self) -> Dict[str, Any]:
        """读取绘图选择；解析失败时返回空字典，由阶段 5 的推荐逻辑兜底。"""
        return self._load_params(KEY_PLOT_CHOICES, {})

    # ---------------------------------------------------------------------
    # 内部工具
    # ---------------------------------------------------------------------
    def _load_params(self, key: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
        """读取 JSON 形式的参数字典并与默认值合并。

        为什么必须与默认值合并：软件升级后可能新增参数，
        旧配置文件里没有该键，合并可保证升级后不出现"参数丢失"的空洞。
        """
        params: Dict[str, Any] = dict(defaults)
        raw = self.get(key, "")
        if not raw:
            return params
        try:
            value = json.loads(str(raw))
        except (ValueError, TypeError):
            return params
        if isinstance(value, dict):
            params.update(value)
        return params


# ---------------------------------------------------------------------------
# 模块级便捷入口
# ---------------------------------------------------------------------------
# 页面里写 settings.instance().export_dir 太长，这里提供同名短函数；
# 注意：这些函数仍然读写同一个单例，不产生第二份配置。
def instance() -> Settings:
    """获取全局唯一 Settings 实例。"""
    return Settings.instance()


def get(key: str, default: Any = None, value_type: Optional[type] = None) -> Any:
    """读取任意偏好项。"""
    return Settings.instance().get(key, default, value_type)


def set_value(key: str, value: Any) -> None:  # noqa: A001 - 与 QSettings.setValue 语义一致的短名
    """写入任意偏好项。"""
    Settings.instance().set(key, value)
