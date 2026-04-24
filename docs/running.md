# Running Milo Agent Orchestrator Locally

This document explains how to set up and run the Milo Agent Orchestrator project, including the required environment variables and Firebase requirements.

## Environment Variables

The following environment variables must be set for the application to run:

| Variable                      | Required | Description                                      |
|-------------------------------|----------|--------------------------------------------------|
| `DATABASE_URL`                | Yes      | PostgreSQL connection string                     |
| `GOOGLE_API_KEY`              | Yes      | Google Gemini API key                            |
| `LLM_MODEL`                   | No       | Gemini model name (default: `gemini-2.5-flash`)  |
| `DB_POOL_SIZE`                | No       | SQLAlchemy pool size (default: `5`)              |
| `DB_MAX_OVERFLOW`             | No       | SQLAlchemy max overflow (default: `10`)          |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Yes    | Path to the Firebase service account JSON file   |
| `AUTO_EVALUATE_ON_CHAT_CLOSE` | No       | Set `false` to skip automatic LLM metrics evaluation when a chat session closes (default: `true`) |

### Example `.env` File

You can create a `.env` file in the project root to simplify setting environment variables:

```env
DATABASE_URL="postgresql://user:pass@localhost:5432/milo"
GOOGLE_API_KEY="your-google-api-key"
LLM_MODEL="gemini-2.5-flash"
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
FIREBASE_SERVICE_ACCOUNT_PATH="/path/to/service-account-key.json"
AUTO_EVALUATE_ON_CHAT_CLOSE=true
```

## Running the Project

Follow these steps to run the project locally:

1. **Install Dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

2. **Set Environment Variables**:

   Use the `.env` file or export the variables manually:

   ```bash
   export DATABASE_URL="postgresql://user:pass@localhost:5432/milo"
   export GOOGLE_API_KEY="your-google-api-key"
   export FIREBASE_SERVICE_ACCOUNT_PATH="/path/to/service-account-key.json"
   ```

3. **Start the Server**:

   ```bash
   uvicorn src.main:app --port 3000
   ```

The server will be available at `http://localhost:3000`.
Tables are created automatically on startup via the FastAPI lifespan hook.

---

For more details, refer to the [README](../README.md).
