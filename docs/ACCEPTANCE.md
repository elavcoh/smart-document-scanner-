# Acceptance audit

Checked on 8 November 2026. “Verified” describes what actually ran. The scope column distinguishes the Android emulator, the browser and server tests.

| Check | Status | Scope and evidence |
| --- | --- | --- |
| Install and run | Verified | Expo dependency compatibility, TypeScript and Android bundle checks. The project launched in Expo Go on the Pixel 8 emulator. |
| Photo selection and review | Verified on emulator | A gallery image was processed and reviewed. Physical camera capture and real-phone Wi-Fi were not retested. |
| Print recognition | Verified | Tesseract read all 12 reference fields on each of the English and Hebrew samples. The tilted, low-quality and blank samples were also evaluated. |
| Handwriting recognition | Verified on sample set | Live Gemini recognized 1/1 exact Hebrew fields, 4/4 on the mixed page and 2/3 on the English ink page. Review remains required. |
| Hebrew editing | Verified | Hebrew data and headers were kept in a right-to-left browser grid. The saved value was read back from Google Sheets after reloading. |
| Document history | Verified | The live history returned separate documents with their own column headings. The new Hebrew sample was found alongside existing English uploads. |
| CSV file | Verified | API returned UTF-8 with BOM and the expected columns and rows. On the emulator, the app saved a physical CSV and reopened that file. The Mac open command was tested with a simulated system launch. |
| Google Sheets export | Verified live | Service-account upload and read-back succeeded for the Hebrew sample. A repeated request did not duplicate it. The per-document tab showed the selected document. |
| Browser and app share the same data | Verified | Browser and app use the same save endpoint. The live test checked an edit, a stale edit returning HTTP 409 and read-back from the document API. |
| Error handling | Verified in tests | Invalid and oversized images, missing handwriting credentials, unreadable output, invalid rows, provider failures and export retries. |
| Dependency audit | Checked | npm reported four moderate advisories in the Expo fingerprint toolchain, and zero high or critical advisories in the audited dependency tree. Retained Expo SDK 57 compatibility. |
| Production deployment | Out of scope | The backend is a local demo. Public hosting needs authentication, access controls and a retention policy. |

See [raw results and methodology](EVALUATION.md), [launch instructions](../README.md), and the [interview walkthrough](INTERVIEW_PREPARATION.md).

## Recheck on 9 November 2026

- **Pixel 8 emulator.** Reviewed a mixed English and Hebrew result, opened its transcript, confirmed the records, and saved a CSV in the emulator's demo folder. The app read back the saved file. Verified that an empty manual record blocks confirmation and that a manual draft can be resumed from Home with its edited value intact. Opened a document from live Sheets history in the app and in its browser editor.
- **Running API.** Repeated ten local OCR cases. Printed English and Hebrew each matched 12/12 fields. The blank image was rejected, and the low-light sample produced image-quality warnings and incorrect records. Gemini Flash-Lite matched 3/3 fields on English handwriting, 1/1 on Hebrew handwriting, and 3/4 on the mixed page. The mixed result omitted one Hebrew word from both its transcript and its proposed cell.
- **Export and concurrency.** Verified CSV escaping, UTF-8, literal formula protection, and leading zeros in the exported file. Sheets history and document read-back used the live workbook. The repeat-write and conflicting-write scenarios used a simulated Sheets service so the retest did not add or edit records in the live workbook.
- **Automated checks.** All 44 Python tests passed. TypeScript passed with unused-code checks enabled and the Android bundle built. A physical Android device, real camera capture, and phone-to-Mac Wi-Fi are outside this recheck.

For a physical-device check, use the same trusted Wi-Fi on the Mac and phone. Bind the backend to `0.0.0.0` and start Expo with `EXPO_PUBLIC_API_URL=http://<mac-ip>:8000`. Open the QR code in a compatible Expo Go build, then check the connection from the app. Keep the backend on the local network.
