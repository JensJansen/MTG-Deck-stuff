import './FormatSelector.css';

interface Props {
  formats: string[];
  onSelect: (fmt: string) => void;
}

const fmtLabel = (fmt: string) => fmt.charAt(0).toUpperCase() + fmt.slice(1);

export function FormatSelector({ formats, onSelect }: Props) {
  return (
    <div className="format-selector">
      <h1 className="format-selector__title">Deck Analytics</h1>
      <p className="format-selector__subtitle">Select a format to explore card co-occurrence data</p>

      {formats.length === 0 ? (
        <p className="format-selector__empty">
          No data found — run{' '}
          <code className="format-selector__code">
            python src/viz/visualize.py --format pauper
          </code>{' '}
          first.
        </p>
      ) : (
        <div className="format-selector__grid">
          {formats.map(fmt => (
            <button key={fmt} className="format-selector__btn" onClick={() => onSelect(fmt)}>
              {fmtLabel(fmt)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
