import WorkerLive from "./WorkerLive";

// One phase 7 part's worker, live: its `document` is the part's draft (the owner: "see the part file as it updated in
// live, so i dont need to wait untill the agent finish it all")
export default function PassPartLive({ runBase, number, isRunning, onClose, document, linkFolder }: { runBase: string;
  number: number; isRunning: boolean; onClose: () => void; document?: string; linkFolder?: string }) {
  return (
    <WorkerLive title={`Part ${number}`} label={`part ${number}`} runBase={runBase}
      readPath={`${runBase}/document-pass/parts/${number}`} sendPath={`${runBase}/document-pass/parts/${number}/messages`}
      isRunning={isRunning} canAsk document={document ?? ""} linkFolder={linkFolder} onClose={onClose} />
  );
}
