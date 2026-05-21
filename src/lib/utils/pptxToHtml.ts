/**
 * Lightweight PPTX → Image renderer.
 *
 * Extracts text and images from each slide and renders them
 * directly to canvas, returning PNG data URLs.
 *
 * Uses jszip (dynamically imported) and the browser Canvas 2D API.
 * No theme resolution, charts, SmartArt, or animations — preview only.
 */

/**
 * Convert PPTX ArrayBuffer → array of PNG data URL strings, one per slide.
 */
export async function pptxToImages(
	_buffer: ArrayBuffer
): Promise<{ images: string[]; width: number; height: number }> {
	throw new Error('PPTX preview is not available on this platform');
}
