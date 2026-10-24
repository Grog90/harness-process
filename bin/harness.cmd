@echo off
rem Обёртка для Windows: запускает CLI управляющего процесса.
rem Использование: bin\harness.cmd --root . demo
setlocal
set "PYTHON_EXE=python"
where python >nul 2>nul || set "PYTHON_EXE=py -3"
%PYTHON_EXE% -m harness %*
exit /b %ERRORLEVEL%
