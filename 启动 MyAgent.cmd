@echo off
title MyAgent
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "C:\Users\ch\Desktop\AIAGEN~1\my_agent\scripts\START_~1.PS1"
exit /b %ERRORLEVEL%
