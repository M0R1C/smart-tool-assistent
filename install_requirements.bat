@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo    Установка зависимостей Smart Macro
echo ========================================

:: Проверяем наличие requirements.txt
if not exist "requirements.txt" (
    echo ОШИБКА: Файл requirements.txt не найден!
    echo Убедитесь, что он находится в той же папке, что и этот bat-файл
    pause
    exit /b 1
)

echo Найден файл requirements.txt
echo.

:: Проверяем установлен ли Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ОШИБКА: Python не найден!
    echo Установите Python с официального сайта:
    echo https://www.python.org/downloads/
    echo Не забудьте отметить галочку "Add Python to PATH" при установке
    pause
    exit /b 1
)

echo Python обнаружен
echo.

:: Проверяем установлен ли pip
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ОШИБКА: pip не найден!
    echo Переустановите Python с галочкой "Add Python to PATH"
    pause
    exit /b 1
)

echo pip обнаружен
echo.

:: Обновляем pip до последней версии
echo Обновление pip до последней версии...
python -m pip install --upgrade pip

echo.
echo ========================================
echo Установка зависимостей из requirements.txt
echo ========================================
echo.

:: Устанавливаем зависимости
python -m pip install -r requirements.txt

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo УСПЕХ: Все зависимости установлены!
    echo ========================================
    echo Теперь вы можете запускать программу.
) else (
    echo.
    echo ========================================
    echo ПРЕДУПРЕЖДЕНИЕ: Были ошибки при установке
    echo ========================================
    echo Некоторые зависимости могут быть не установлены.
    echo Попробуйте запустить установку от имени администратора.
)

echo.
pause