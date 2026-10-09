# =============================================================================
# 样本与示例数据生成脚本（阶段 0 使用一次）
# 不是业务逻辑代码，不属于冻结的工程结构，仅用于可复现地重建 fixtures。
#
# 生成 5 个文件：
#   tests/samples/sample_quadrat.csv    UTF-8 带 BOM，含各类脏数据
#   tests/samples/sample_sensor.xlsx    时间列为 Excel 序列号，含突变/长缺口/乱序
#   tests/samples/sample_plain_gbk.txt  GBK 制表符分隔，无时间列
#   static/demo/demo_quadrat.csv        干净样方群落数据（12 样方 × 6 物种 × 2 层）
#   static/demo/demo_sensor.csv         干净传感器时序数据（2024-06 全月逐时）
# =============================================================================
import math
import random
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\lenovo\source\repos\PythonApplication1")
SAMPLES = ROOT / "tests" / "samples"
DEMO = ROOT / "static" / "demo"
SAMPLES.mkdir(parents=True, exist_ok=True)
DEMO.mkdir(parents=True, exist_ok=True)


# =============================================================================
# 一、sample_quadrat.csv —— UTF-8 带 BOM，样方群落脏数据样本
# =============================================================================
def build_sample_quadrat() -> pd.DataFrame:
    """构造样方群落脏数据。

    刻意植入的脏数据（与开发方案第八章表格逐条对应）：
      1. 首列名带 BOM：由 encoding="utf-8-sig" 写出，文件头为 \\ufeff样方号
      2. 株数含负数（-3、-1）
      3. 盖度含 >100（108、118、121）与 0-1 小数制（0.12、0.35）混用
      4. 完全重复行（Q2 油松、Q4 山杏 各重复一次）
      5. 部分缺失值（株数 1 个、胸径 2 个、盖度 2 个）
    数据特征（供后续阶段单测断言使用）：
      6 个样方 × 6 个物种 = 36 行；18 行无胸径（灌木/草本，天然如此）
    """
    species = ["油松", "辽东栎", "山杏", "胡枝子", "披针苔草", "蒿类"]
    # 每个物种在每个样方中的株数基准（刻意设计得大小分明，便于重要值排序验证）
    base = {
        "油松":     [12, 8, 5, 3, 2, 1],
        "辽东栎":   [4, 10, 7, 6, 3, 2],
        "山杏":     [3, 5, 9, 8, 4, 3],
        "胡枝子":   [6, 4, 8, 11, 7, 5],
        "披针苔草": [15, 12, 10, 14, 18, 9],
        "蒿类":     [7, 6, 5, 9, 10, 12],
    }
    # 胸径：仅乔木（油松、辽东栎、山杏）有记录，其余为缺失（纳米 NaN 前的 None）
    dbh_base = {
        "油松":     [18.5, 15.2, 12.8, 9.6, 7.4, 5.2],
        "辽东栎":   [14.2, 19.6, 16.1, 13.5, 10.2, 8.1],
        "山杏":     [9.8, 11.4, 13.7, 12.2, 8.6, 6.3],
    }
    # 盖度基准：前三个样方为百分制，后三个样方为 0-1 小数制（制造量纲混用）
    # 注意：若样方数改为 6 以外的值，本表必须同步扩列，否则构造阶段即报索引错误。
    cover_base = {
        "油松":     [62, 55, 48, 70, 58, 44],
        "辽东栎":   [40, 58, 52, 36, 61, 49],
        "山杏":     [25, 33, 45, 0.30, 0.41, 0.36],
        "胡枝子":   [0.12, 0.35, 0.28, 0.33, 0.47, 0.26],   # 小数制
        "披针苔草": [0.45, 0.52, 0.38, 0.55, 0.61, 0.42],   # 小数制
        "蒿类":     [0.10, 0.18, 0.22, 0.15, 0.24, 0.19],   # 小数制
    }

    rows = []
    for qi, qname in enumerate(["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]):
        for sp in species:
            count = base[sp][qi]
            dbh = dbh_base.get(sp, [None] * 6)[qi]
            if sp in ("油松", "辽东栎", "山杏"):
                cover = cover_base[sp][qi]
            else:
                # 后三个样方的林下植被盖度用小数制，前三个样方用百分制（同一列两种量纲）
                cover = cover_base[sp][qi] if qi >= 3 else round(cover_base[sp][qi] * 100, 1)
            rows.append({"样方号": qname, "物种": sp, "株数": count, "胸径": dbh, "盖度": cover})

    df = pd.DataFrame(rows)

    # --- 植入脏数据：株数为负 ---
    df.loc[df["样方号"].eq("Q2") & df["物种"].eq("油松"), "株数"] = -3
    df.loc[df["样方号"].eq("Q4") & df["物种"].eq("辽东栎"), "株数"] = -1
    # --- 植入脏数据：盖度 >100 ---
    df.loc[df["样方号"].eq("Q1") & df["物种"].eq("油松"), "盖度"] = 108
    df.loc[df["样方号"].eq("Q2") & df["物种"].eq("辽东栎"), "盖度"] = 118
    df.loc[df["样方号"].eq("Q3") & df["物种"].eq("山杏"), "盖度"] = 121
    # --- 植入脏数据：缺失值 ---
    df.loc[df["样方号"].eq("Q3") & df["物种"].eq("蒿类"), "株数"] = np.nan
    df.loc[df["样方号"].eq("Q1") & df["物种"].eq("山杏"), "胸径"] = np.nan
    df.loc[df["样方号"].eq("Q5") & df["物种"].eq("辽东栎"), "胸径"] = np.nan
    df.loc[df["样方号"].eq("Q5") & df["物种"].eq("胡枝子"), "盖度"] = np.nan
    df.loc[df["样方号"].eq("Q6") & df["物种"].eq("蒿类"), "盖度"] = np.nan
    # --- 植入脏数据：完全重复行 ---
    dup_a = df[(df["样方号"] == "Q2") & (df["物种"] == "山杏")]
    dup_b = df[(df["样方号"] == "Q4") & (df["物种"] == "胡枝子")]
    df = pd.concat([df, dup_a, dup_b], ignore_index=True)

    # 列顺序固定为：样方号 / 物种 / 株数 / 胸径 / 盖度
    df = df[["样方号", "物种", "株数", "胸径", "盖度"]]
    return df


# =============================================================================
# 二、sample_sensor.xlsx —— 传感器时序脏数据样本
# =============================================================================
def build_sample_sensor() -> pd.DataFrame:
    """构造传感器时序脏数据。

    刻意植入的脏数据（与开发方案第八章表格逐条对应）：
      1. 时间列为 Excel 序列号数字（如 45413.0），需要 origin="1899-12-30", unit="D"
      2. 单点突变值：气温 58.7、相对湿度 -15.0（违背物理常识的孤立极值）
      3. 连续 10 点长缺口：既有整行删除造成的断测，也有单列为 NaN 的缺测
      4. 乱序时间戳：第 50/51 两行与第 80/81 两行时间互换，并整体后置
    列：时间 / 气温 / 相对湿度 / 土壤温度
    """
    random.seed(20240501)
    np.random.seed(20240501)

    start = pd.Timestamp("2024-05-01 00:00:00")
    times = [start + pd.Timedelta(hours=i) for i in range(240)]  # 10 天逐小时

    air, hum, soil = [], [], []
    for t in times:
        # 气温：日变化正弦 + 缓慢升温趋势 + 随机扰动（生态数据的非正态、季节性特征）
        diurnal = 6.0 * math.sin((t.hour - 9) / 24.0 * 2 * math.pi)
        trend = 0.02 * (t - start).total_seconds() / 3600.0
        air.append(round(18.0 + diurnal + trend + random.gauss(0, 0.6), 1))
        # 相对湿度：与气温反相
        hum.append(round(62.0 - 0.8 * diurnal + random.gauss(0, 1.8), 1))
        # 土壤温度：日变化幅度小、滞后于气温
        soil.append(round(15.5 + 1.5 * math.sin((t.hour - 13) / 24.0 * 2 * math.pi) + random.gauss(0, 0.2), 1))

    df = pd.DataFrame({"时间": times, "气温": air, "相对湿度": hum, "土壤温度": soil})

    # --- 植入脏数据：连续 10 点长缺口（模拟传感器故障 10 小时）---
    gap_idx = list(range(100, 110))
    df = df.drop(index=gap_idx).reset_index(drop=True)
    # --- 植入脏数据：单列为 NaN 的短缺口（连续 2 点，用于验证 max_gap=3 内的线性插值）---
    df.loc[40:41, "气温"] = np.nan
    df.loc[150:150, "相对湿度"] = np.nan
    df.loc[200:203, "土壤温度"] = np.nan
    # --- 植入脏数据：单点突变值 ---
    df.loc[70, "气温"] = 58.7
    df.loc[71, "相对湿度"] = -15.0
    # --- 植入脏数据：乱序时间戳 ---
    i, j = 50, 51
    df.loc[[i, j], "时间"] = df.loc[[j, i], "时间"].to_numpy()
    i, j = 80, 81
    df.loc[[i, j], "时间"] = df.loc[[j, i], "时间"].to_numpy()

    # 时间列转为 Excel 序列号（1899-12-30 为 0 点基准）
    # 精度说明（实测踩坑，勿改）：
    #   不要对序列号做 round(6)。6 位小数会让整点时间产生 ±28.8 毫秒偏移，
    #   解析回来变成 01:00:00.028799，破坏"统一输出 YYYY-MM-DD HH:MM:SS"的规格。
    #   改用 (t - origin) / 1 天 的秒数除法，双精度浮点对整天数的表示是精确的，
    #   解析回来的误差仅约 1 微秒，阶段 2 解析器再统一取整到秒即可完全消除。
    origin = pd.Timestamp("1899-12-30")
    df["时间"] = (df["时间"] - origin).dt.total_seconds() / 86400.0
    return df


# =============================================================================
# 三、sample_plain_gbk.txt —— GBK 制表符分隔，无时间列
# =============================================================================
def build_sample_plain_gbk() -> pd.DataFrame:
    """构造普通实验统计脏数据（GBK 编码、制表符分隔、无时间列）。

    验证点：
      1. 编码探测必须能落到 gb18030（否则中文列名乱码）
      2. 该数据本就没有时间列，解析器不得强制补出整列 NaT
      3. 含少量缺失值与一个异常值，用于清洗阶段
    列：处理组 / 重复 / 温度 / 株高 / 生物量
    """
    random.seed(7)
    rows = []
    for group, center in [("对照", 25.0), ("增温", 27.5), ("增温+降水", 27.0), ("降水", 25.4)]:
        for rep in range(1, 6):
            temp = round(center + random.uniform(-1.2, 1.2), 1)
            height = round(center * 1.6 + random.uniform(-4, 4), 1)
            biomass = round(height * 0.42 + random.uniform(-1.5, 1.5), 2)
            rows.append({"处理组": group, "重复": rep, "温度": temp, "株高": height, "生物量": biomass})
    df = pd.DataFrame(rows)
    # 植入脏数据：缺失值与异常值
    df.loc[3, "株高"] = np.nan
    df.loc[9, "生物量"] = np.nan
    df.loc[12, "温度"] = -40.0
    return df


# =============================================================================
# 四、static/demo/demo_quadrat.csv —— 干净、规范、能直接跑出漂亮结果
# =============================================================================
def build_demo_quadrat() -> pd.DataFrame:
    """构造内置示例样方数据（干净无脏数据）。

    设计目标：载入示例数据后，重要值排序明显、多样性指数差异清晰，
              出图效果漂亮，让用户在 10 秒内看懂软件价值。
    规模：12 个样方 × 6 个物种 = 72 行；每物种在每个样方都出现一次。
    样方面积：10 m × 10 m = 100 m²（供密度指标使用）。
    """
    species = ["油松", "辽东栎", "山杏", "胡枝子", "披针苔草", "蒿类"]
    # 沿环境梯度设计：Q1-Q4 乔木占优，Q9-Q12 灌木草本占优，保证多样性指数有梯度
    matrix = {
        "油松":     [16, 14, 12, 11, 9, 7, 6, 5, 4, 3, 2, 1],
        "辽东栎":   [10, 12, 13, 14, 12, 10, 8, 7, 5, 4, 3, 2],
        "山杏":     [5, 6, 8, 9, 11, 12, 11, 9, 7, 5, 4, 3],
        "胡枝子":   [4, 5, 6, 8, 10, 12, 14, 15, 13, 11, 8, 6],
        "披针苔草": [6, 7, 8, 10, 13, 15, 18, 21, 24, 26, 22, 18],
        "蒿类":     [3, 4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 27],
    }
    dbh_map = {  # 乔木胸径（cm），随样方号略有起伏
        "油松":     [21.5, 20.8, 19.6, 18.9, 17.4, 16.2, 15.1, 14.3, 13.2, 12.1, 10.8, 9.6],
        "辽东栎":   [17.2, 18.1, 18.9, 19.5, 18.3, 16.8, 15.4, 14.1, 12.6, 11.2, 9.8, 8.5],
        "山杏":     [10.4, 11.1, 11.8, 12.4, 13.1, 13.6, 12.9, 12.1, 11.2, 10.3, 9.4, 8.6],
    }
    rows = []
    for qi in range(12):
        qname = "Q{0}".format(qi + 1)
        for sp in species:
            count = matrix[sp][qi]
            dbh = dbh_map[sp][qi] if sp in dbh_map else None
            # 林下植被盖度用百分制（干净数据统一量纲，避免示例数据触发异常检测）
            if sp == "油松":
                cover = round(dbh_map["油松"][qi] * 2.6, 1)
            elif sp == "辽东栎":
                cover = round(dbh_map["辽东栎"][qi] * 2.4, 1)
            elif sp == "山杏":
                cover = round(dbh_map["山杏"][qi] * 2.8, 1)
            elif sp == "胡枝子":
                cover = round(4.0 + 3.0 * qi, 1)
            elif sp == "披针苔草":
                cover = round(5.0 + 4.2 * qi, 1)
            else:
                cover = round(2.5 + 3.8 * qi, 1)
            rows.append({"样方号": qname, "物种": sp, "株数": count, "胸径": dbh, "盖度": min(cover, 95.0)})
    return pd.DataFrame(rows)[["样方号", "物种", "株数", "胸径", "盖度"]]


# =============================================================================
# 五、static/demo/demo_sensor.csv —— 干净传感器时序（2024 年 6 月全月逐时）
# =============================================================================
def build_demo_sensor() -> pd.DataFrame:
    """构造内置示例传感器数据（干净无脏数据）。

    规模：2024-06-01 ~ 2024-06-30 逐小时 = 720 行，时间已排好序、无缺口、无异常值。
    列：时间 / 气温 / 相对湿度 / 土壤温度 / 光合有效辐射
    """
    random.seed(20240601)
    times = pd.date_range("2024-06-01 00:00:00", "2024-06-30 23:00:00", freq="h")
    air, hum, soil, par = [], [], [], []
    for t in times:
        doy = t.dayofyear
        # 日变化 + 月内缓慢升温
        diurnal = 7.5 * math.sin((t.hour - 9) / 24.0 * 2 * math.pi)
        seasonal = 0.12 * (doy - 152)
        air.append(round(21.0 + diurnal + seasonal + random.gauss(0, 0.5), 1))
        hum.append(round(min(95.0, max(28.0, 68.0 - 1.1 * diurnal + random.gauss(0, 1.6))), 1))
        soil.append(round(18.0 + 2.0 * math.sin((t.hour - 13) / 24.0 * 2 * math.pi) + seasonal * 0.5 + random.gauss(0, 0.15), 1))
        # 光合有效辐射：夜间为 0，白天正态峰
        if 6 <= t.hour <= 18:
            par.append(round(max(0.0, 1500.0 * math.sin((t.hour - 6) / 12.0 * math.pi) + random.gauss(0, 60)), 1))
        else:
            par.append(0.0)
    return pd.DataFrame({"时间": times.strftime("%Y-%m-%d %H:%M:%S"), "气温": air, "相对湿度": hum, "土壤温度": soil, "光合有效辐射": par})


# =============================================================================
# 六、写盘
# =============================================================================
def main() -> None:
    # 1. 样方群落脏数据样本：UTF-8 带 BOM（首列名将带 \ufeff，用于验证编码探测）
    q = build_sample_quadrat()
    q.to_csv(SAMPLES / "sample_quadrat.csv", index=False, encoding="utf-8-sig")
    print("[1/5] sample_quadrat.csv   ->", len(q), "行", len(q.columns), "列")

    # 2. 传感器时序脏数据样本：xlsx，时间列为 Excel 序列号
    s = build_sample_sensor()
    s.to_excel(SAMPLES / "sample_sensor.xlsx", index=False, sheet_name="传感器数据")
    print("[2/5] sample_sensor.xlsx   ->", len(s), "行", len(s.columns), "列")

    # 3. 普通统计脏数据样本：GB18030（兼容 GBK），制表符分隔，无时间列
    p = build_sample_plain_gbk()
    p.to_csv(SAMPLES / "sample_plain_gbk.txt", index=False, encoding="gb18030", sep="\t")
    print("[3/5] sample_plain_gbk.txt ->", len(p), "行", len(p.columns), "列")

    # 4. 内置示例数据：干净样方群落
    dq = build_demo_quadrat()
    dq.to_csv(DEMO / "demo_quadrat.csv", index=False, encoding="utf-8-sig")
    print("[4/5] demo_quadrat.csv     ->", len(dq), "行", len(dq.columns), "列")

    # 5. 内置示例数据：干净传感器时序
    ds = build_demo_sensor()
    ds.to_csv(DEMO / "demo_sensor.csv", index=False, encoding="utf-8-sig")
    print("[5/5] demo_sensor.csv      ->", len(ds), "行", len(ds.columns), "列")


if __name__ == "__main__":
    main()
