/** Save a file the browser received (e.g. a CSV report) under the name the server suggested. */
export function filenameFrom(disposition: string | null, fallback: string): string {
  const m = /filename="?([^";]+)"?/i.exec(disposition ?? "");
  return m?.[1] ?? fallback;
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
