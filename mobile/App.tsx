/**
 * Run the main flow of the scanner app.
 * Keep track of the current photo, the rows being checked, and saved documents.
 * Send work to the API and show the right screen for each step.
 */

import { randomUUID } from 'expo-crypto';
import * as ImagePicker from 'expo-image-picker';
import * as WebBrowser from 'expo-web-browser';
import { useEffect, useRef, useState } from 'react';
import { Alert, BackHandler, Image, Keyboard, Linking, ScrollView, Share, StatusBar, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { ApiError, checkBackend, clearPending, documentEditorUrl, getDocumentSheet, loadDocument, loadHistory, loadPending, openCsvOnMac, previewCsv, processPhoto, savePending, updateDocument, uploadSheets, type ExtractionMode } from './src/api';
import { exportCsv, openSavedCsv, saveCsv } from './src/csv';
import { CsvPreview, type CsvPreviewData } from './src/CsvPreview';
import { preparePhoto, rotatePhoto } from './src/photo';
import { Review } from './src/Review';
import type { Draft, DraftRow, Photo, ReviewedTable, SheetDocument, SheetHistory, SheetsResult } from './src/types';
import { Action, Banner, Field, styles, type Notice } from './src/ui';

/** The screens in the scanner flow. */
type Screen = 'home' | 'preview' | 'review' | 'export' | 'history' | 'editDocument';

/** Make a default column name in the draft's language. */
function columnName(index: number, direction: Draft['direction']) {
  return direction === 'rtl' ? `עמודה ${index}` : `Column ${index}`;
}

/** Make an empty row with a stable ID for the list. */
function blankRow(width: number): DraftRow {
  return { id: randomUUID(), cells: Array(width).fill(''), source: '', warnings: [], manual: true, edited: [] };
}

/**
 * Hold the state for the scanner screens.
 * Keep the working draft, the checked export, and the saved rows apart.
 * This lets the user edit a draft without changing a saved document.
 */
function Scanner() {
  const [screen, setScreen] = useState<Screen>('home');
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [original, setOriginal] = useState(false);
  const [previewFromReview, setPreviewFromReview] = useState(false);
  // The draft changes during review. The checked table is what we send to export.
  const [draft, setDraft] = useState<Draft | null>(null);
  const [reviewed, setReviewed] = useState<ReviewedTable | null>(null);
  const [pending, setPending] = useState<ReviewedTable | null>(null);
  const [sheetUpload, setSheetUpload] = useState<{ documentId: string; receipt: SheetsResult } | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState('');
  const [history, setHistory] = useState<SheetHistory | null>(null);
  // Keep the saved document and the new edits as two sets of rows.
  const [editing, setEditing] = useState<SheetDocument | null>(null);
  const [editedRows, setEditedRows] = useState<string[][]>([]);
  const [csvPreview, setCsvPreview] = useState<CsvPreviewData | null>(null);
  const hasUnsavedEdits = !!editing && editedRows.some((row, index) =>
    row.some((cell, column) => cell !== editing.rows[index][column]));
  // Export saved history rows. Edits only become part of the saved table after a successful save.
  const savedHistoryTable = editing?.document_id
    ? { document_id: editing.document_id, headings: editing.headings, rows: editing.rows }
    : null;
  const running = useRef(false);
  // Lock a draft while Sheets may be saving it or after the upload is confirmed.
  const uploaded = !!draft && draft.documentId === sheetUpload?.documentId;
  const locked = uploaded || (!!draft && draft.documentId === pending?.document_id);
  const exportTable = reviewed ?? pending;
  const confirmed = sheetUpload && exportTable?.document_id === sheetUpload.documentId ? sheetUpload.receipt : null;

  /** Switch screens and clear the previous message. */
  function go(next: Screen) {
    setNotice(null);
    setScreen(next);
  }

  /** Show an error message that the user can read. */
  function report(error: unknown) {
    const message = error instanceof Error ? error.message : 'The operation could not be completed.';
    setNotice({ tone: 'error', text: message });
  }

  /**
   * Run one action at a time and show its progress.
   * The ref blocks a second tap before React updates the buttons.
   */
  async function run(label: string, operation: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(label);
    setNotice(null);
    try {
      await operation();
    } catch (error) {
      report(error);
    } finally {
      running.current = false;
      setBusy('');
    }
  }

  /**
   * Take the photo picked by Android and prepare the working JPEG.
   * After it is ready, clear the old draft and show the new photo.
   */
  async function acceptPhoto(asset: ImagePicker.ImagePickerAsset) {
    const next = await preparePhoto(asset);
    setPhoto(next);
    setOriginal(false);
    setPreviewFromReview(false);
    setDraft(null);
    setReviewed(null);
    go('preview');
  }

  // Restore a pending upload and check for a photo returned after Android closed the activity.
  useEffect(() => {
    void loadPending().then(setPending).catch(report);
    void ImagePicker.getPendingResultAsync().then(result => {
      if (!result) return;
      if ('code' in result) {
        report(new Error(result.message));
      } else if (!result.canceled) {
        void run('Preparing photo', () => acceptPhoto(result.assets[0]));
      }
    }).catch(report);
  }, []);

  /**
   * Handle the Android Back button while inside the scanner.
   * Go back to the right screen without clearing the draft. Let Android handle Back on Home.
   */
  function back() {
    if (running.current) return true;
    if (screen === 'home') return false;
    if (screen === 'history') {
      go('home');
    } else if (screen === 'editDocument') {
      confirmDiscardEdits(() => go('history'));
    } else if (screen === 'preview') {
      go(previewFromReview && draft ? 'review' : 'home');
    } else if (screen === 'review') {
      setPreviewFromReview(false);
      go(photo ? 'preview' : 'home');
    } else {
      go(draft && draft.documentId === reviewed?.document_id ? 'review' : 'home');
    }
    return true;
  }

  // Use the current screen state when Android sends a Back event.
  useEffect(() => {
    const listener = BackHandler.addEventListener('hardwareBackPress', back);
    return () => listener.remove();
  }, [screen, draft, reviewed, previewFromReview, photo, hasUnsavedEdits]);

  /** Open the camera or gallery and prepare the selected image. */
  async function pick(camera: boolean) {
    if (camera) {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) {
        if (!permission.canAskAgain) {
          Alert.alert('Camera access is off', 'Allow camera access in Android Settings, or choose an existing photo.', [
            { text: 'Cancel', style: 'cancel' },
            { text: 'Open settings', onPress: () => void Linking.openSettings().catch(report) },
          ]);
          return;
        }
        throw new Error('Camera permission was declined. You can still choose a photo from the gallery.');
      }
    }
    const options: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], allowsEditing: false, quality: 1 };
    const result = camera ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
    if (!result.canceled) await acceptPhoto(result.assets[0]);
  }

  /** Ask before replacing a photo when a draft is open. */
  function replace(camera: boolean) {
    const start = () => void run(camera ? 'Opening camera' : 'Opening gallery', () => pick(camera));
    if (draft) Alert.alert('Replace this document?', 'Selecting a new photo discards the current editor. Any saved Sheets upload remains available.', [
      { text: 'Cancel', style: 'cancel' }, { text: 'Choose new photo', onPress: start },
    ]);
    else start();
  }

  /**
   * Ask the user how to read the photo.
   * Explain which choice sends it to Google and when a new result can replace a draft.
   */
  function chooseExtraction() {
    const options = 'Use Gemini for print, handwriting, or both in Hebrew or English. This sends the photo to Google. For print only, you can process it on the Mac.';
    const message = draft ? `${options}\n\nSuccessful extraction replaces the current draft and its edits.` : options;
    Alert.alert(draft ? 'Extract records again?' : 'Extract records?', message, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'On the Mac', onPress: () => extract('local') },
      { text: 'Use Gemini', onPress: () => extract('handwriting') },
    ]);
  }

  /**
   * Send the working photo with the selected OCR mode.
   * Turn missing cell values into empty input fields and keep the original text and warnings.
   */
  function extract(mode: ExtractionMode) {
    if (!photo) return;
    void run('Extracting records', async () => {
      const result = await processPhoto(photo, mode);
      setDraft({
        documentId: result.document_id, headings: result.headings, direction: result.direction,
        provider: result.provider, warnings: result.warnings, raw: result.raw_text,
        rows: result.records.map((row, index) => ({
          id: `ocr-${index}`, cells: row.cells.map(value => value ?? ''), source: row.source_text,
          warnings: row.warnings, manual: false, edited: [],
        })),
      });
      setReviewed(null);
      go('review');
    });
  }

  /** Ask before replacing the current draft with manual entry. */
  function chooseManualLanguage() {
    if (draft) {
      Alert.alert('Start a new document?', 'This replaces the draft currently open.', [
        { text: 'Cancel', style: 'cancel' }, { text: 'Continue', onPress: chooseLanguage },
      ]);
    } else {
      chooseLanguage();
    }
  }

  /** Let the user pick the starting language for the draft. */
  function chooseLanguage() {
    Alert.alert('Document language', 'Choose a starting layout. Both layouts accept Hebrew and English.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'English', onPress: () => manual('ltr', screen === 'preview') },
      { text: 'עברית', onPress: () => manual('rtl', screen === 'preview') },
    ]);
  }

  /** Start a blank draft and keep the photo if manual entry came from its preview. */
  function manual(direction: Draft['direction'], keepPhoto: boolean) {
    if (!keepPhoto) setPhoto(null);
    setDraft({
      documentId: randomUUID(), provider: 'Manual entry', raw: '', warnings: [], direction,
      headings: [columnName(1, direction)], rows: [blankRow(1)],
    });
    setReviewed(null);
    go('review');
  }

  /** Apply an edit and clear the old export snapshot if the draft is unlocked. */
  function changeDraft(change: (previous: Draft) => Draft) {
    if (locked) return;
    setReviewed(null);
    setDraft(current => current && change(current));
  }

  /**
   * Check the headings and rows before showing Export.
   * Save the checked values in one table that both CSV and Google Sheets can use.
   */
  function confirm() {
    if (!draft) return;
    Keyboard.dismiss();
    const headings = draft.headings.map(value => value.trim());
    if (headings.some(value => !value) || new Set(headings.map(value => value.toLowerCase())).size !== headings.length) {
      report(new Error('Give every column a distinct name before confirming.'));
      return;
    }
    if (draft.rows.some(row => !row.cells.some(value => value.trim()))) {
      report(new Error('Fill or remove each wholly empty record before confirming.'));
      return;
    }
    setReviewed({ document_id: draft.documentId, headings, rows: draft.rows.map(row => row.cells) });
    go('export');
  }

  /**
   * Save a pending upload before sending the table.
   * If the result is unclear, keep the exact payload so a retry uses the same ID.
   * After a confirmed response, keep the receipt and clear the pending upload.
   */
  function sendToSheets(table: ReviewedTable) {
    void run('Checking Google Sheets', async () => {
      if (!pending) {
        savePending(table);
        setPending(table);
      }
      try {
        const result = await uploadSheets(table);
        setReviewed(table);
        setSheetUpload({ documentId: table.document_id, receipt: result });
        setPending(null);
        clearPending();
      } catch (error) {
        // Clear a new pending upload only for errors that rule out a completed write.
        const beforeAnyWrite = ['client_config', 'sheets_not_configured', 'sheets_credentials',
          'sheets_tab_missing', 'sheets_heading_mismatch', 'invalid_request'];
        if (!pending && error instanceof ApiError && beforeAnyWrite.includes(error.code)) {
          setPending(null);
          clearPending();
        }
        throw error;
      }
    });
  }

  /** Open the Google Sheets link in Chrome and report opening errors. */
  function openSheetInChrome(url: string) {
    void WebBrowser.openBrowserAsync(url, { browserPackage: 'com.android.chrome' }).catch(() =>
      report(new Error('Chrome could not open the spreadsheet. Check that Chrome is installed and try again.')));
  }

  /**
   * Ask Android for a folder and save the CSV there.
   * Read the saved file back so the preview shows what was actually saved.
   */
  function saveCsvToDevice(table: ReviewedTable) {
    Keyboard.dismiss();
    void run('Saving CSV', async () => {
      const file = await saveCsv(table);
      if (!file) return;
      try {
        setCsvPreview({ text: await file.text(), table, saved: true });
        setNotice({ tone: 'success', text: 'CSV saved and opened from the folder you selected.' });
      } catch {
        setNotice({ tone: 'warning', text: 'CSV saved. Use Open saved CSV on the home screen to try opening it again.' });
      }
    });
  }

  /** Open a CSV that is already on the Android device. */
  function chooseSavedCsv() {
    void run('Opening saved CSV', async () => {
      const text = await openSavedCsv();
      if (text !== null) {
        setCsvPreview({ text, saved: true });
      }
    });
  }

  /** Load the CSV text and show it before saving or sharing. */
  function showCsvPreview(table: ReviewedTable) {
    Keyboard.dismiss();
    void run('Loading CSV', async () => {
      const text = await previewCsv(table);
      setCsvPreview({ text, table });
    });
  }

  /** Ask the local server to save and open the CSV on the Mac. */
  function openOnMac(table: ReviewedTable) {
    Keyboard.dismiss();
    void run('Opening CSV on Mac', async () => {
      const { filename } = await openCsvOnMac(table);
      setNotice({ tone: 'success', text: `Opened ${filename} on the Mac. A copy is in Downloads.` });
    });
  }

  /** Create the CSV and open the Android share menu. */
  function shareCsv(table: ReviewedTable) {
    void run('Creating CSV', async () => {
      const filename = await exportCsv(table);
      setNotice({ tone: 'success', text: `Created ${filename}. Use the Android share options to save or send it.` });
    });
  }

  /**
   * Open our browser editor using the saved document ID.
   * The editor reads the same rows and saves through the same server as the app.
   */
  function openWebEditor(documentId: string) {
    Keyboard.dismiss();
    void run('Opening browser editor', async () => {
      await WebBrowser.openBrowserAsync(documentEditorUrl(documentId), { browserPackage: 'com.android.chrome' });
    });
  }

  /**
   * Ask for the Sheets link for the open document.
   * The server can create its own tab that shows the rows for this document.
   */
  function openHistorySheet() {
    if (!editing?.document_id) return;
    const documentId = editing.document_id;
    Keyboard.dismiss();
    void run('Opening Google Sheets', async () => {
      const { spreadsheet_url } = await getDocumentSheet(documentId);
      openSheetInChrome(spreadsheet_url);
    });
  }

  /** Use the same CSV action in export and document history. */
  function csvButton(table: ReviewedTable) {
    return <Action title="View & export CSV" disabled={!!busy} busy={busy === 'Loading CSV'}
      onPress={() => showCsvPreview(table)} />;
  }

  /** Close the CSV preview once the current action is done. */
  function closeCsv() {
    if (running.current) return;
    setCsvPreview(null);
    setNotice(null);
  }

  /** Load the latest documents from the connected workbook. */
  function refreshHistory() {
    void run('Loading history', async () => setHistory(await loadHistory()));
  }

  /** Open a saved document and start editing from its current rows. */
  function openDocument(document: SheetDocument) {
    setEditing(document);
    setEditedRows(document.rows);
    go('editDocument');
  }

  /** Protect local edits before leaving or loading saved rows again. */
  function confirmDiscardEdits(proceed: () => void) {
    if (!hasUnsavedEdits) {
      proceed();
      return;
    }
    Alert.alert('Discard unsaved edits?', 'Your changes have not been saved to Google Sheets.', [
      { text: 'Keep editing', style: 'cancel' },
      { text: 'Discard changes', style: 'destructive', onPress: proceed },
    ]);
  }

  /** Read the latest saved version before opening or refreshing a document. */
  function openLatestDocument(document: SheetDocument) {
    const documentId = document.document_id;
    if (!documentId) {
      openDocument(document);
      return;
    }
    void run('Loading document', async () => {
      const latest = await loadDocument(documentId);
      setHistory(current => current && {
        documents: current.documents.map(item => item.document_id === documentId ? latest : item),
      });
      openDocument(latest);
    });
  }

  /** Find the confirmed upload by ID in the latest history and open it. */
  function openUploadedDocument(documentId: string) {
    void run('Opening document', async () => {
      const latest = await loadHistory();
      const document = latest.documents.find(item => item.document_id === documentId);
      if (!document) throw new Error('This document could not be found in the connected spreadsheet.');
      setHistory(latest);
      openDocument(document);
    });
  }

  /**
   * Send the changed rows together with the rows we first loaded.
   * When the server confirms the edit, replace that document in history.
   */
  function saveDocument() {
    if (!editing?.document_id || !history) return;
    Keyboard.dismiss();
    void run('Saving document', async () => {
      const saved = await updateDocument(editing, editedRows);
      setHistory({
        ...history,
        documents: history.documents.map(document => document.document_id === saved.document_id ? saved : document),
      });
      go('history');
      setNotice({ tone: 'success', text: 'Document updated in Google Sheets.' });
    });
  }

  /** Ask before clearing a saved upload with a possibly unknown result. */
  function discardPending() {
    Alert.alert('Clear the saved upload?', 'The spreadsheet may already contain these records. Check it before uploading this document again.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Clear saved upload', style: 'destructive', onPress: () => {
        try {
          clearPending();
          setPending(null);
          if (!reviewed) go('home');
        } catch (error) {
          report(error);
        }
      } },
    ]);
  }

  // Review sends back edits. The main screen keeps the draft and its column sizes.
  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar barStyle="dark-content" />
      {csvPreview && <CsvPreview preview={csvPreview} busy={busy} notice={notice}
        onClose={closeCsv} onSave={saveCsvToDevice} onShare={shareCsv} onOpenOnMac={openOnMac} />}
      <View style={styles.toolbar}>
        <Text style={styles.kicker}>SMART DOCUMENT SCANNER</Text>
        <Text style={styles.toolbarTitle}>
          {screen === 'home' ? 'Capture' : screen === 'preview' ? '1 / Preview' : screen === 'review' ? '2 / Review' : screen === 'history' ? 'History' : screen === 'editDocument' ? 'Edit document' : '3 / Export'}
        </Text>
      </View>
      {!!notice && <View style={{ paddingHorizontal: 20, paddingTop: 10 }}>
        <Banner tone={notice.tone}>{notice.text}</Banner>
      </View>}
      {screen === 'review' && draft ? (
        <Review draft={draft} locked={locked} uploaded={uploaded}
          preview={photo ? () => { setPreviewFromReview(true); go('preview'); } : undefined} confirm={confirm}
          updateHeading={(index, value) => changeDraft(old => ({
            ...old, headings: old.headings.map((heading, i) => i === index ? value : heading),
          }))}
          updateCell={(id, index, value) => changeDraft(old => ({
            ...old, rows: old.rows.map(row => row.id !== id ? row : {
              ...row, cells: row.cells.map((cell, i) => i === index ? value : cell),
              edited: row.edited.includes(index) ? row.edited : [...row.edited, index],
            }),
          }))}
          addColumn={() => changeDraft(old => ({
            ...old, headings: [...old.headings, columnName(old.headings.length + 1, old.direction)],
            rows: old.rows.map(row => ({ ...row, cells: [...row.cells, ''] })),
          }))}
          addRow={() => changeDraft(old => ({ ...old, rows: [...old.rows, blankRow(old.headings.length)] }))}
          removeRow={id => changeDraft(old => ({ ...old, rows: old.rows.filter(row => row.id !== id) }))} />
      ) : <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.content}>
        {/* Home brings together new scans, unfinished work, and saved documents. */}
        {screen === 'home' && <>
          <Text style={styles.title}>Turn paper into records</Text>
          <Text style={styles.body}>Photograph a single table or list. Review the extracted rows, then share a CSV or upload them to Google Sheets.</Text>
          <View style={styles.card}>
            <Action title="Scan Document" variant="primary" onPress={() => replace(true)} disabled={!!busy} />
            <Action title="Choose From Gallery" onPress={() => replace(false)} disabled={!!busy} />
            {!!busy && <Banner>{busy}…</Banner>}
          </View>
          <Action title="Enter records manually" disabled={!!busy} onPress={chooseManualLanguage} />
          <Action title="Open saved CSV" disabled={!!busy} busy={busy === 'Opening saved CSV'} onPress={chooseSavedCsv} />
          <Action title="Document history" disabled={!!busy} onPress={() => {
            setHistory(null);
            go('history');
            refreshHistory();
          }} />
          {(draft || photo) && <Action title="Resume current document" disabled={!!busy} onPress={() => go(draft ? 'review' : 'preview')} />}
          {pending && <View style={styles.card}>
            <Text style={styles.sectionTitle}>Saved Sheets upload</Text>
            <Text style={styles.body}>{pending.rows.length} reviewed records are saved for confirmation.</Text>
            <Action title="View saved upload" disabled={!!busy} onPress={() => { setReviewed(null); go('export'); }} />
          </View>}
          <Action title="Check backend connection" disabled={!!busy} onPress={() => void run('Checking connection', async () => {
            const health = await checkBackend();
            setNotice({ tone: 'success', text: `Mac backend reached. OCR ${health.ocr_provider} ${health.ocr_configured ? 'configured' : 'needs setup'}. Sheets ${health.sheets_configured ? 'configured' : 'needs setup'}. Handwriting ${health.handwriting_configured ? 'configured' : 'needs a Gemini key'}.` });
          })} />
        </>}
        {/* History shows one card per document, even when it has several rows. */}
        {screen === 'history' && <>
          <Text style={styles.title}>Document history</Text>
          <Text style={styles.body}>Open a document to view all its records together.</Text>
          <Action title="Refresh" onPress={refreshHistory} disabled={!!busy} busy={busy === 'Loading history'} />
          {history?.documents.length === 0 && <Banner>No uploaded documents yet.</Banner>}
          {history?.documents.map((document, index) => (
            <View key={document.document_id ?? `ungrouped-${index}`} style={styles.card}>
              <Text style={styles.sectionTitle}>{document.document_id ? `Document ${index + 1}` : 'Ungrouped records'}</Text>
              <Text style={styles.body}>{document.rows.length} records</Text>
              <Action title="Open document" disabled={!!busy} onPress={() => openLatestDocument(document)} />
            </View>
          ))}
          <Action title="Home" disabled={!!busy} onPress={() => go('home')} />
        </>}
        {/* Keep the saved rows apart from the current edits until the user saves. */}
        {screen === 'editDocument' && editing && history && <>
          <Text style={styles.title}>{editing.document_id ? 'Document records' : 'Ungrouped records'}</Text>
          {!editing.document_id && <Banner>These older rows could not be matched to an uploaded document.</Banner>}
          {savedHistoryTable && <View style={styles.card}>
            <Text style={styles.sectionTitle}>Saved document</Text>
            <Action title="Refresh saved rows" disabled={!!busy} busy={busy === 'Loading document'}
              onPress={() => confirmDiscardEdits(() => openLatestDocument(editing))} />
            <Action title="Edit in browser" disabled={!!busy} busy={busy === 'Opening browser editor'}
              onPress={() => openWebEditor(savedHistoryTable.document_id)} />
            <Action title="View in Google Sheets" disabled={!!busy}
              busy={busy === 'Opening Google Sheets'} onPress={openHistorySheet} />
            {csvButton(savedHistoryTable)}
          </View>}
          {editedRows.map((row, rowIndex) => (
            <View key={rowIndex} style={styles.card}>
              <Text style={styles.sectionTitle}>Record {rowIndex + 1}</Text>
              {editing.headings.map((heading, column) => (
                <Field key={column} label={heading} accessibilityLabel={`Record ${rowIndex + 1}, ${heading}`}
                  fallback={/[\u05d0-\u05ea]/.test(heading) ? 'rtl' : 'ltr'} autoCapitalize="none" multiline
                  editable={!!editing.document_id && !busy} value={row[column]} maxLength={3000}
                  onChangeText={value => setEditedRows(rows => rows.map((cells, i) =>
                    i === rowIndex ? cells.map((cell, j) => j === column ? value : cell) : cells))} />
              ))}
            </View>
          ))}
          {!!editing.document_id && <Action title="Save document to Google Sheets" variant="primary" onPress={saveDocument}
            disabled={!!busy || editedRows.some(row => !row.some(cell => cell.trim()))}
            busy={busy === 'Saving document'} />}
          <Action title="Back to history" disabled={!!busy}
            onPress={() => confirmDiscardEdits(() => go('history'))} />
        </>}
        {/* Preview lets the user check the photo before picking an OCR mode. */}
        {screen === 'preview' && photo && <>
          <Text style={styles.title}>Check the page</Text>
          <Text style={styles.body}>Keep the page upright and make sure every row is visible.</Text>
          <Image source={{ uri: original ? photo.originalUri : photo.uri }} accessibilityLabel="Selected document"
            resizeMode="contain" style={styles.photo} />
          {draft && <Action title="Return to records" variant="primary" onPress={() => go('review')} disabled={!!busy} />}
          {!locked && <Action title={busy === 'Extracting records' ? 'Extracting records…' : draft ? 'Extract again' : 'Extract Records'}
            variant={draft ? 'secondary' : 'primary'} busy={busy === 'Extracting records'} disabled={!!busy} onPress={chooseExtraction} />}
          {!!busy && busy !== 'Extracting records' && <Banner>{busy}…</Banner>}
          <Action title={original ? 'Show working image' : 'Show original image'} onPress={() => setOriginal(!original)} disabled={!!busy} />
          <Action title="Rotate 90°" disabled={!!busy} onPress={() => void run('Rotating photo', async () => {
            setPhoto(await rotatePhoto(photo)); setOriginal(false);
          })} />
          <Action title="Retake photo" onPress={() => replace(true)} disabled={!!busy} />
          <Action title="Replace from gallery" onPress={() => replace(false)} disabled={!!busy} />
          {!draft && <Action title="Enter records manually" onPress={chooseManualLanguage} disabled={!!busy} />}
          <Action title="Home" onPress={() => go('home')} disabled={!!busy} />
        </>}
        {/* Both export buttons use the same reviewed table. */}
        {screen === 'export' && exportTable && <>
          <Text style={styles.title}>Export records</Text>
          <Text style={styles.body}>{exportTable.rows.length} reviewed records · {exportTable.headings.length} columns</Text>
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>CSV file</Text>
            {csvButton(exportTable)}
          </View>
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Google Sheets</Text>
            {confirmed ? <>
              <Banner tone="success">{confirmed.rows_written} records confirmed in Google Sheets.</Banner>
              <Action title="View this document" variant="primary" disabled={!!busy}
                busy={busy === 'Opening document'} onPress={() => openUploadedDocument(exportTable.document_id)} />
              <Action title="Open shared sheet in Chrome" disabled={!!busy}
                onPress={() => openSheetInChrome(confirmed.spreadsheet_url)} />
              <Action title="Share link" onPress={() => void Share.share({ message: confirmed.spreadsheet_url }).catch(report)} />
              <Text style={styles.body}>The shared sheet contains rows from all uploaded documents.</Text>
            </> : <>
              {pending && <Banner tone="warning">
                {pending.document_id === exportTable.document_id
                  ? 'A reviewed upload is saved. Check the same request for a confirmed result.'
                  : 'Check the saved upload for the earlier document from Home before sending another.'}
              </Banner>}
              <Action title={pending ? 'Check or retry saved upload' : 'Upload to Google Sheets'}
                disabled={!!busy || (!!pending && pending.document_id !== exportTable.document_id)}
                busy={busy === 'Checking Google Sheets'} onPress={() => sendToSheets(pending ?? exportTable)} />
              {pending?.document_id === exportTable.document_id && <Action title="Clear saved upload" variant="danger"
                disabled={!!busy} onPress={discardPending} />}
            </>}
          </View>
          {draft && draft.documentId === reviewed?.document_id && <Action title="Back to review" onPress={() => go('review')} disabled={!!busy} />}
          <Action title="Home" onPress={() => go('home')} disabled={!!busy} />
        </>}
      </ScrollView>}
    </SafeAreaView>
  );
}

/** Set up safe screen spacing before showing the scanner. */
export default function App() {
  return <SafeAreaProvider><Scanner /></SafeAreaProvider>;
}
