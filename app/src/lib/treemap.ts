/** Treemap "squarified" (Bruls, Huizing, van Wijk 2000): tiles as close to
 *  square as possible, laid out inside one rectangle. Coordinates are in the
 *  same unit as the box (the dashboard passes 0..100 and uses them as %). */
export interface TreemapInput<T> { item: T; weight: number }
export interface TreemapTile<T> { item: T; weight: number; x: number; y: number; w: number; h: number }

function worst(areas: number[], side: number): number {
  const sum = areas.reduce((total, area) => total + area, 0);
  const max = Math.max(...areas), min = Math.min(...areas);
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

export function squarify<T>(inputs: TreemapInput<T>[], x = 0, y = 0, width = 100, height = 100): TreemapTile<T>[] {
  const valid = inputs.filter(input => Number.isFinite(input.weight) && input.weight > 0)
    .sort((a, b) => b.weight - a.weight);
  const total = valid.reduce((sum, input) => sum + input.weight, 0);
  if (!valid.length || !(total > 0) || !(width > 0) || !(height > 0)) return [];
  const scale = (width * height) / total;
  let rest = valid.map(input => ({ ...input, area: input.weight * scale }));
  const tiles: TreemapTile<T>[] = [];
  while (rest.length) {
    const side = Math.min(width, height);
    const row = [rest[0]];
    let next = 1;
    while (next < rest.length
      && worst([...row, rest[next]].map(r => r.area), side) <= worst(row.map(r => r.area), side)) row.push(rest[next++]);
    rest = rest.slice(next);
    const rowArea = row.reduce((sum, r) => sum + r.area, 0);
    if (width >= height) {
      const columnWidth = rowArea / height;
      let cursor = y;
      for (const r of row) {
        const tileHeight = r.area / columnWidth;
        tiles.push({ item: r.item, weight: r.weight, x, y: cursor, w: columnWidth, h: tileHeight });
        cursor += tileHeight;
      }
      x += columnWidth; width -= columnWidth;
    } else {
      const rowHeight = rowArea / width;
      let cursor = x;
      for (const r of row) {
        const tileWidth = r.area / rowHeight;
        tiles.push({ item: r.item, weight: r.weight, x: cursor, y, w: tileWidth, h: rowHeight });
        cursor += tileWidth;
      }
      y += rowHeight; height -= rowHeight;
    }
  }
  return tiles;
}
