rem C:\Claude\Projects\chessmon\.venv\Scripts\uvicorn --app-dir C:\Claude\Projects\chessmon server.app:app --host 0.0.0.0 --port 8000
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=chessmon"
python -m uvicorn --app-dir C:\Claude\Projects\chessmon server.app:app --host 0.0.0.0 --port 8000 ^
  --ssl-keyfile key.pem --ssl-certfile cert.pem

pause