import { useEffect, useRef, useState } from "react";
import CopyButton from "./CopyButton";
import Icon from "./Icon";
import { usePersistentState } from "../viewState";

// A log that follows its end while the reader is at the end, and stays put when the reader scrolls up. Its wrap choice
// is kept in the page's cookie for every log, and starts unwrapped.
export default function LogView({ title, text, empty }: { title: string; text: string; empty: string }) {
  const box = useRef<HTMLPreElement>(null);
  const [isFollowing, setIsFollowing] = useState(true);
  const [isWrapped, setIsWrapped] = usePersistentState("logWrapped", false);

  useEffect(() => {
    if (isFollowing && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [text, isFollowing]);

  const onScroll = () => {
    const element = box.current;
    if (element) setIsFollowing(element.scrollHeight - element.scrollTop - element.clientHeight < 40);
  };


  return (
    <div className="card log-card">
      <div className="log-bar">
        <span className="log-title">
          <Icon name="terminal" /> {title}
        </span>
        <div className="log-tools">
          <button className={`btn ghost small${isWrapped ? " active" : ""}`} aria-pressed={isWrapped}
            onClick={() => setIsWrapped(!isWrapped)}>
            {isWrapped && <Icon name="check" size={14} />} Wrap
          </button>
          <CopyButton getText={() => text} isDisabled={!text} />
          <button className={`btn ghost small${isFollowing ? " active" : ""}`} aria-pressed={isFollowing}
            onClick={() => setIsFollowing(true)}>
            {isFollowing && <Icon name="check" size={14} />} Follow
          </button>
        </div>
      </div>
      <pre className={`log${isWrapped ? " wrap" : ""}`} ref={box} onScroll={onScroll}>
        {text || <span className="log-empty">{empty}</span>}
      </pre>
    </div>
  );
}
