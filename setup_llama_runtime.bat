@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LLAMA_BIN=%CD%\core\server\engines\llama\bin"
set "LLAMA_URL=https://github.com/ggml-org/llama.cpp/releases/download/b7798/llama-b7798-bin-win-vulkan-x64.zip"

if not exist "%LLAMA_BIN%\llama.dll" (
    echo Downloading the llama.cpp Vulkan runtime required by Qwen and Fun-ASR...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $zip = Join-Path $env:TEMP 'capswriter-llama-b7798.zip'; $tmp = Join-Path $env:TEMP ('capswriter-llama-' + [guid]::NewGuid()); try { Invoke-WebRequest -Uri '%LLAMA_URL%' -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force; $dll = Get-ChildItem -LiteralPath $tmp -Filter 'llama.dll' -Recurse | Select-Object -First 1; if ($null -eq $dll) { throw 'llama.dll was not found in the downloaded archive.' }; Copy-Item -Path (Join-Path $dll.Directory.FullName '*') -Destination '%LLAMA_BIN%' -Recurse -Force } finally { Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }"
    if errorlevel 1 (
        echo ERROR: Could not prepare llama.cpp. Download the archive manually and unpack it into:
        echo   core\server\engines\llama\bin
        pause
        exit /b 1
    )
)

for %%D in (
    "%CD%\core\server\engines\qwen_asr_gguf\inference\bin"
    "%CD%\core\server\engines\fun_asr_gguf\inference\bin"
    "%CD%\core\server\engines\force_aligner_gguf\inference\bin"
) do (
    if not exist "%%~D\llama.dll" (
        if exist "%%~D" (
            echo ERROR: %%~D exists but does not contain llama.dll.
            echo Remove or populate that directory, then retry.
            pause
            exit /b 1
        )
        mklink /J "%%~D" "%LLAMA_BIN%" >nul
        if errorlevel 1 (
            echo ERROR: Could not create the native runtime link: %%~D
            pause
            exit /b 1
        )
    )
)

echo llama.cpp runtime is ready.
exit /b 0
