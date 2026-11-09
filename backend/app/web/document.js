/** Load and edit one saved scanner document in the browser. */

'use strict';

const documentId = location.pathname.split('/').filter(Boolean).at(-1);
const form = document.querySelector('form');
const save = document.querySelector('#save');
const reload = document.querySelector('#reload');
const statusLine = document.querySelector('#status');
const grid = document.querySelector('.grid');
const head = document.querySelector('thead');
const body = document.querySelector('tbody');
let headings = [];
let original = null;
let fields = [];
let busy = false;

/** Show the current status or error above the table. */
function message(text, tone = '') {
  statusLine.textContent = text;
  statusLine.dataset.tone = tone;
}

/** Read the cells and keep original line endings in unchanged values. */
function values() {
  return fields.map((row, i) => row.map((field, j) => {
    const previous = original[i][j];
    return field.value === previous.replace(/\r\n?/g, '\n') ? previous : field.value;
  }));
}

/** Check if any cell differs from the loaded table. */
function hasChanges() {
  return original !== null && JSON.stringify(values()) !== JSON.stringify(original);
}

/** Block edits while busy and enable save only when something changed. */
function updateControls() {
  save.disabled = busy || !hasChanges();
  reload.disabled = busy;
  for (const field of fields.flat()) field.readOnly = busy;
}

/** Read an API reply and show its useful error message if the call fails. */
async function json(path, options) {
  const response = await fetch(path, { ...options, cache: 'no-store' });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.issues?.[0]?.message ?? result.message ?? 'Could not complete the request.');
  }
  return result;
}

/**
 * Build the editor grid from the saved rows and headings.
 * Use plain text for headings and let each input choose its own text direction.
 */
function showTable(columns, rows) {
  headings = columns;
  grid.dir = /[\u05d0-\u05ea]/.test(headings.join('')) ? 'rtl' : 'ltr';
  original = rows;
  const header = document.createElement('tr');
  for (const title of ['Row', ...headings]) {
    const cell = document.createElement('th');
    cell.scope = 'col';
    cell.dir = 'auto';
    cell.textContent = title;
    header.append(cell);
  }
  head.replaceChildren(header);
  body.replaceChildren();
  fields = rows.map((row, i) => {
    const tr = document.createElement('tr');
    const number = document.createElement('th');
    number.scope = 'row';
    number.textContent = String(i + 1);
    tr.append(number);
    const inputs = row.map((value, j) => {
      const cell = document.createElement('td');
      const input = document.createElement('textarea');
      input.rows = 1;
      input.dir = 'auto';
      input.maxLength = 3000;
      input.value = value;
      input.setAttribute('aria-label', `Row ${i + 1}, ${headings[j]}`);
      input.addEventListener('input', updateControls);
      input.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
        event.preventDefault();
        fields[i + 1]?.[j]?.focus();
      });
      cell.append(input);
      tr.append(cell);
      return input;
    });
    body.append(tr);
    return inputs;
  });
  grid.hidden = false;
}

/** Fetch the current rows, asking first if local edits would be lost. */
async function load() {
  if (busy || (hasChanges() && !confirm('Discard your unsaved edits and reload?'))) return;
  busy = true;
  updateControls();
  message('Loading document…');
  try {
    const result = await json(`/api/records/${encodeURIComponent(documentId)}`);
    showTable(result.headings, result.rows);
    message(`${result.rows.length} records loaded.`);
  } catch (error) {
    message(error.message, 'error');
  } finally {
    busy = false;
    updateControls();
  }
}

/** Send the new rows and the original values to check for a stale edit. */
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !hasChanges()) return;
  const rows = values();
  if (rows.some(row => row.every(cell => !cell.trim()))) {
    message('Keep at least one value in each row before saving.', 'error');
    return;
  }
  busy = true;
  updateControls();
  message('Saving changes…');
  try {
    const result = await json('/api/records/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_id: documentId, headings, original, rows }),
    });
    original = result.rows;
    message('Saved to Google Sheets.', 'success');
  } catch (error) {
    message(error.message, 'error');
  } finally {
    busy = false;
    updateControls();
  }
});

reload.addEventListener('click', load);
/** Warn before leaving the page with unsaved changes. */
window.addEventListener('beforeunload', event => {
  if (!hasChanges()) return;
  event.preventDefault();
  event.returnValue = '';
});
void load();
