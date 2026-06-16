import type { CardNode, ColorCat } from './types';

export const COLOR_HEX: Record<ColorCat, string> = {
  W: '#F0EFE2',
  U: '#1A6FAD',
  B: '#A07850',
  R: '#D3202A',
  G: '#00733E',
  Multi: '#C8A923',
  Colorless: '#9E9E9E',
};

export const COLOR_LABEL: Record<ColorCat, string> = {
  W: 'White',
  U: 'Blue',
  B: 'Black',
  R: 'Red',
  G: 'Green',
  Multi: 'Multicolor',
  Colorless: 'Colorless / Artifact',
};

export const COLOR_ORDER: ColorCat[] = ['W', 'U', 'B', 'R', 'G', 'Multi', 'Colorless'];

// Bitmask per single color; Colorless and Multi have no bits (mask === 0 means colorless).
export const COLOR_BITS: Partial<Record<ColorCat, number>> = {
  W: 1, U: 2, B: 4, R: 8, G: 16,
};

/**
 * Returns true if `node` matches the active color filter.
 *
 * - No selection → always pass.
 * - Colorless: independently matches cards with color_mask === 0.
 * - Colored pips with mode "including": card must contain AT LEAST the selected colors.
 * - Colored pips with mode "exactly": card must contain EXACTLY those colors, no more.
 *
 * Selecting Colorless alongside colored pips uses OR semantics (colorless cards OR
 * cards matching the colored selection), since a card cannot be both.
 */
export function colorFilterPass(
  node: CardNode,
  selectedColors: Set<string>,
  mode: 'including' | 'exactly',
): boolean {
  if (selectedColors.size === 0) return true;

  const mask = node.color_mask ?? 0;

  const hasColorless = selectedColors.has('Colorless');
  const selectedMask = (['W', 'U', 'B', 'R', 'G'] as const)
    .filter(c => selectedColors.has(c))
    .reduce((acc, c) => acc | (COLOR_BITS[c] ?? 0), 0);

  if (hasColorless && mask === 0) return true;

  if (selectedMask > 0) {
    return mode === 'including'
      ? (mask & selectedMask) === selectedMask
      : mask === selectedMask;
  }

  return false;
}
