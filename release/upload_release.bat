@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ===========================================================================
REM  EcoTidy - 用 GitHub API 上传发布包（比浏览器可靠）
REM
REM  为什么不用浏览器上传（实测踩坑）：
REM      GitHub Releases 页面的 Assets 上传对 80 MB 以上的文件经常报
REM      "Something went wrong, and we can't process that file"。
REM      原因是浏览器上传走的是网页会话，网络一抖就整包作废，
REM      而且**没有断点续传、不能重试**，只能从头再来。
REM      用 API 上传可以重试，失败了再跑一次即可，成功率明显更高。
REM
REM  用法：
REM      1. 先建一个 Token（只需一次）：
REM         打开 https://github.com/settings/tokens
REM         -^> Generate new token (classic)
REM         -^> 勾选 repo 权限 -^> 生成后复制那串 ghp_xxx
REM      2. 双击本脚本，按提示粘贴 Token
REM ===========================================================================

set REPO=zhou634/EcoTidy
set TAG=v1.0
set ZIP=%~dp0..\EcoTidy_v1.0_win64.zip
set PROXY=http://127.0.0.1:7897
set API=https://api.github.com/repos/%REPO%

cd /d "%~dp0.."

echo.
echo ============================================================
echo  EcoTidy 发布包上传
echo ============================================================
echo   仓库：%REPO%
echo   标签：%TAG%
echo.

if not exist "%ZIP%" (
    echo   [错误] 找不到 %ZIP%
    echo          请先在工程根目录生成发布包。
    echo.
    pause
    exit /b 1
)
for %%F in ("%ZIP%") do set ZIPSIZE=%%~zF
echo   文件：%ZIP%
echo   大小：%ZIPSIZE% 字节
echo.

set TOKEN=
set /p TOKEN=   请粘贴 GitHub Token（ghp_ 开头，输入时不回显）:
if "!TOKEN!"=="" (
    echo.
    echo   [错误] Token 不能为空。
    pause
    exit /b 1
)

set CURLPROXY=
netstat -ano | findstr ":7897" | findstr "LISTENING" >nul
if not errorlevel 1 (
    set CURLPROXY=--proxy %PROXY%
    echo   [OK] 使用代理 %PROXY%
) else (
    echo   [提示] 代理端口未监听，将直连 GitHub
)
echo.

echo ============================================================
echo  第 1 步：创建 Release %TAG%
echo ============================================================
curl.exe -sS %CURLPROXY% --max-time 60 ^
  -X POST ^
  -H "Authorization: Bearer !TOKEN!" ^
  -H "Accept: application/vnd.github+json" ^
  -H "X-GitHub-Api-Version: 2022-11-28" ^
  -d "{\"tag_name\":\"%TAG%\",\"name\":\"EcoTidy v1.0\",\"body\":\"EcoTidy v1.0 - 生态野外多源监测数据清洗与标准化分析系统\\n\\n下载下面的 zip，解压后双击 EcoTidy.exe 即可运行（无需安装 Python）。\\n\\n详细用法见压缩包内的「使用说明.txt」。\",\"draft\":false,\"prerelease\":false}" ^
  "!API!/releases" ^
  -o "%TEMP%\ecotidy_release.json" -w "  HTTP %%{http_code}\n"

if errorlevel 1 (
    echo   [失败] 网络错误
    goto :fail
)

findstr /c:"already_exists" "%TEMP%\ecotidy_release.json" >nul 2>nul
if not errorlevel 1 (
    echo   [提示] 该标签的 Release 已存在，改为查询它
)

echo.
echo ============================================================
echo  第 2 步：查询 Release 的 upload_url
echo ============================================================
for /f "delims=" %%u in ('powershell -NoProfile -Command ^
  "(Get-Content -Raw '%TEMP%\ecotidy_release.json' | ConvertFrom-Json).upload_url"') do set UPLOAD_URL=%%u

if "!UPLOAD_URL!"=="" (
    echo   [失败] 没拿到 upload_url，请检查 Token 权限是否勾了 repo
    echo.
    echo   GitHub 返回内容：
    type "%TEMP%\ecotidy_release.json"
    goto :fail
)
set UPLOAD_URL=!UPLOAD_URL:{?name,label}=!
echo   上传地址就绪

echo.
echo ============================================================
echo  第 3 步：上传附件（86 MB，可能要几分钟，别关窗口）
echo ============================================================
for %%F in ("%ZIP%") do set ZIPNAME=%%~nxF

curl.exe %CURLPROXY% ^
  --max-time 1800 ^
  --retry 5 --retry-delay 5 --retry-all-errors ^
  --speed-time 120 --speed-limit 1024 ^
  -X POST ^
  -H "Authorization: Bearer !TOKEN!" ^
  -H "Content-Type: application/zip" ^
  -H "Accept: application/vnd.github+json" ^
  --data-binary "@%ZIP%" ^
  "!UPLOAD_URL!?name=!ZIPNAME!" ^
  -o "%TEMP%\ecotidy_asset.json" -w "  HTTP %%{http_code}  上传 %%{size_upload} 字节  速度 %%{speed_upload} 字节/秒  耗时 %%{time_total}s\n"

if errorlevel 1 (
    echo.
    echo   [失败] 上传中断（网络问题）。直接**再运行一次本脚本**即可，
    echo          已上传的部分不会浪费，GitHub 会覆盖同名附件。
    goto :fail
)

echo.
echo ============================================================
echo  完成！
echo ============================================================
echo   发布页：https://github.com/%REPO%/releases/tag/%TAG%
echo.
echo   请打开确认能看到附件 %ZIPNAME%
echo.
pause
exit /b 0

:fail
echo.
echo ============================================================
echo  失败排查：
echo    401 -^> Token 错或已过期，重新生成
echo    403 -^> Token 没勾 repo 权限
echo    404 -^> 仓库名写错，或 Token 无权访问该仓库
echo    422 -^> 同名附件已存在（可忽略，或先去网页删掉旧附件）
echo.
echo  其它办法：
echo    · 用 Edge/Chrome 重试网页上传（有时是浏览器问题）
echo    · 安装 GitHub CLI 后一条命令搞定：
echo        gh release create v1.0 "%ZIP%" --title "EcoTidy v1.0" --notes "见压缩包内使用说明"
echo ============================================================
echo.
pause
exit /b 1
