@echo off
echo Building SignWell Invoicer.exe ...
echo.

pip install pyinstaller customtkinter --quiet

pyinstaller --onefile --windowed ^
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
echo Done. Find the exe in the dist\ folder.
echo.
echo =====================================================
echo  Copy these files next to the exe before sending:
echo    .env          (API key + sender info)
echo    clients.yaml  (client registry)
echo =====================================================
pause
