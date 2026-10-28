"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { ProfileFileEntry } from "@/lib/api";

export function MediaPreview({ files, initialIndex, onClose }: {
  files: ProfileFileEntry[];
  initialIndex: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const [failed, setFailed] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const file = files[index];
  // Match the fixed route prefix, not a profile name or nested filename named "file".
  const previewUrl = file.file_url.replace(/^(\/api\/profiles\/[^/]+\/[^/]+\/)file\//, "$1preview/");

  useEffect(() => {
    const dialog = dialogRef.current;
    const opener = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog?.showModal();
    closeRef.current?.focus();
    return () => {
      dialog?.close();
      document.body.style.overflow = overflow;
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  function move(delta: number) {
    setIndex((current) => (current + delta + files.length) % files.length);
    setFailed(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "Tab") {
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], video[controls]',
      ));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    } else if ((event.target as HTMLElement).tagName !== "VIDEO" && files.length > 1) {
      // Do not steal the video player's native seeking/volume shortcuts.
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1);
      }
    }
  }

  return (
    <dialog ref={dialogRef} aria-modal="true" aria-labelledby={titleId}
      className="modal p-2 sm:p-4" onKeyDown={onKeyDown}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="modal-box flex max-h-[95dvh] w-full max-w-5xl flex-col gap-3 overflow-y-auto p-3 sm:p-5">
        <div className="flex items-center justify-between gap-2">
          <h2 id={titleId} className="min-w-0 break-all text-sm font-semibold">{file.filename}</h2>
          <button ref={closeRef} type="button" className="btn btn-sm shrink-0" aria-label="关闭预览" onClick={onClose}>关闭</button>
        </div>
        <div className="flex min-h-0 items-center justify-center bg-neutral text-neutral-content rounded">
          {file.kind === "video" ? (
            <video key={file.path} src={previewUrl}
              controls playsInline tabIndex={0} preload="metadata" aria-label={file.filename}
              className="max-h-[65dvh] w-full object-contain" onError={() => setFailed(true)} />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={file.path} src={previewUrl} alt={file.filename}
              className="max-h-[65dvh] max-w-full object-contain" onError={() => setFailed(true)} />
          )}
        </div>
        {failed && <p role="alert" className="text-warning">无法预览此媒体，可能格式不受浏览器支持。可下载后查看。</p>}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button type="button" className="btn btn-sm" aria-label="上一项" disabled={files.length < 2} onClick={() => move(-1)}>上一项</button>
          <span className="text-sm" aria-live="polite">{index + 1} / {files.length}</span>
          <button type="button" className="btn btn-sm" aria-label="下一项" disabled={files.length < 2} onClick={() => move(1)}>下一项</button>
          <a className="btn btn-primary btn-sm" href={file.file_url} download aria-label={`下载 ${file.filename}`}>下载原文件</a>
        </div>
      </div>
    </dialog>
  );
}
