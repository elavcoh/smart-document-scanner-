/** A message for the page, a row, or a specific column. */
/** Data shared by the scanner screens and the backend calls. */

export type Warning = { code: string; message: string; column: number | null };

/** The backend OCR result before the user makes edits. */
export type Extraction = {
  document_id: string;
  provider: string;
  raw_text: string;
  headings: string[];
  direction: 'rtl' | 'ltr';
  status: 'review_required';
  duration_ms: number;
  warnings: Warning[];
  records: {
    cells: (string | null)[];
    source_text: string;
    confidence: (number | null)[];
    warnings: Warning[];
  }[];
};

/** The confirmed headings and rows used for both export formats. */
export type ReviewedTable = {
  document_id: string;
  headings: string[];
  rows: string[][];
};

/** The receipt for a new or already confirmed upload. */
export type SheetsResult = {
  status: 'confirmed';
  spreadsheet_id: string;
  spreadsheet_url: string;
  rows_written: number;
  updated_range: string | null;
  replayed: boolean;
};

/** One editable row with its original OCR text and edit markers. */
export type DraftRow = {
  id: string;
  cells: string[];
  source: string;
  warnings: Warning[];
  manual: boolean;
  edited: number[];
};

/** The current document while the user is still reviewing it. */
export type Draft = {
  documentId: string;
  headings: string[];
  rows: DraftRow[];
  provider: string;
  raw: string;
  direction: 'rtl' | 'ltr';
  warnings: Warning[];
};

/** The original image URI and the working image used for OCR. */
export type Photo = { originalUri: string; uri: string; width: number; height: number };

/** A saved document or a group of older rows without an ID. */
export type SheetDocument = { document_id: string | null; headings: string[]; rows: string[][] };
/** The list of documents loaded from Sheets. */
export type SheetHistory = { documents: SheetDocument[] };
