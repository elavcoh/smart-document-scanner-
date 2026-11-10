/**
 * Show the CSV and its actions in one place.
 * A new export can be saved or shared here. An old file can be opened to read it.
 * The main app handles the file work when a button is pressed.
 */

import { useState } from 'react';
import { Modal, ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { ReviewedTable } from './types';
import { Action, Banner, styles, type Notice } from './ui';

/** Limit the amount of CSV text shown on the screen. */
const PREVIEW_LIMIT = 32_000;
/** The text to show and the reviewed table when export actions are available. */
export type CsvPreviewData = { text: string; table?: ReviewedTable; saved?: boolean };

/** The preview state and actions supplied by the scanner. */
type Props = {
  preview: CsvPreviewData;
  busy: string;
  notice: Notice | null;
  onClose: () => void;
  onSave: (table: ReviewedTable) => void;
  onShare: (table: ReviewedTable) => void;
  onOpenOnMac: (table: ReviewedTable) => void;
};

/**
 * Show the CSV as text and show the actions for this kind of preview.
 * Use the callbacks from App when the user saves, shares, or closes the preview.
 */
export function CsvPreview({ preview, busy, notice, onClose, onSave, onShare, onOpenOnMac }: Props) {
  const [more, setMore] = useState(false);
  const { table, text, saved } = preview;
  return (
    <Modal visible onRequestClose={onClose} animationType="slide">
      <SafeAreaView style={styles.screen}>
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.title}>{saved ? 'Saved CSV' : 'CSV preview'}</Text>
          {notice && <Banner tone={notice.tone}>{notice.text}</Banner>}
          {table && <View style={styles.card}>
            {!saved && <Action title="Save CSV to device" variant="primary"
              disabled={!!busy} busy={busy === 'Saving CSV'} onPress={() => onSave(table)} />}
            <Action title={more ? 'Fewer options' : 'More options'} disabled={!!busy} onPress={() => setMore(!more)} />
            {more && <>
              <Action title="Share CSV" disabled={!!busy} busy={busy === 'Creating CSV'} onPress={() => onShare(table)} />
              <Action title="Open CSV on Mac" disabled={!!busy} busy={busy === 'Opening CSV on Mac'} onPress={() => onOpenOnMac(table)} />
            </>}
          </View>}
          <Action title="Done" disabled={!!busy} onPress={onClose} />
          {text.length > PREVIEW_LIMIT && <Banner>Showing the first 32,000 characters.</Banner>}
          <ScrollView horizontal style={styles.card}>
            <Text selectable style={[styles.body, { fontFamily: 'monospace' }]}>{text.slice(0, PREVIEW_LIMIT)}</Text>
          </ScrollView>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}
