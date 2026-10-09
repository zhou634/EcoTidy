@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ===========================================================================
REM  EcoTidy - 推送到 GitHub
REM
REM  用法：双击本文件。
REM
REM  两种情况都会自动处理：
REM    · 还没建仓库 -> 先在 GitHub 网页新建一个**空**仓库（不要勾
REM      README／LICENSE／.gitignore），确认下面 REPO_URL 正确，再运行本脚本；
REM    · 仓库已存在 -> 直接提交并推送当前改动。
REM
REM  为什么做成脚本（实测踩坑）：
REM    · 直接 git add . 会把 env\（600 多 MB 虚拟环境）、build\、dist\
REM      一起加进去，仓库瞬间上 GB，而且历史里永久存在、事后删不干净；
REM    · 中文文件名在 cmd 默认 GBK 下是乱码，所以第一行先切 UTF-8；
REM    · Windows 系统代理设置可能指着没在运行的代理软件，导致等 20 秒后
REM      报一句看不懂的错 —— 所以先检查端口；
REM    · 节点太慢会让传输中途被掐断（Connection was reset），
REM      所以预先配好 HTTP/1.1 与大缓冲。
REM ===========================================================================

REM ↓↓↓ 只有"首次建库"会用到：确认这是你的仓库地址 ↓↓↓
set REPO_URL=https://github.com/zhou634/EcoTidy.git
set PROXY_PORT=7897

set GIT=git
where git >nul 2>nul
if errorlevel 1 (
    set GIT="C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\cmd\git.exe"
    if not exist !GIT! (
        echo.
        echo [错误] 找不到 git。
        echo        请安装 Git for Windows：https://git-scm.com/download/win
        echo        装完**重新打开**窗口再运行本脚本。
        echo.
        pause
        exit /b 1
    )
)

cd /d "%~dp0.."

echo.
echo ============================================================
echo  EcoTidy 推送到 GitHub
echo ============================================================
echo   工程目录：%CD%
echo.

echo [1/5] 检查代理端口 %PROXY_PORT% ...
netstat -ano | findstr ":%PROXY_PORT%" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo.
    echo   [警告] 端口 %PROXY_PORT% 没有监听 —— 代理软件可能没运行。
    echo.
    echo   推送失败时请先：
    echo     1^) 启动 Clash Verge
    echo     2^) 进「代理」页，打开【系统代理】
    echo     3^) 确认「设置」页端口是 %PROXY_PORT%
    echo.
    echo   若你能直连 GitHub、不需要代理，可忽略本警告。
    echo.
    set GO=
    set /p GO=   仍要继续吗？(Y/N):
    if /i not "!GO!"=="Y" exit /b 1
) else (
    echo       端口在监听，OK
)

echo [2/5] 检查 git 身份 ...
%GIT% config user.name >nul 2>nul
if errorlevel 1 (
    set GN=
    set /p GN=       你的名字（会显示在提交记录里）:
    %GIT% config --global user.name "!GN!"
)
%GIT% config user.email >nul 2>nul
if errorlevel 1 (
    set GE=
    set /p GE=       你的邮箱（GitHub 账号邮箱）:
    %GIT% config --global user.email "!GE!"
)
echo       身份 OK

echo [3/5] 初始化仓库（已存在则跳过）...
if not exist ".git" (
    %GIT% init
    %GIT% branch -M main
    %GIT% remote add origin %REPO_URL%
    echo       已初始化并关联 %REPO_URL%
) else (
    echo       已存在 .git
    %GIT% remote get-url origin >nul 2>nul
    if errorlevel 1 (
        %GIT% remote add origin %REPO_URL%
        echo       已补充远程地址
    ) else (
        for /f "delims=" %%u in ('%GIT% remote get-url origin') do echo       远程地址：%%u
    )
)

echo [4/5] 配置推送参数（针对大文件与不稳定线路）...
%GIT% config --local http.proxy http://127.0.0.1:%PROXY_PORT%
%GIT% config --local https.proxy http://127.0.0.1:%PROXY_PORT%
%GIT% config --local http.version HTTP/1.1
%GIT% config --local http.postBuffer 524288000
%GIT% config --local http.lowSpeedLimit 0
%GIT% config --local http.lowSpeedTime 999999
echo       HTTP/1.1 + 500MB 缓冲 + 放宽超时

echo [5/5] 提交并推送 ...
echo.
echo   待提交的改动：
%GIT% status --short
echo.
set MSG=
set /p MSG=   提交说明（直接回车使用默认）:
if "!MSG!"=="" set MSG=更新代码
%GIT% add .
%GIT% commit -m "!MSG!" 2>nul
echo.
echo ------------------------------------------------------------
%GIT% push -u origin HEAD
set RESULT=%errorlevel%
echo ------------------------------------------------------------

echo.
if %RESULT% neq 0 (
    echo ============================================================
    echo  推送失败，按成功率从高到低试：
    echo.
    echo  【1】换节点  ^<-- 最可能有效
    echo       Clash 代理页别用"自动选择/故障转移"，
    echo       手动挑一个延迟低、标注"专线/中转"的节点。
    echo.
    echo  【2】开 TUN 模式
    echo       Clash Verge 设置页 -^> TUN 模式（需管理员权限）
    echo.
    echo  【3】改用 SSH（逐步执行）
    echo       ssh-keygen -t ed25519 -C "你的邮箱"
    echo         ^(一路回车，不设密码^)
    echo       type %%USERPROFILE%%\.ssh\id_ed25519.pub
    echo         ^(复制那一整行^)
    echo       打开 https://github.com/settings/keys 添加 SSH key
    echo       %GIT% remote set-url origin git@github.com:zhou634/EcoTidy.git
    echo       然后重新运行本脚本
    echo.
    echo  常见错误对照：
    echo    Could not resolve host    -^> 代理软件没开，或系统代理没打开
    echo    Connection was reset      -^> 节点不稳，换节点或开 TUN
    echo    failed to push some refs  -^> 远端有本地没有的提交，
    echo                                 先执行：git pull --rebase origin main
    echo    Authentication failed     -^> 登录的不是 zhou634 账号，
    echo                                 去「凭据管理器」删掉 git:https://github.com
    echo    SEC_E_NO_CREDENTIALS      -^> Windows TLS 被安全软件拦截，
    echo                                 临时关闭该软件或改用 SSH
    echo ============================================================
) else (
    echo ============================================================
    echo  推送成功！
    echo  https://github.com/zhou634/EcoTidy
    echo ============================================================
)
echo.
pause
