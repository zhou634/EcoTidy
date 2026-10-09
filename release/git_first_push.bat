@echo off
chcp 65001 >nul
setlocal

REM ===========================================================================
REM  EcoTidy 首次上传到 GitHub 的辅助脚本
REM
REM  两种情况，脚本会自己判断，你什么都不用改：
REM
REM    ① 已在 Visual Studio 里点过「创建并推送」
REM       → 脚本检测到 .git 与远程地址，直接显示仓库链接，不做任何改动；
REM
REM    ② 还没建仓库（走纯命令行上传）
REM       → 先在 GitHub 网页新建一个**空**仓库（不要勾 README／LICENSE／
REM         .gitignore），然后运行本脚本，它会用下面的 REPO_URL 建库并推送。
REM
REM  为什么要有这个脚本（实测踩坑）：
REM      · 直接 git add . 会把 env\（600 多 MB 虚拟环境）、build\、dist\
REM        一起加进去，仓库瞬间上 GB，而且历史里永久存在、事后删不干净；
REM      · 中文文件名在 Windows 控制台默认 GBK 下会显示成乱码，
REM        所以第一行先切 UTF-8 代码页；
REM      · 首次提交前需要设置用户名邮箱，否则 commit 直接失败。
REM ===========================================================================

REM ↓↓↓ 只有"情况 ②"会用到：确认这是你的仓库地址 ↓↓↓
set REPO_URL=https://github.com/zhou634/EcoTidy.git

echo.
echo ============================================================
echo  EcoTidy - 首次上传到 GitHub
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 git。
    echo.
    echo 请先安装 Git for Windows：https://git-scm.com/download/win
    echo 安装时保持默认选项即可；装完**重新打开**这个窗口再运行。
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0.."

REM ---- 情况 ①：Visual Studio 已经建好仓库并推送过 ----
if exist ".git" (
    git remote get-url origin >nul 2>nul
    if not errorlevel 1 (
        echo [完成] 这个工程已经连上 GitHub 了，不需要再跑本脚本。
        echo.
        echo        仓库地址：
        git remote get-url origin
        echo.
        echo        以后想更新代码，只要三步：
        echo            git add .
        echo            git commit -m "说明这次改了什么"
        echo            git push
        echo.
        pause
        exit /b 0
    )
)

echo [1/6] 使用仓库地址：%REPO_URL%
echo       若不是你的仓库，请用记事本改本脚本开头的 REPO_URL（第 26 行）。
echo.

echo [2/6] 检查 git 身份（首次提交必需）...
git config user.name >nul 2>nul
if errorlevel 1 (
    echo       未设置用户名，正在设置为你稍后输入的值。
    set /p GIT_NAME=       请输入你的名字（会显示在提交记录里）:
    git config --global user.name "%GIT_NAME%"
)
git config user.email >nul 2>nul
if errorlevel 1 (
    set /p GIT_EMAIL=       请输入你的邮箱（GitHub 账号邮箱）:
    git config --global user.email "%GIT_EMAIL%"
)
echo       身份 OK

echo [3/6] 初始化仓库...
git init
git branch -M main

echo [4/6] 添加文件（按 .gitignore 排除 env/build/dist/temp 等）...
git add .

echo.
echo       即将提交的文件数量：
git diff --cached --name-only | find /c /v ""
echo       ^↑ 正常约 70 个。若显示上千个，说明 .gitignore 没生效，
echo          请按 Ctrl+C 中止并检查 .gitignore 是否还在工程根目录。
echo.
echo       其中 static 目录下（含 17MB 字体，属正常）：
git diff --cached --name-only | findstr /i "static"
echo.

pause

echo [5/6] 提交...
git commit -m "初始提交：EcoTidy 生态数据清洗与分析软件 v1.0"

echo [6/6] 关联远程并推送...
git remote remove origin >nul 2>nul
git remote add origin %REPO_URL%
git push -u origin main

echo.
if errorlevel 1 (
    echo ============================================================
    echo  推送失败，常见原因：
    echo   1. 仓库地址写错 或 仓库不存在 -^> 检查 REPO_URL
    echo   2. 未登录 -^> 推送时会弹出浏览器让你登录 GitHub，登录即可
    echo   3. 远程已有内容 -^> 若是新建的空仓库不该出现；
    echo      若你在网页上勾选了 README，先执行：
    echo          git pull --rebase origin main
    echo      再重新 push
    echo ============================================================
) else (
    echo ============================================================
    echo  上传完成！打开 %REPO_URL% 就能看到。
    echo ============================================================
)
echo.
pause
