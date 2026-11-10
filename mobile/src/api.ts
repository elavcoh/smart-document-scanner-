/**
 * Keep the calls from the app to the server in one place.
 * Build the URLs, read replies, and turn errors into messages the screens can show.
 * This file also keeps a local copy of a Sheets upload that may need a retry.
 */

import { File, Paths } from 'expo-file-system';

import type { Extraction, Photo, ReviewedTable, SheetDocument, SheetHistory, SheetsResult } from './types';

const URL_SETTING = process.env.EXPO_PUBLIC_API_URL?.trim().replace(/\/+$/, '');
// Keep the upload on the device so it can be retried after the app closes.
const pendingFile = new File(Paths.document, 'scanner-pending-sheets.json');

/** An API failure with a code the screen can check. */
export class ApiError extends Error {
  /** Keep the server error code with its message. */
  constructor(readonly code: string, message: string) {
    super(message);
  }
}

/** Check the configured server address and add the route path. */
function backendUrl(path: string) {
  if (!URL_SETTING || !/^https?:\/\/[^/]+$/.test(URL_SETTING)) {
    throw new ApiError('client_config', 'Set EXPO_PUBLIC_API_URL to the scanner backend address, then restart Expo.');
  }
  return URL_SETTING + path;
}

/**
 * Send a request with a timeout and decode the successful reply.
 * Use a common error format so every screen can show failures the same way.
 */
async function call<T>(
  path: string,
  options: RequestInit,
  timeout: number,
  decode: (response: Response) => Promise<T>,
): Promise<T> {
  const url = backendUrl(path);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    if (!response.ok) {
      const data = await response.json().catch(() => undefined);
      const hint = Array.isArray(data?.issues) ? data.issues[0]?.message : undefined;
      throw new ApiError(data?.code ?? 'api_error', hint ?? data?.message ?? `Server returned HTTP ${response.status}.`);
    }
    return await decode(response);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) {
      throw new ApiError('client_timeout', 'The request timed out. Check the Mac and retry.');
    }
    throw new ApiError('client_network', 'Could not complete the request to the Mac. Check Wi-Fi, the backend address and the firewall.');
  } finally {
    clearTimeout(timer);
  }
}

/** The two OCR choices shown in the photo preview. */
export type ExtractionMode = 'handwriting' | 'local';

/**
 * Send the working JPEG as a form upload.
 * Pass the selected OCR mode in the URL and return the server result for review.
 */
export function processPhoto(photo: Photo, mode: ExtractionMode) {
  const form = new FormData();
  form.append('image', new File(photo.uri), 'document.jpg');
  return call<Extraction>(`/api/documents/process?mode=${mode}`, { method: 'POST', body: form }, 80_000, response => response.json());
}

/** Make the request options for a JSON POST. */
function jsonPost(body: object): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

/** Check that the server responds and read its configuration flags. */
export function checkBackend() {
  return call<{ ocr_configured: boolean; sheets_configured: boolean; ocr_provider: string; handwriting_configured: boolean }>(
    '/health', {}, 5000, response => response.json(),
  );
}

/** Get the CSV file bytes and the name given by the server. */
export function fetchCsv(table: ReviewedTable) {
  return call('/api/exports/csv', jsonPost(table), 25_000, async response => {
    const attachment = response.headers.get('Content-Disposition')?.match(/filename="(document_records_\d{8}_\d{6}\.csv)"/);
    return {
      bytes: new Uint8Array(await response.arrayBuffer()),
      filename: attachment?.[1] ?? 'document_records.csv',
    };
  });
}

/** Get the CSV as text for the preview screen. */
export function previewCsv(table: ReviewedTable) {
  return call('/api/exports/csv', jsonPost(table), 25_000, response => response.text());
}

/** Ask the local server to save and open the file on the Mac. */
export function openCsvOnMac(table: ReviewedTable) {
  return call<{ filename: string }>('/api/exports/csv/mac', jsonPost(table), 25_000, response => response.json());
}

/**
 * Send the checked table to Google Sheets through the server.
 * Only accept a confirmed reply with the expected row count and a Sheets link.
 */
export function uploadSheets(table: ReviewedTable) {
  return call<SheetsResult>('/api/exports/sheets', jsonPost(table), 90_000, async response => {
    const data: SheetsResult = await response.json();
    if (data.status !== 'confirmed' || data.rows_written !== table.rows.length ||
        !data.spreadsheet_url?.startsWith('https://docs.google.com/spreadsheets/d/')) {
      throw new ApiError('sheets_outcome_unknown', 'The upload response could not be confirmed. Check the same saved upload again.');
    }
    return data;
  });
}

/** Store the exact table on the device before trying the Sheets write. */
export function savePending(table: ReviewedTable) {
  // Synchronous writes occur before sending anything to the backend.
  pendingFile.create({ overwrite: true });
  pendingFile.write(JSON.stringify(table));
}

/** Read a pending Sheets upload left on the device, if there is one. */
export async function loadPending(): Promise<ReviewedTable | null> {
  if (!pendingFile.exists) return null;
  return JSON.parse(await pendingFile.text()) as ReviewedTable;
}

/** Delete the local pending file after confirmation or user dismissal. */
export function clearPending() {
  if (pendingFile.exists) pendingFile.delete();
}

/** Get the documents saved in the connected workbook. */
export function loadHistory() {
  return call<SheetHistory>('/api/records', {}, 45_000, response => response.json());
}

/**
 * Send the ID, headings, and both versions of the rows.
 * The server can reject a save if the rows changed after we loaded them.
 */
export function updateDocument(document: SheetDocument, rows: string[][]) {
  const { document_id, headings, rows: original } = document;
  return call<SheetDocument>('/api/records/edit', jsonPost({ document_id, headings, original, rows }),
    70_000, response => response.json());
}

/** Build the local browser editor URL for a saved document. */
export function documentEditorUrl(documentId: string) {
  return backendUrl(`/documents/${encodeURIComponent(documentId)}`);
}

/** Ask the server for a Sheets link that shows one document. */
export function getDocumentSheet(documentId: string) {
  return call<{ spreadsheet_url: string }>(`/api/records/${encodeURIComponent(documentId)}/sheet`,
    { method: 'POST' }, 70_000, response => response.json());
}
