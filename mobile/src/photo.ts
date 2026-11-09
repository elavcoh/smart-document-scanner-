/** Prepare a working photo for OCR while keeping the original. */

import { File } from 'expo-file-system';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import type { ImagePickerAsset } from 'expo-image-picker';

import type { Photo } from './types';

/** Make a JPEG for OCR and keep the original URI for comparison. */
export async function preparePhoto(asset: ImagePickerAsset): Promise<Photo> {
  if ((asset.fileSize ?? 0) > 32 * 1024 * 1024 || asset.width * asset.height > 48_000_000) {
    throw new Error('Choose a photo under 32 MiB and 48 megapixels, or retake it at lower resolution.');
  }
  if (Math.min(asset.width, asset.height) < 120) {
    throw new Error('This photo is too small. Retake it at higher resolution.');
  }
  if (asset.mimeType && !asset.mimeType.startsWith('image/')) {
    throw new Error('Choose a still image.');
  }
  const context = ImageManipulator.manipulate(asset.uri);
  if (Math.max(asset.width, asset.height) > 2600) {
    context.resize(asset.width >= asset.height ? { width: 2600 } : { height: 2600 });
  }
  const output = await (await context.renderAsync()).saveAsync({ compress: 0.94, format: SaveFormat.JPEG });
  if ((new File(output.uri).size ?? 0) > 12 * 1024 * 1024) {
    throw new Error('This photo is still over 12 MiB after compression. Choose a smaller image.');
  }
  return { ...output, originalUri: asset.uri };
}

/** Rotate only the working image by 90 degrees. */
export async function rotatePhoto(photo: Photo): Promise<Photo> {
  const rotated = ImageManipulator.manipulate(photo.uri).rotate(90);
  const output = await (await rotated.renderAsync()).saveAsync({ compress: 0.94, format: SaveFormat.JPEG });
  return { ...output, originalUri: photo.originalUri };
}
