@echo off
set ENROLLMENT_TOKEN=16051979Cs$
set ADMIN_PASS=16051979Cs$
set ADMIN_USER=magicx
set HMAC_SECRET=16051979Cs$
python -m uvicorn server:app --host 0.0.0.0 --port 8000

pause