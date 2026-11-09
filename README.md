# Smart Document Scanner

An Android demo that turns a photo of one paper table or list into editable records. It supports Hebrew and English, keeps the source text available for review, and exports the confirmed records to CSV or Google Sheets.

## Run on the Android emulator

Install Python 3.12+, Node.js 22.13+, Android Studio, and Homebrew. Run the following from the project root on your Mac.

```bash
brew install tesseract tesseract-lang
tesseract --list-langs  # Check for eng and heb
cp -n .env.example .env
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --limit-concurrency 16 --no-access-log
```

Start an emulator in Android Studio’s **Device Manager**. In a second terminal from the project root, run:

```bash
cd mobile
npm ci
EXPO_PUBLIC_API_URL=http://10.0.2.2:8000 npm start -- --android
```

Keep both terminals running. Use Expo Go compatible with SDK 57. If the app cannot connect, check **Check backend connection** and run `curl -fsS http://127.0.0.1:8000/health` on the Mac. [Expo Go downloads](https://expo.dev/go).

## Try the demo

1. Copy `samples/printed_en.png` or `samples/printed_he.png` to the emulator. Tap **Choose From Gallery**, then **Extract Records → On the Mac**.
2. Compare the extracted rows with **View document** and edit any errors. **Enter records manually** also supports Hebrew and English.
3. Tap **Confirm reviewed records → View & export CSV** to preview, save, or share the file. With Sheets configured, you can also upload the confirmed table and edit it later from **Document history**.

## Optional integrations

- **Gemini for handwriting and mixed pages.** Set `GEMINI_API_KEY` in the root `.env`, restart the backend, and select **Extract Records → Use Gemini** on a sample such as `samples/mixed_ink.png`. This sends the prepared image to Google. The server tries Flash‑Lite once if the primary Flash model hits its quota or returns a temporary service error. [Google AI Studio](https://aistudio.google.com/apikey).
- **Google Sheets for export and history.** Enable the Sheets API, create a service account, and share a workbook containing a blank `Scans` tab with the service account as **Editor**. Set `SHEETS_CREDENTIALS_FILE` to the absolute path of its JSON key and set `SHEETS_SPREADSHEET_ID` to the ID between `/d/` and `/edit` in the workbook URL. Keep the JSON key outside the project and restart the backend. Open a document from history to edit it in the app or browser editor. Its document tab in Google Sheets is a live view. [Service account setup](https://cloud.google.com/iam/docs/keys-create-delete).

Use fictional data for the demo. The backend is designed for local use. Keep API keys in the server environment and restrict access to the workbook.

## How the code is organized

- **`mobile/`** handles photos, review, history, and export screens.
- **`backend/app/ocr/`** adapts Tesseract and Gemini to the same transcription format. Extraction and validation turn that format into ordered rows with warnings.
- **`backend/app/`** validates the reviewed table before export. CSV and Sheets share that contract. Sheets retries use the same document ID to avoid duplicate uploads.

## Run the checks

```bash
(cd backend && .venv/bin/python -m pytest -q)
(cd mobile && npm run typecheck && npm run bundle:android)
backend/.venv/bin/python backend/tools/evaluate.py --only printed_en.png
```

The OCR evaluation uses the running backend. The automated tests replace Google services with test doubles.

[Architecture and API](docs/ARCHITECTURE.md) · [OCR results](docs/EVALUATION.md) · [Acceptance checks](docs/ACCEPTANCE.md) · [Interview walkthrough](docs/INTERVIEW_PREPARATION.md)
