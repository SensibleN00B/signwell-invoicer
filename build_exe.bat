@echo off
echo Building SignWell Invoicer (folder mode) ...
echo.

pip install pyinstaller customtkinter --quiet

pyinstaller --onedir --windowed ^
  --name "SignWell Invoicer" ^
  --hidden-import "invoicer.config" ^
  --hidden-import "invoicer.models" ^
  --hidden-import "invoicer.sender" ^
  --hidden-import "invoicer.signwell" ^
  --hidden-import "invoicer.tracking" ^
  --hidden-import "invoicer.gui" ^
  --hidden-import "email_validator" ^
  --hidden-import "pydantic_settings" ^
  --collect-all customtkinter ^
  run_gui.py

echo.
echo Done. Find the folder in dist\SignWell Invoicer\
echo Run: dist\SignWell Invoicer\SignWell Invoicer.exe
echo.
echo =====================================================
echo  Copy these files into dist\SignWell Invoicer\
echo  before distributing:
echo    .env          (API key + sender info)
echo    clients.yaml  (client registry)
echo =====================================================
pause
