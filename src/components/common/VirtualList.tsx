import React from 'react';
import { List } from 'react-window';

interface VirtualListProps<T> {
  data: T[];
  height: number;
  itemHeight: number;
  renderItem: (item: T, index: number, style: React.CSSProperties) => React.ReactNode;
  className?: string;
}

function VirtualList<T>({ data, height, itemHeight, renderItem, className }: VirtualListProps<T>) {
  const rowCount = data.length;
  if (rowCount === 0) return null;

  const Row = (props: { index: number; style: React.CSSProperties; data: T[] }) => {
    const { index, style, data: items } = props;
    return <div style={style}>{renderItem(items[index], index, style)}</div>;
  };

  return (
    <List
      className={className}
      defaultHeight={height}
      rowCount={rowCount}
      rowHeight={itemHeight}
      rowComponent={Row as any}
      rowProps={{ data } as any}
    />
  );
}

export default React.memo(VirtualList);
