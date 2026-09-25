# Repository Guidelines

## Project Structure & Module Organization

This is a Python/Flask integration service for Nova Guarda WhatsApp flows and 77Gestao sync.

- `app.py`: local entrypoint that starts the Flask app.
- `nova_guarda/`: application package.
- `nova_guarda/routes.py`: HTTP routes, webhooks, UI pages, and dev endpoints.
- `nova_guarda/automation.py`: poller for booking send, check-in/check-out dispatch, and retry.
- `nova_guarda/storage.py`: local persistence layer and schema setup.
- `nova_guarda/clients/`: HTTP clients for WhatsApp providers, 77Gestao, and geocoding.
- `nova_guarda/templates/`: Flask HTML templates.
- `tests/`: `unittest` test suite.
- `docs/arquitetura/`: architecture visualizations.

## Build, Test, and Development Commands

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run locally:

```powershell
python app.py
```

Run all tests:

```powershell
python -m unittest discover -s tests -v
```

Compile-check source and tests:

```powershell
python -m compileall nova_guarda tests
```

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation, clear function names, and type hints where they improve readability. Keep domain names explicit: prefer `booking`, `appointment`, `cooperator`, `gestao77`, and `provider` over generic names. Keep business rules in service/storage modules instead of templates.

## Testing Guidelines

Tests use Python `unittest`. Name files `test_*.py` and test classes by flow, for example `OnboardingFlowTest`. Add regression tests for every state transition, webhook parsing change, persistence change, and 77Gestao sync rule. Prefer fake providers and temporary SQLite databases in tests.

## Commit & Pull Request Guidelines

Recent commits use short imperative summaries, for example `Persist conversation webhook events` and `Complete operational automation and monitoring UI`. Keep commits focused and mention the affected flow when possible. Pull requests should include a concise summary, test results, configuration notes, and screenshots for UI changes.

## Security & Configuration Tips

Do not commit `.env`, real WhatsApp tokens, 77Gestao credentials, or generated local databases. Use `.env.example` for placeholders only. The app currently uses SQLite through `DATABASE_PATH`; keep persistence changes isolated in `nova_guarda/storage.py`.
