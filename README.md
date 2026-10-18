# Scheduling AI

An AI agent for a workforce scheduling system. You write a request in the chat, and the agent finds the data, checks the rules through the rule engine, and opens the right screen.

## Setup

Copy the example config and add your OpenAI API key:

```bash
cp .env.example .env
```

Then set `OPENAI_API_KEY` in `.env`.

## Run with Docker

```bash
docker compose up --build
```

Open http://127.0.0.1:8765.

## Run without Docker

You need Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8765
```

Open http://127.0.0.1:8765.

## How to use

Choose an account at the top of the page: the manager or one of the employees. Then write a request in the chat, for example:

- `Show me next week's nights, only with shift leads`
- `Who worked the most weekends in the last month?`
- `Assign Ron instead of Michal on Thursday morning`

When the agent opens a screen, it appears on the right.
All changes are kept in memory, so a restart brings back the original data.
