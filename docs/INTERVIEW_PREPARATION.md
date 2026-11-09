# Interview preparation

## Two-minute English explanation

“I brought a small document-processing project to discuss how I approach applied AI. The Android app takes a photo of a one-page table or list. A FastAPI service validates the image and sends it to a configurable recognition provider. The service turns the recognized lines into ordered records, keeps the source text, and flags cells or rows that need attention. The user can check the photo, correct values and confirm a table before exporting it as CSV or to Google Sheets.

The part I wanted to examine was Hebrew handwriting. Printed Hebrew support tells us little about cursive Hebrew on a phone photo. I compared the published provider capabilities and used an offline OCR engine to get a baseline with actual handwriting scans. There are measurable failures, so the editor keeps partial rows visible and lets the user enter missing values. The live Gemini run matched the supplied Hebrew ink and mixed page exactly, and made a one-word error on the English handwriting sample. This is a small test set, so I would still measure accuracy on new photos and writers.

There are two boundaries I’d highlight. Both exports consume the same reviewed records, and Sheets writes use an idempotent request so a lost response does not create another copy of the same reviewed upload. This project connects to the role through evaluation, reliable data handling, and human oversight. The delivery documents are fictional demo examples.”

Use the specific results in [the evaluation](EVALUATION.md). Describe what you personally ran or changed after your own walkthrough. If a service or phone step is still blocked, name the missing dependency during the demonstration.

## Five-minute technical walkthrough

| Time | Topic | Demonstration cue |
| --- | --- | --- |
| 0:00–0:45 | Scope | Show the printed delivery sheet. Each of the three physical rows should become a record. |
| 0:45–1:30 | Capture and API | Show the preview, trigger extraction and point out the multipart request and asynchronous progress. |
| 1:30–2:35 | Recognition, extraction and validation | Compare the row transcript with its cells. Show an uncertain field or a missing value and edit it. |
| 2:35–3:35 | Export | Confirm the table, open the CSV and, with Sheets configured, upload and open the tab. Mention the retry marker. |
| 3:35–4:35 | Evaluation | Show expected versus actual rows and fields. Discuss Hebrew ink, low-quality input and why a successful request says little about accuracy. |
| 4:35–5:00 | Trade-off | Explain the single-page, single-schema-tab scope and the physical tests you would run next. |

## Technical discussion prompts

- **Why Python?** FastAPI provides a small typed boundary for uploads and exports. Pillow, the provider clients and pytest make the recognition and validation paths straightforward to run and test. The response contract carries the same shape into TypeScript.
- **Why React Native and Expo?** The system camera and gallery picker, image rotation, local file writing and Android share chooser are available through Expo. React state keeps the preview, draft and confirmed export distinct. SDK 57 was chosen for the documented Expo Go device path during the SDK 58 transition.
- **How does recognition work?** Local Tesseract reads image pixels and emits words, boxes and confidence scores. The backend groups those boxes into rows and aligns them to likely columns. Gemini processes the image and returns a structured transcript with proposed row roles and values. Neither provider receives pen strokes. Gemini's confidence is unknown.
- **How is extraction different?** OCR answers what text is visible. Extraction answers which tokens form a record, where they belong, and which heading names the field. Boxes help position local OCR words. A valid model JSON shape can still contain the wrong row or text. Evidence checks, null cells and review address those failure modes.
- **What happens when recognition fails?** The API distinguishes an invalid image, unreadable page, timeout and provider failure. It flags uncertainty, keeps source lines, and never fills blank fields with example values. The user can compare against the image, add records and export the corrected result.
- **How do exports differ?** Both start with the same confirmed table. Python's CSV writer handles escaping and applies formula protection. Google Sheets receives literal strings in one atomic append with a unique metadata marker. The backend checks that marker if the write response is lost.
- **How is accuracy measured?** Pair predicted rows with references in order, then measure matched-row recall, exact field matches, missing and incorrect fields, and transcript character error rate. The benchmark also records unexpected rows, warning counts and wall time. A corrected export demonstrates human oversight and is excluded from recognition scores.
- **What was hard?** A plausible-looking table can hide column shifts, a missing row or a duplicated value. A provider can be good on print and poor on handwritten text in the same script. Retrying an external write after a disconnect also requires thinking about what happened on the other server.
- **What if two uploads arrive at once?** A test starts two API requests with the same document ID. Identical payloads both receive confirmation and the sheet gets one append. If the payloads differ, the losing request receives HTTP 409. A new scan gets a new ID, so deliberately rescanning the same paper can still create a second document.
- **What if someone edits the sheet at the same time?** The editor sends the original cells with the proposed change. The backend detects a stale snapshot before saving. A human editing Google Sheets directly can still race between the read and write. The in-process lock only coordinates requests to this server instance.
- **What if the model gives a plausible wrong answer?** Both the cell and the transcript can contain the same OCR error. The photo and human review are the final check. Show one missed word from a real sample and explain why a schema check alone cannot detect it.
- **What comes next?** Test multiple writers on actual phone photos, compare cloud and local models on the same ground truth, and investigate whether cropped row recognition improves difficult cursive. A production rollout would need explicit access control, traffic limits and a retention policy.
- **Why does it relate to TrafixCT?** The project illustrates a small perception-to-decision pipeline. Typed inputs, grounded outputs, measurable failure cases, user intervention and reliable integration are useful engineering patterns in mobility systems. The scanner remains an independent technical demo.
