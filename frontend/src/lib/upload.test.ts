import { vi } from "vitest";

import { MAX_BYTES, UploadError, checkFile, formatBytes, uploadToS3 } from "./upload";

class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  status = 204;
  responseText = "";
  method = "";
  url = "";
  sent: FormData | null = null;
  constructor() { FakeXhr.last = this; }
  open(method: string, url: string) { this.method = method; this.url = url; }
  send(body: FormData) { this.sent = body; }
}

afterEach(() => vi.unstubAllGlobals());

describe("checkFile", () => {
  it("accepts the four allowed types up to 15 MiB", () => {
    for (const type of ["application/pdf", "image/jpeg", "image/png", "image/webp"]) expect(checkFile({ size: 1000, type })).toBeNull();
    expect(checkFile({ size: MAX_BYTES, type: "application/pdf" })).toBeNull();
  });
  it("refuses empty, oversize and other types before anything is sent", () => {
    expect(checkFile({ size: 0, type: "application/pdf" })).toBe("empty");
    expect(checkFile({ size: MAX_BYTES + 1, type: "application/pdf" })).toBe("size");
    for (const type of ["text/html", "application/x-msdownload", "image/svg+xml", "image/gif", ""]) expect(checkFile({ size: 10, type })).toBe("type");
  });
  it("formats sizes for people", () => {
    expect([formatBytes(900), formatBytes(2048), formatBytes(5 * 1024 * 1024)]).toEqual(["900 B", "2 KB", "5.0 MB"]);
  });
});

describe("uploadToS3", () => {
  it("posts the signed fields first and the file LAST, and reports progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const seen: number[] = [];
    const done = uploadToS3({ url: "http://s3/bucket", fields: { key: "k", policy: "p", "Content-Type": "application/pdf" } }, new Blob(["x"]), (f) => seen.push(f));
    const x = FakeXhr.last;
    expect([x.method, x.url]).toEqual(["POST", "http://s3/bucket"]);
    expect([...x.sent!.keys()]).toEqual(["key", "policy", "Content-Type", "file"]); // S3 ignores fields after the file
    x.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 });
    x.onload?.();
    await done;
    expect(seen).toEqual([0.25, 1]);
  });
  it("turns S3's refusal into a typed error with the S3 code", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const p = uploadToS3({ url: "u", fields: {} }, new Blob(["x"]), () => undefined);
    FakeXhr.last.status = 400;
    FakeXhr.last.responseText = "<Error><Code>EntityTooLarge</Code></Error>";
    FakeXhr.last.onload?.();
    await expect(p).rejects.toMatchObject({ status: 400, s3Code: "EntityTooLarge" });
    const q = uploadToS3({ url: "u", fields: {} }, new Blob(["x"]), () => undefined);
    FakeXhr.last.onerror?.();
    await expect(q).rejects.toBeInstanceOf(UploadError);
    await expect(q).rejects.toMatchObject({ status: 0 });
  });
});
