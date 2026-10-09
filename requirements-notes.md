# EcoTidy 依赖与解释器说明

> `requirements.txt` 只放纯 ASCII 的版本锁定（原因见下），中文说明统一放在本文件。

## 一、解释器版本

| 项 | 说明 |
|---|---|
| **实测通过** | **Python 3.9.13**（本机全部环境均为该版本，454 项测试通过） |
| 可用范围 | `>= 3.9, < 3.12` |
| 3.10 / 3.11 | 满足下列所有包的版本要求，可用但**未实测** |
| 3.12 及以上 | **不可用**：PySide6 6.5.3 与 PyInstaller 5.13.2 都不支持 |

> 更正记录：早期版本文档写「解释器统一锁定为 Python 3.10」，与实际情况不符
> （本机没有 3.10，项目一直在 3.9.13 上开发与验证）。已按事实更正，避免误导。

**代码必须保持 3.9 兼容。** 已知陷阱：`Path.write_text(newline=...)` 是 3.10+
才有的参数，在 3.9 会直接 `TypeError`，写文件请用
`open(path, "w", newline="")`。

## 二、为什么 `requirements.txt` 里不能写中文

pip 在 Windows 上读取 requirements 文件时用的是**系统区域编码**
（中文系统是 GBK），不是 UTF-8。文件里只要有中文注释，pip 在**解析阶段**就会崩：

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0xae in position 103:
illegal multibyte sequence
```

这个错误发生在安装任何包之前，界面上的表现就是"装不上，报一串看不懂的错"。
因此把中文说明全部移到本文件，`requirements.txt` 保持纯 ASCII。

## 三、版本锁定清单

| 包 | 版本 | 用途 |
|---|---|---|
| PySide6 | 6.5.3 | 界面框架（官方支持 Python 3.7 ~ 3.11） |
| pandas | 2.0.3 | 数据处理 |
| numpy | 1.24.4 | 数值计算 |
| matplotlib | 3.7.3 | 绘图 |
| openpyxl | 3.1.2 | Excel 读写 |
| pytest | 7.4.4 | 测试 |
| pyinstaller | 5.13.2 | 打包（仅发布阶段用，运行期不需要） |

## 四、安装

```
"<你的环境>\Scripts\python.exe" -m pip install -r requirements.txt
```

国内下载慢可加镜像：

```
-i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 五、Visual Studio 用户注意

VS 每为一个工作区**新建**环境（`env1` / `env2` / `env3` …）都是**空的**，
里面没有装任何依赖。换环境后必须重新执行上面的安装命令，否则会报：

```
ModuleNotFoundError: No module named 'PySide6'
```

`main.py` 现在会在启动时自检依赖，缺包时直接打印中文指引
（含"给当前解释器装依赖"的完整命令）并以退出码 3 结束，
不会再甩一段英文堆栈。

**建议：只保留一个环境。** 本项目当前只使用 `env`（依赖完整）。
多余的 `env1` / `env2` / `env3` 已删除；若 VS 又自动新建，请在
「Python 环境」窗口里删掉它并把项目解释器切回 `env`。
