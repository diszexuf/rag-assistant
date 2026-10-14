export type DiffKind = 'same' | 'add' | 'del'

export interface DiffLine {
  kind: DiffKind
  text: string
}

// Построчный diff на наибольшей общей подпоследовательности. Программы — сотни строк,
// поэтому таблицы O(n·m) достаточно.
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n')
  const b = after.split('\n')
  const table: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }

  const result: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      result.push({ kind: 'same', text: a[i] })
      i++
      j++
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      result.push({ kind: 'del', text: a[i++] })
    } else {
      result.push({ kind: 'add', text: b[j++] })
    }
  }
  while (i < a.length) result.push({ kind: 'del', text: a[i++] })
  while (j < b.length) result.push({ kind: 'add', text: b[j++] })
  return result
}

// Оставляет изменённые строки и по `context` неизменённых вокруг них; пропуск помечается kind: 'same', text: '…'.
export function collapseDiff(lines: DiffLine[], context = 2): DiffLine[] {
  const keep = new Array(lines.length).fill(false)
  lines.forEach((line, index) => {
    if (line.kind === 'same') return
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k++) {
      keep[k] = true
    }
  })
  const result: DiffLine[] = []
  let skipped = false
  lines.forEach((line, index) => {
    if (keep[index]) {
      result.push(line)
      skipped = false
    } else if (!skipped) {
      result.push({ kind: 'same', text: '…' })
      skipped = true
    }
  })
  return result
}
