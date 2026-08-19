# Week 8 load test

This stack measures the backend with a deterministic HTTP mock LLM. It does not call a real model.

## Run

```bash
./load_tests/run_load_test.sh
```

The script always uses:

- Compose project `ai-chat-v2-load`
- PostgreSQL database `chat_v2_load_test`
- a project-specific PostgreSQL volume
- host ports `18000`, `55432`, and `56379`
- mock credentials from `load_tests/load.env`

It removes the temporary containers, network, and database volume on exit. CSV and JSON results remain under `load_tests/results/`, which is ignored by Git except for `.gitkeep`.

To override the base image when the default registry is unavailable:

```bash
PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  ./load_tests/run_load_test.sh
```

## Measurement boundary

User registration and login happen once per virtual user through an uninstrumented setup client. Locust records only `POST /chat` and `POST /chat/stream`.

Each request creates a new conversation so that message-history size stays constant. The streaming response time is full-response latency, not time to first token.
