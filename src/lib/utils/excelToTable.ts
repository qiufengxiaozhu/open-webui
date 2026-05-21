/**
 * Shared Excel → HTML table renderer.
 *
 * Converts a worksheet to a styled HTML table with:
 * - Column letter headers (A, B, C…)
 * - Row numbers
 * - Proper empty cell handling
 * - Sanitized output
 */

export type WorkSheet = Record<string, unknown>;

export interface ExcelTableResult {
	html: string;
	rowCount: number;
	colCount: number;
}

/**
 * Render a worksheet as an HTML table string.
 * Uses sheet_to_json with header:1 for a raw 2D array.
 */
export async function excelToTable(_worksheet: WorkSheet): Promise<ExcelTableResult> {
	return {
		html: '<div class="text-gray-500 italic p-4">Excel preview is not available on this platform.</div>',
		rowCount: 0,
		colCount: 0
	};
}
