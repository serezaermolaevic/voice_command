@echo off
chcp 65001 >nul
title Сборка Джарвиса

echo ========================================
echo  🤖 СБОРКА ПРИЛОЖЕНИЯ "ДЖАРВИС"
echo ========================================
echo.

:: [0/5] Проверка, что у ТЕКУЩЕГО python есть tkinter
echo [0/5] Проверка tkinter...
python -c "import tkinter" 2>nul
if errorlevel 1 (
    echo.
    echo ========================================
    echo  ❌ У этого Python нет модуля tkinter!
    echo ========================================
    echo  Проверьте, какой python активен:
    where python
    echo.
    echo  Если это Anaconda/Miniconda - выполните:
    echo    conda install tk
    echo  Если это Python со python.org - переустановите
    echo  инсталлятор и отметьте галку "tcl/tk and IDLE".
    echo  Если это Python из Microsoft Store - он часто
    echo  идёт без tkinter, поставьте версию с python.org.
    echo.
    pause
    exit /b 1
)
echo  ✅ tkinter найден у активного python.
echo.

:: [1/5] Проверка наличия PyInstaller (у ТОГО ЖЕ python)
echo [1/5] Проверка PyInstaller...
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo Установка PyInstaller...
    python -m pip install pyinstaller
    echo.
)

:: [2/5] Проверка зависимостей
echo [2/5] Проверка зависимостей...
python -m pip install speechrecognition pyttsx3 pyaudio pyperclip pyautogui

:: [3/5] Очистка старых артефактов сборки (важно!)
echo.
echo [3/5] Очистка старых build/dist/spec...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Джарвис.spec" del /q "Джарвис.spec"

echo.
echo [4/5] Сборка EXE файла...
echo.

:: Сборка тем же интерпретатором, где проверили tkinter
python -m PyInstaller --onefile ^
            --windowed ^
            --name="Джарвис" ^
            --icon="icon.ico" ^
            --add-data="icon.ico;." ^
            --hidden-import=tkinter ^
            --hidden-import=speech_recognition ^
            --hidden-import=pyttsx3 ^
            --hidden-import=pyaudio ^
            --hidden-import=pyperclip ^
            --hidden-import=pyautogui ^
            --collect-all=tkinter ^
            --clean ^
            --noconfirm ^
            main.py

echo.
echo [5/5] Проверка результата...
if exist "dist\Джарвис.exe" (
    echo.
    echo ========================================
    echo  ✅ СБОРКА УСПЕШНА!
    echo ========================================
    echo.
    echo  📁 Файл: dist\Джарвис.exe
    echo  📊 Размер: 
    for %%A in ("dist\Джарвис.exe") do echo     %%~zA байт
    echo.
    echo  🚀 Запустить приложение? (Y/N)
    choice /c YN /n /m "Выберите: "
    if errorlevel 2 goto end
    if errorlevel 1 start "" "dist\Джарвис.exe"
) else (
    echo.
    echo ========================================
    echo  ❌ ОШИБКА СБОРКИ!
    echo ========================================
    echo.
    echo  Проверьте логи выше.
)

:end
echo.
pause