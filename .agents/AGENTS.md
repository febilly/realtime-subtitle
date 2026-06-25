# Realtime Subtitle Project Rules

## Running Tests
- Always run the test suite using `python -m pytest` from the workspace root.
- Do NOT use virtualenv-specific binary paths like `.venv\Scripts\pytest` or `.\.venv\Scripts\pytest.exe`, as these can cause CommandNotFoundException or execution policy errors in Windows PowerShell.
- The standard system `python` command is pre-configured with the required environment packages and is the correct interpreter to use.
