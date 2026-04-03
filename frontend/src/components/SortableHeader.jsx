/**
 * Заголовок сортируемой таблицы с подсветкой и стрелкой.
 * Общий компонент, заменяет 3+ дубликата.
 */
export function SortableHeader({ col, sortKey, sortDir, onSort }) {
  const isActive = sortKey === col.key;
  return (
    <th
      onClick={() => onSort(col.key)}
      className={`th-sortable cursor-pointer select-none whitespace-nowrap px-3 py-2 ${col.align} ${isActive ? 'text-accent' : ''}`}
    >
      {col.label}
      {isActive && <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>}
    </th>
  );
}
