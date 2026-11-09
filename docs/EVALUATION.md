# OCR evaluation and release status

Run on 8 November 2026 through a running FastAPI server on an Apple Silicon Mac. The same ten non-sensitive images were sent as multipart requests to Tesseract 5.5.3, first with `tessdata_fast`, then with `tessdata_best`, each using `eng+heb`. The test set includes real pen strokes from published scans, a mixed-media composite, rendered printed pages and controlled image degradation. A physical phone capture remains a separate test.

## Provider choice

| Candidate | Printed English | Handwritten English | Printed Hebrew | Handwritten Hebrew | Evidence and decision |
| --- | --- | --- | --- | --- | --- |
| Google Cloud Vision document text detection | Listed | Latin handwriting listed | Listed | Hebrew script absent from published handwriting list | Candidate reviewed using [official language and script support](https://docs.cloud.google.com/vision/docs/languages). |
| Google Document AI Enterprise Document OCR | Listed | Marked supported | Listed | Explicitly marked unsupported for handwriting | Candidate reviewed using the [processor language table](https://docs.cloud.google.com/document-ai/docs/processors-list#processor_ocr). |
| Gemini 3.5 Flash | Image input, live accuracy pending | Image input, live accuracy pending | Image input, live accuracy pending | Image input, live accuracy pending | Configurable cloud candidate. Image input and structured outputs are documented, with no script-specific handwriting accuracy guarantee. [Model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash). |
| Tesseract 5.5.3 | Tested | Tested, inadequate on the sample | Tested | Tested, inadequate on the sample | Configurable local print baseline. [Tesseract language models](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html). |

The independent ivrit.ai benchmark shows that a Gemini-family model's line results can differ substantially from its page results. Those published model labels and documents differ from this app's test configuration. This motivated a local measurement and a credentialed Gemini trial on the same pages. [ivrit.ai handwriting benchmark](https://huggingface.co/spaces/ivrit-ai/hebrew-handwriting-ocr-leaderboard).

Each recognition request selects one provider. The local adapter makes print demos reproducible without credentials. The Gemini adapter was chosen for the handwriting trial because one image request can propose a transcript and table layout. The SDK retries once on temporary server errors. Validation errors and quota errors are reported to the app. The app keeps the original image, any partial records and a manual editor available.

## Measured results

The table shows the higher-accuracy language-data run. “Rows” lists expected, returned, then matched. “Fields” lists correct, wrong, then missing. A field is correct when it matches the reference after Unicode NFC and whitespace normalization. A row match uses a configured literal key or at least 0.62 transcript similarity, with order-preserving one-to-one assignment. Fields in unmatched reference rows count as missing. Unexpected predicted rows are recorded separately. Character error rate (CER) is character edit distance divided by reference length. Rows or fields that required user intervention are scored before correction.

| Sample | Rows expected / returned / matched | Row recall | Fields correct / wrong / missing | Field accuracy | CER | Flagged fields | Server ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Printed English route | 3 / 3 / 3 | 100% | 12 / 0 / 0 | 100.00% | 0.00% | 0 | 559 |
| Printed Hebrew route | 3 / 3 / 3 | 100% | 12 / 0 / 0 | 100.00% | 0.00% | 0 | 593 |
| Printed bilingual route | 3 / 3 / 3 | 100% | 11 / 1 / 0 | 91.67% | 0.88% | 0 | 594 |
| Handwritten English archival ink | 3 / 1 / 0 | 0% | 0 / 0 / 3 | 0.00% | 96.15% | 1 | 295 |
| Handwritten Hebrew modern ink | 1 / 1 / 0 | 0% | 0 / 0 / 1 | 0.00% | 85.25% | 1 | 639 |
| Mixed print and handwritten Hebrew | 2 / 2 / 1 | 50% | 2 / 0 / 2 | 50.00% | 59.09% | 2 | 791 |
| Dark and blurred printed route | 3 / 3 / 0 | 0% | 0 / 0 / 12 | 0.00% | 39.13% | 8 | 1,019 |
| Printed route at 3° tilt | 3 / 3 / 3 | 100% | 12 / 0 / 0 | 100.00% | 0.00% | 0 | 441 |
| Borderless inventory with no header | 3 / 3 / 3 | 100% | 8 / 0 / 1 | 88.89% | 3.33% | 2 | 295 |
| Blank | 0 / 0 / 0 | — | — | — | — | — | HTTP 422 |

The fast-data run scored 11/12 on printed English, 12/12 on printed Hebrew and 9/12 on the bilingual page. Both runs recovered 12/12 from the tilted page after compensating for mild skew in word positions. These single measurements describe the supplied images and this execution environment. [Best-data output](tesseract-best-results.json), [fast-data output](tesseract-fast-results.json).

- In the bilingual page, best-data Tesseract read `B07` as `BO7` without flagging it. This is why the app asks the user to inspect every row, including high-confidence fields.
- In the mixed page the printed row survived. The handwritten row stayed present with unresolved cells and source text. Both pure handwriting inputs produced some OCR text but zero rows with adequate evidence for a match.
- In the dark and blurred page the backend returned explicit dark and low-detail warnings. Although three rows were returned in the best-data run, none met the row-match criterion.
- The borderless checklist kept row and column order, preserved an intentional blank and missed one printed quantity. The blank page returned an explicit error. Corrupt, unsupported and oversized uploads were exercised in pytest. Curved paper, physical shadows and multiple writers remain untested in this run.

## Live handwriting results

The installed backend was tested with Gemini 3.5 Flash. These are unedited results from the supplied ink images. They measure only these samples, not recognition accuracy for unseen writers.

| Sample | Rows matched | Exact fields | Character error rate |
| --- | ---: | ---: | ---: |
| Archival English handwriting | 3 / 3 | 2 / 3 | 3.85% |
| Modern Hebrew handwriting | 1 / 1 | 1 / 1 | 0.00% |
| English print with Hebrew handwriting | 2 / 2 | 4 / 4 | 0.00% |

On the English letter the model substituted one word; the source transcription was checked against the [Whitman Archive](https://whitmanarchive.org/item/loc.02109). The Hebrew fixture was checked against the image, and the reference for the same crop in the mixed sample was corrected as well. An earlier mixed request received a temporary provider error. The final run succeeded for all three files. [Full Gemini evaluation](gemini-handwriting-results.json).

## Export and release evidence

- Printed English and Hebrew each returned all 12 expected fields in a best-data run. A Hebrew sample was exported as UTF-8 CSV, uploaded to the live test workbook and read back with its original headings. Repeating the same upload did not add rows or change existing English documents.
- The live Hebrew document was opened in its own Google Sheets tab. The document API saved a Hebrew edit, rejected a stale edit with HTTP 409, and returned the updated records for history and CSV. The original test value was restored.
- The Chrome editor rendered the Hebrew grid right to left. A browser edit was saved to Google Sheets and was still present after reloading. The demo data was restored and checked afterward.
- The Android app has been used in a Pixel 8 emulator for gallery selection, history, viewing Google Sheets, and saving and reopening a real CSV. The updated Android JavaScript bundle and dependency check passed, and Expo launched the project on the emulator. A separate physical-phone run, including camera capture and Wi-Fi access, remains to be done.

See the [criterion-by-criterion audit](ACCEPTANCE.md) and [launch guide](../README.md).
