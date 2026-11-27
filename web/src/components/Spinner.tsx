// The one loading sign of the page: a mid-size spinning ring, centred in the space it fills. Its words are read by a
// screen reader and by the tests, never shown.
export default function Spinner() {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
