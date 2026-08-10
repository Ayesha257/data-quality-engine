import { useCallback, useRef, useState } from "react";

const ACCEPTED = [".xlsx", ".xls", ".xlsm", ".csv"];

export default function FileDrop({ file, onSelect }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  const validate = (f) => {
    if (!f) return;
    const ok = ACCEPTED.some((ext) => f.name.toLowerCase().endsWith(ext));
    if (!ok) {
      alert(`Only ${ACCEPTED.join(", ")} files are accepted.`);
      return;
    }
    onSelect(f);
  };

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    validate(e.dataTransfer.files?.[0]);
  }, []);

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      className={`relative rounded-xl border-2 border-dashed p-10 text-center cursor-pointer transition-colors
        ${dragging ? "border-teal-500 bg-teal-500/5" : "border-ink-600 hover:border-ink-500 bg-ink-800/40"}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(",")}
        className="hidden"
        onChange={(e) => validate(e.target.files?.[0])}
      />
      {file ? (
        <div className="flex flex-col items-center gap-2">
          <FileIcon />
          <p className="font-mono text-sm text-mist-100">{file.name}</p>
          <p className="text-xs text-mist-400">{(file.size / 1024).toFixed(0)} KB — click to replace</p>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <FileIcon muted />
          <p className="text-sm text-mist-200">
            Drop a file here, or <span className="text-teal-400 font-medium">browse</span>
          </p>
          <p className="text-xs text-mist-400 font-mono">{ACCEPTED.join("  ·  ")} · up to 200MB</p>
        </div>
      )}
    </div>
  );
}

function FileIcon({ muted }) {
  return (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" className={muted ? "text-mist-400" : "text-teal-400"}>
      <path
        d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path d="M14 2v6h6" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
