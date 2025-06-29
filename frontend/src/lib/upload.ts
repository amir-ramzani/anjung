/** Uploading a file to the presigned S3 POST that the API hands out (LLD 10.1). */
export const ALLOWED_TYPES = ["application/pdf", "image/jpeg", "image/png", "image/webp"] as const;
export const MAX_BYTES = 15 * 1024 * 1024; // enforced by S3 itself through the signed policy, checked here to fail early

export type FileProblem = "empty" | "type" | "size";

export function checkFile(file: { size: number; type: string }): FileProblem | null {
  if (file.size === 0) return "empty";
  if (!(ALLOWED_TYPES as readonly string[]).includes(file.type)) return "type";
  if (file.size > MAX_BYTES) return "size";
  return null;
}

export const formatBytes = (n: number): string =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

export class UploadError extends Error {
  constructor(public status: number, public s3Code: string | null) {
    super(`upload failed (${status}${s3Code ? ` ${s3Code}` : ""})`);
  }
}

const s3Code = (xml: string): string | null => /<Code>([^<]+)<\/Code>/.exec(xml)?.[1] ?? null;

/**
 * POSTs the fields the API returned, then the file as the LAST field (S3 ignores anything after it).
 * XMLHttpRequest rather than fetch because only it reports upload progress.
 */
export function uploadToS3(
  upload: { url: string; fields: Record<string, string> },
  file: Blob,
  onProgress: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(upload.fields)) form.append(k, v);
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", upload.url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? (onProgress(1), resolve()) : reject(new UploadError(xhr.status, s3Code(xhr.responseText))));
    xhr.onerror = () => reject(new UploadError(0, null));
    xhr.send(form);
  });
}
