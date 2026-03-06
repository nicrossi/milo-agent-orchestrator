$env:DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/milo'
$env:GOOGLE_API_KEY = 'replace-with-real-google-api-key'
$env:RAG_SERVICE_URL = 'http://localhost:9999'

.\.venv311\Scripts\python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
