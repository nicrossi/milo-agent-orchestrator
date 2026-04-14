# Running Milo Agent Orchestrator Locally

This document explains how to set up and run the Milo Agent Orchestrator project, including the required environment variables and Firebase requirements.

## Environment Variables

The following environment variables must be set for the application to run:

| Variable                      | Required | Description                                      |
|-------------------------------|----------|--------------------------------------------------|
| `DATABASE_URL`                | Yes      | PostgreSQL connection string                     |
| `VERTEX_PROJECT`              | No       | GCP project ID (enables Vertex AI)               |
| `VERTEX_LOCATION`             | No       | GCP region (default: `us-central1`)              |
| `GOOGLE_API_KEY`              | No*      | Google Gemini API key (*if not using Vertex AI)  |
| `GOOGLE_APPLICATION_CREDENTIALS`| No*    | Path to service account JSON (*if using Vertex)  |
| `LLM_MODEL`                   | No       | Gemini model name (default: `gemini-2.5-flash`)  |
| `DB_POOL_SIZE`                | No       | SQLAlchemy pool size (default: `5`)              |
| `DB_MAX_OVERFLOW`             | No       | SQLAlchemy max overflow (default: `10`)          |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Yes    | Path to the Firebase service account JSON file   |

### Example `.env` File

You can create a `.env` file in the project root to simplify setting environment variables:

```env
DATABASE_URL="postgresql://user:pass@localhost:5432/milo"
VERTEX_PROJECT="your-gcp-project-id"
VERTEX_LOCATION="us-central1"
GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
# GOOGLE_API_KEY="your-google-api-key" (Required if VERTEX_PROJECT is not set)
LLM_MODEL="gemini-2.5-flash"
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
FIREBASE_SERVICE_ACCOUNT_PATH="/path/to/service-account-key.json"
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
   export VERTEX_PROJECT="your-gcp-project-id"
   export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
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
