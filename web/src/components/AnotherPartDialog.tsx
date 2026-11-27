import { useEffect, useState } from "react";
import { describeError, fetchJson } from "../api";
import Dialog from "./Dialog";
import Icon from "./Icon";
import RangeEditor, { describeRange } from "./RangeEditor";
import type { RangeValue } from "./RangeEditor";

// What `GET /api/runs/<root>/<name>/input` answers: the run's recording, its length or its PDF's pages, and the part it
// processes now
interface RunInput extends RangeValue {
  recording: string;
  is_audio: boolean;
  duration_s: number;
  page_count: number;
}

// The New run folder menu's Another part: the run's own recording in the Range editor, the part it
// processes now chosen to start from; Process this part copies the run into a new run folder from phase 1 with that
// part and queues it there. The run itself is only read.
export default function AnotherPartDialog({ runBase, runName, onSubmit, onClose }: {
  runBase: string;
  runName: string;
  onSubmit: (value: RangeValue) => void;
  onClose: () => void;
}) {
  const [input, setInput] = useState<RunInput | null>(null);
  const [value, setValue] = useState<RangeValue>({ range_start: "", range_end: "", range_pages: "" });
  const [error, setError] = useState("");
  useEffect(() => {
    fetchJson<RunInput>(`${runBase}/input`).then((answer) => {
      setInput(answer);
      setValue({ range_start: answer.range_start, range_end: answer.range_end, range_pages: answer.range_pages });
    }).catch((reason) => setError(describeError(reason)));
  }, [runBase]);
  const isSame = input !== null && value.range_start === input.range_start && value.range_end === input.range_end
    && value.range_pages === input.range_pages;
  return (
    <Dialog title={`Process another part of ${runName}`} onClose={onClose}>
      {error && <p className="error" role="alert">{error}</p>}
      {!input && !error && <p className="muted">Reading the run's recording…</p>}
      {input && (
        <>
          <p className="muted">
            A new run folder beside this run processes the part you choose, every phase from the start; this run stays as
            it is. Now: {describeRange(input, input.duration_s, input.page_count).toLowerCase()}.
          </p>
          <RangeEditor source={{ base: `${runBase}/preview`, query: "" }} durationS={input.duration_s}
            pageCount={input.page_count} isAudio={input.is_audio} value={value} onChange={setValue} />
          <div className="dialog-actions">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" disabled={isSame} title={isSame ? "choose a part other than this run's" : ""}
              onClick={() => onSubmit(value)}>
              <Icon name="play" size={14} /> Process {describeRange(value, input.duration_s, input.page_count).toLowerCase()}
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}
