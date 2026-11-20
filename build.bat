@echo off
chcp 65001 >nul
echo ========================================================
echo        SVN 极速搜索与 Spotlight 工具打包脚本
echo ========================================================
echo.
echo 正在检查 PyInstaller...
where pyinstaller >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [错误] 未检测到 pyinstaller，请先运行: pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo 请选择要打包的目标:
echo [1] 打包全部 (SVN极速搜索工具 + Spotlight版)
echo [2] 仅打包 SVN极速搜索工具
echo [3] 仅打包 SVN极速搜索_Spotlight版
echo [0] 退出
echo.
set /p choice=请输入选项 [1/2/3/0]: 

if "%choice%"=="1" goto build_all
if "%choice%"=="2" goto build_search
if "%choice%"=="3" goto build_spotlight
if "%choice%"=="0" goto end
goto invalid_choice

:build_all
echo.
echo [1/2] 开始打包 SVN极速搜索工具...
pyinstaller --clean -y SVN_Search.spec
if %ERRORLEVEL% neq 0 (
    echo [错误] 打包 SVN极速搜索工具 失败！
    pause
    exit /b 1
)
echo.
echo [2/2] 开始打包 SVN极速搜索_Spotlight版...
pyinstaller --clean -y SVN_Spotlight.spec
if %ERRORLEVEL% neq 0 (
    echo [错误] 打包 SVN极速搜索_Spotlight版 失败！
    pause
    exit /b 1
)
goto build_success

:build_search
echo.
echo 开始打包 SVN极速搜索工具...
pyinstaller --clean -y SVN_Search.spec
if %ERRORLEVEL% neq 0 (
    echo [错误] 打包失败！
    pause
    exit /b 1
)
goto build_success

:build_spotlight
echo.
echo 开始打包 SVN极速搜索_Spotlight版...
pyinstaller --clean -y SVN_Spotlight.spec
if %ERRORLEVEL% neq 0 (
    echo [错误] 打包失败！
    pause
    exit /b 1
)
goto build_success

:build_success
echo.
echo ========================================================
echo 打包完成！生成的可执行文件位于 dist/ 目录中。
echo ========================================================
pause
goto end

:invalid_choice
echo 无效输入，脚本退出。
pause

:end
