# XML / YAML / JSON Editor

A Flask-based editor for uploading, validating, editing, previewing, and downloading XML, YAML, and JSON files directly in the browser.

## Features

- Upload `.xml`, `.yaml`, `.yml`, and `.json` files
- Validate XML, YAML, and JSON before opening the editor
- Edit XML values and attributes in a structured browser form
- Edit YAML or JSON content in a textarea-based editor
- Preview the updated document before saving it
- Download the generated file as a modified copy
- Clear the current session and remove uploaded/generated artifacts
- Best-effort cleanup when browser tabs close or the page is closed

## Requirements

- Python 3.9+
- A modern browser
- Windows PowerShell examples below; the project also works on other operating systems

## Run Locally

From the project directory:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Create a project-root `.env` file with a secret key like this:

```env
FLASK_SECRET_KEY=your-strong-secret-key
```

The app reads `FLASK_SECRET_KEY` from `.env` on startup. Keep it stable between restarts and do not commit the file to source control.

Open <http://127.0.0.1:5000> in your browser.

## Supported Files

The app accepts files with these extensions:

- `.xml`
- `.yaml`
- `.yml`
- `.json`

The upload limit is 2 MB per file. Generated files are stored in `downloads/`, while uploaded originals are kept in `uploads/`.

## Session Cleanup Behavior

The app includes a **Clear session and delete files** action that immediately removes the current session's uploaded and generated files.

The cleanup logic also tracks active browser tabs using a SQLite lease database:

- lease data is stored in `uploads/.session-tabs.sqlite3`
- each open editor tab refreshes its activity timestamp
- page close events trigger a best-effort cleanup request
- the server removes uploaded/generated files only when the last active tab for that upload is gone

This is intended to minimize stale files after browser exit, but browser close notifications are not guaranteed. A crashed browser, forced shutdown, or interrupted network can prevent the close signal from reaching the server. For reliable cleanup in production, use an explicit scheduled cleanup job on the deployment host.

## Local Development Notes

For local debugging, run:

```powershell
$env:FLASK_DEBUG = '1'
python app.py
```

Do not enable debug mode on a public or shared deployment.

## Tests

Run the automated regression checks with:

```powershell
python -m unittest discover -s tests -v
```

## Production Deployment Notes

Before exposing the app publicly:

- set a stable private `FLASK_SECRET_KEY` in the environment
- serve it behind HTTPS
- use a production WSGI server such as Waitress
- configure secure cookie settings when HTTPS is terminated in front of Flask
- add authentication and authorization if the app will be shared
- add monitoring, backups, storage quotas, and a retention policy for uploaded/generated files
- use private persistent storage appropriate to the environment

This project is a local/browser-based editor and is not designed as a multi-user authenticated application by default. Browser-close cleanup is best-effort rather than guaranteed, so deployment hardening is recommended before public use.