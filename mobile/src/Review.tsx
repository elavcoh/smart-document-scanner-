/**
 * Show the draft rows before export.
 * Let the user check OCR warnings, compare the source text, and fix headings or cells.
 * Send changes back to App so there is one place that holds the draft.
 */

import { useState } from 'react';
import { FlatList, Text, View } from 'react-native';

import type { Draft, DraftRow } from './types';
import { Action, Banner, Field, styles, writing } from './ui';

/** The draft and the editing actions supplied by the scanner. */
type Props = {
  draft: Draft;
  locked: boolean;
  uploaded: boolean;
  updateHeading: (index: number, value: string) => void;
  addColumn: () => void;
  addRow: () => void;
  removeRow: (id: string) => void;
  updateCell: (id: string, index: number, value: string) => void;
  preview?: () => void;
  confirm: () => void;
};

/**
 * Show the extracted rows and warnings next to their text fields.
 * Use the edit callbacks to update the draft in App. Block edits when the draft is locked.
 */
export function Review({ draft, locked, uploaded, updateHeading, addColumn, addRow, removeRow, updateCell, preview, confirm }: Props) {
  const [showRaw, setShowRaw] = useState(false);
  const [showHeadings, setShowHeadings] = useState(false);

  /** Render one row and hide old cell warnings after the user edits a value. */
  function rowEditor(row: DraftRow, index: number) {
    return (
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Record {index + 1}</Text>
        {row.manual && <Text style={styles.caption}>Manually added</Text>}
        {draft.headings.map((heading, column) => {
          const label = heading || `Column ${column + 1}`;
          const alerts = row.warnings.filter(warning => warning.column === column);
          const edited = row.edited.includes(column);
          return (
            <View key={column} style={{ gap: 6 }}>
              <Field label={label} fallback={draft.direction} value={row.cells[column]}
                multiline autoCapitalize="none" editable={!locked} maxLength={3000}
                onChangeText={value => updateCell(row.id, column, value)} />
              {edited && !row.manual && <Text style={styles.caption}>Edited by you</Text>}
              {!edited && alerts.map(warning => <Banner key={warning.code} tone="warning">{warning.message}</Banner>)}
            </View>
          );
        })}
        {row.warnings.filter(warning => warning.column === null).map(warning =>
          <Banner key={warning.code} tone="warning">{warning.message}</Banner>)}
        {!!row.source && <>
          <Text style={styles.caption}>Recognized source</Text>
          <Text selectable style={[styles.body, writing(row.source, draft.direction)]}>{row.source}</Text>
        </>}
        <Action title={`Delete record ${index + 1}`} variant="danger" disabled={locked} onPress={() => removeRow(row.id)} />
      </View>
    );
  }

  return (
    <FlatList data={draft.rows} keyExtractor={row => row.id} renderItem={({ item, index }) => rowEditor(item, index)}
      keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={styles.content}
      ListHeaderComponent={<View style={{ gap: 12 }}>
        <Text style={styles.title}>Review records</Text>
        <Text style={styles.body}>{draft.rows.length} records · {draft.headings.length} columns · {draft.provider}</Text>
        {preview && <Action title="View document" onPress={preview} />}
        {locked && <Banner tone="warning">{uploaded ? 'This version has been confirmed in Google Sheets.' : 'This version is saved for a Sheets upload. Confirm it or clear the saved upload before editing.'}</Banner>}
        {draft.warnings.map((warning, index) => <Banner key={index} tone="warning">{warning.message}</Banner>)}
        {!!draft.raw && <>
          <Action title={showRaw ? 'Hide recognized text' : 'Show recognized text'} onPress={() => setShowRaw(!showRaw)} />
          {showRaw && <Text selectable style={[styles.card, styles.body, writing(draft.raw, draft.direction)]}>{draft.raw}</Text>}
        </>}
        <Action title={showHeadings ? 'Hide column editor' : 'Edit column headings'} onPress={() => setShowHeadings(!showHeadings)} />
        {showHeadings && <View style={styles.card}>
          {draft.headings.map((heading, index) =>
            <Field key={index} label={`Column ${index + 1}`} fallback={draft.direction} value={heading}
              editable={!locked} maxLength={120} onChangeText={value => updateHeading(index, value)} />)}
          <Action title="Add column" disabled={locked || draft.headings.length >= 32} onPress={addColumn} />
        </View>}
      </View>}
      ListFooterComponent={<View style={{ gap: 12 }}>
        <Action title="Add record" onPress={addRow} disabled={locked || draft.rows.length >= 300} />
        <Action title="Confirm reviewed records" variant="primary" onPress={confirm} disabled={!draft.rows.length} />
      </View>} />
  );
}
