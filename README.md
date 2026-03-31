# Milo Agent Orchestrator

Welcome to the Milo Agent Orchestrator project! This README provides an overview of the project and links to detailed documentation.

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Running the Project](#running-the-project-locally)
4. [License](#license)

## Overview

The Milo Agent Orchestrator is a FastAPI-based application designed to manage and orchestrate AI agents. It provides a robust API for interacting with various services and models.

## Getting Started

To get started with the project, clone the repository and install the required dependencies:

```bash
    git clone https://github.com/nicrossi/milo-agent-orchestrator.git
    cd milo-agent-orchestrator
    pip install -r requirements.txt
```

## Running the Project Locally

For detailed instructions on running the project, including setting up environment variables and Firebase requirements, refer to the [Running the Project](docs/running.md) documentation.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Stack

| Layer       | Technology                          |
|-------------|-------------------------------------|
| Framework   | FastAPI 0.135                       |
| Database    | PostgreSQL via SQLAlchemy (asyncpg) |
| LLM         | Google Gemini (google-genai)        |
| RAG         | External HTTP service (httpx)       |
| Runtime     | Python 3.11+, uvicorn               |


### WebSocket protocol

Messages are JSON frames with a `type` discriminator:

**Server to client:**

```json
{"type": "chunk", "text": "..."}
{"type": "done"}
{"type": "error", "detail": "..."}
```

**Client to server:** plain text string (the user message).

The server closes idle connections after 1 hour (code `1008`).


## Project layout

```
src/
  main.py                    Application entry point and lifespan management
  api/
  |- routers/chat.py          HTTP and WebSocket endpoint definitions
  |- session.py               ChatSession — WebSocket lifecycle encapsulation
  orchestration/
  |- agent.py                 OrchestratorAgent — RAG + LLM pipeline
  adapters/
  |- clients/
  |  |- chat_history.py        Database repository for conversation turns
  |  |- rag.py                 HTTP client for the external RAG service
  |- llm/
  |  |- base.py                Abstract LLM adapter interface
  |  |- gemini.py              Google Gemini implementation
  core/
  |- database.py              Async engine, session factory, init/close
  |- models.py                SQLAlchemy ORM models (ChatMessage)
  schemas/
  |- chat.py                  Pydantic request/response models
```
