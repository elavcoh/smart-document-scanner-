/**
 * Keep the small UI parts used by several scanner screens.
 * Use the same buttons, banners, input fields, colors, and spacing.
 * Text fields can also show Hebrew and English in the right direction.
 */

import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, type TextInputProps, View } from 'react-native';

/** A message and the color style used to display it. */
export type Notice = { tone: 'error' | 'info' | 'success' | 'warning'; text: string };

/** Reuse the same colors across the scanner screens. */
export const colors = {
  background: '#F3F6FA', white: '#FFFFFF', ink: '#18263A', muted: '#495D74',
  border: '#CCD7E4', primary: '#164F88', light: '#E5F0FB', danger: '#A02222',
  caution: '#754800', cautionBg: '#FFF1D7', success: '#165D43', successBg: '#E1F5EB',
};

/** Show a button with a disabled or loading state when needed. */
export function Action({ title, onPress, variant = 'secondary', disabled = false, busy = false }: {
  title: string; onPress: () => void; variant?: 'primary' | 'secondary' | 'danger';
  disabled?: boolean; busy?: boolean;
}) {
  const primary = variant === 'primary';
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: disabled || busy, busy }}
      disabled={disabled || busy}
      onPress={onPress}
      style={({ pressed }) => [styles.button, primary && styles.primary,
        (disabled || busy || pressed) && { opacity: 0.55 }]}
    >
      {busy && <ActivityIndicator color={primary ? colors.white : colors.primary} />}
      <Text style={[styles.buttonLabel, primary && { color: colors.white },
        variant === 'danger' && { color: colors.danger }]}>{title}</Text>
    </Pressable>
  );
}

/** Show a short message with a color that fits its purpose. */
export function Banner({ tone = 'info', children }: { tone?: 'info' | 'warning' | 'error' | 'success'; children: ReactNode }) {
  return <View style={[styles.banner, tone === 'warning' && { backgroundColor: colors.cautionBg },
    tone === 'success' && { backgroundColor: colors.successBg }, tone === 'error' && { backgroundColor: '#FCE7E7' }]}>
    <Text accessibilityLiveRegion="polite" style={[styles.body,
      tone === 'error' && { color: colors.danger }, tone === 'success' && { color: colors.success },
      tone === 'warning' && { color: colors.caution }]}>{children}</Text>
  </View>;
}

/** Set the text direction from the first letter, with a default for empty text. */
export function writing(value: string, fallback: 'ltr' | 'rtl' = 'ltr') {
  const strong = value.match(/\p{L}/u)?.[0];
  const rtl = strong ? /[\u05D0-\u05EA]/u.test(strong) : !/\p{N}/u.test(value) && fallback === 'rtl';
  return { textAlign: rtl ? 'right' : 'left', writingDirection: rtl ? 'rtl' : 'ltr' } as const;
}

/** Show a labeled input with the right direction for its current value. */
export function Field({ label, fallback, accessibilityLabel = label, ...props }: TextInputProps & { label: string; fallback: 'ltr' | 'rtl' }) {
  return <View style={styles.fieldGroup}>
    <Text style={[styles.caption, writing(label, fallback)]}>{label}</Text>
    <TextInput {...props} accessibilityLabel={accessibilityLabel} placeholderTextColor={colors.muted}
      style={[styles.input, writing(props.value ?? '', fallback), props.style]} />
  </View>;
}

/** Reuse layout and text styles across screens. */
export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  toolbar: { paddingHorizontal: 20, paddingVertical: 12, backgroundColor: colors.white,
    borderBottomWidth: 1, borderBottomColor: colors.border, gap: 4 },
  toolbarTitle: { fontSize: 18, fontWeight: '700', color: colors.ink },
  kicker: { color: colors.primary, fontSize: 12, fontWeight: '700', letterSpacing: 1.4 },
  body: { color: colors.muted, fontSize: 15, lineHeight: 22 },
  title: { fontSize: 32, lineHeight: 38, fontWeight: '800', color: colors.ink },
  sectionTitle: { fontSize: 20, fontWeight: '700', color: colors.ink },
  caption: { fontSize: 13, color: colors.muted, fontWeight: '700' },
  content: { padding: 20, paddingBottom: 36, gap: 14, width: '100%', maxWidth: 740, alignSelf: 'center' },
  card: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border, borderRadius: 16, padding: 16, gap: 12 },
  button: { minHeight: 48, padding: 11, backgroundColor: colors.white, borderRadius: 10,
    borderWidth: 1, borderColor: colors.border, justifyContent: 'center', alignItems: 'center',
    flexDirection: 'row', gap: 8 },
  primary: { backgroundColor: colors.primary, borderColor: colors.primary },
  buttonLabel: { fontSize: 15, color: colors.primary, fontWeight: '700', textAlign: 'center' },
  banner: { backgroundColor: colors.light, padding: 12, borderRadius: 10 },
  fieldGroup: { gap: 5 },
  input: { padding: 10, minHeight: 46, borderColor: colors.border, borderWidth: 1,
    borderRadius: 8, color: colors.ink, backgroundColor: colors.white, fontSize: 16 },
  inline: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', alignItems: 'center' },
  photo: { width: '100%', height: 300, backgroundColor: '#DAE3ED', borderRadius: 12 },
});
