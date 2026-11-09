/** Android settings for Expo, including the camera and local dev HTTP option. */

module.exports = {
  name: 'Smart Document Scanner',
  slug: 'smart-document-scanner',
  version: '1.0.0',
  orientation: 'portrait',
  platforms: ['android'],
  android: {
    package: 'com.smartdocumentscanner.app',
    permissions: ['android.permission.CAMERA'],
    allowBackup: false,
    blockedPermissions: ['android.permission.RECORD_AUDIO'],
  },
  plugins: [
    ['expo-image-picker', { microphonePermission: false }],
    ['expo-build-properties', {
      // Allow HTTP only when it is enabled for local Android development.
      android: { usesCleartextTraffic: process.env.SCANNER_ANDROID_DEV_HTTP === '1' },
    }],
  ],
  extra: { supportsRTL: true },
};
