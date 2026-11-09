/** Save, read, and share CSV files on Android. */

import { Directory, File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';

import { fetchCsv } from './api';
import type { ReviewedTable } from './types';

/**
 * Write the CSV into a folder picked by the user.
 * If writing fails, try to remove the incomplete file and keep the original error.
 */
export async function saveCsv(table: ReviewedTable) {
  const { bytes, filename } = await fetchCsv(table);
  let directory: Directory;
  try {
    directory = await Directory.pickDirectoryAsync();
  } catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ERR_PICKER_CANCELLED') return null;
    throw error;
  }
  const file = directory.createFile(filename, 'text/csv');
  try {
    file.write(bytes);
  } catch (error) {
    try { file.delete(); } catch { /* Keep the original save error. */ }
    throw error;
  }
  return file;
}

/** Read a CSV picked from the device and limit its size before preview. */
export async function openSavedCsv() {
  const selected = await File.pickFileAsync({ mimeTypes: ['text/csv', 'text/comma-separated-values', 'application/csv'] });
  if (selected.canceled) return null;
  if (selected.result.size > 8_000_000) throw new Error('Choose a CSV smaller than 8 MB for preview.');
  return selected.result.text();
}

/** Write a copy to the cache and open the Android share menu. */
export async function exportCsv(table: ReviewedTable) {
  const { bytes, filename } = await fetchCsv(table);
  const file = new File(Paths.cache, filename);
  file.create({ overwrite: true });
  file.write(bytes);
  if (!await Sharing.isAvailableAsync()) {
    throw new Error('CSV was created, but the Android share service is unavailable.');
  }
  await Sharing.shareAsync(file.uri, { mimeType: 'text/csv', dialogTitle: 'Share reviewed records' });
  return filename;
}

