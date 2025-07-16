import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, unwrap, type Schemas } from "../api/client";
import { STAFF, useAuth } from "../auth/AuthContext";
import { config } from "../config";
import { usePaged } from "../lib/hooks";
import { UploadError, checkFile, formatBytes, uploadToS3 } from "../lib/upload";
import { Badge, Button, Empty, ErrorBox, Field, Loading, LoadMore, Select, Table, dateOnly } from "./ui";

type Doc = Schemas["Document"];
type OwnerType = Doc["owner_type"];
type DocType = Doc["type"];

const SCAN_POLL_MS = 2000;
const SCAN_POLL_MAX = 45; // about 90 seconds, then we stop waiting and let the list show the real status
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Types a tenant may attach (the API enforces it too, this only hides what would be refused). */
export const TENANT_DOC_TYPES: DocType[] = ["identity", "other", "maintenance_photo"];

interface Props {
  ownerType: OwnerType;
  ownerId: string;
  /** which document types the uploader offers */
  types: DocType[];
  /** hide the uploader and show the list only */
  readOnly?: boolean;
  /** a heading other than "Documents", e.g. "Documents for unit A-1" */
  title?: string;
}

/** Documents of one owner: an uploader and the list, so every screen that has documents looks the same. */
export function DocumentsSection({ ownerType, ownerId, types, readOnly, title }: Props) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby={`docs-${ownerType}`}>
      <h2 id={`docs-${ownerType}`}>{title ?? t("docs.title")}</h2>
      {!readOnly && <Uploader ownerType={ownerType} ownerId={ownerId} types={types} />}
      <DocumentList ownerType={ownerType} ownerId={ownerId} />
    </section>
  );
}

type Phase = { name: "idle" } | { name: "uploading"; fraction: number } | { name: "scanning" } | { name: "done"; status: Doc["status"] };

/** `onClean` runs once the uploaded file has passed the malware scan (the logo uses it). */
export function Uploader({ ownerType, ownerId, types, onClean }: Omit<Props, "readOnly" | "title"> & { onClean?: (documentId: string) => void }) {
  const { t } = useTranslation();
  const { me } = useAuth();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [type, setType] = useState<DocType>(types[0] ?? "other");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const busy = phase.name === "uploading" || phase.name === "scanning";
  const [dragging, setDragging] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  // a file dropped anywhere on the form goes into the file field, as if it had been chosen there
  const drop = (e: DragEvent<HTMLFormElement>) => {
    e.preventDefault();
    setDragging(false);
    const files = e.dataTransfer.files;
    if (busy || !files.length || !input.current) return;
    const one = new DataTransfer();
    one.items.add(files[0]!);
    input.current.files = one.files;
    setChosen(files[0]!.name);
    setProblem(files.length > 1 ? t("docs.oneAtATime") : null);
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    const file = input.current?.files?.[0];
    setError(null);
    if (!file) return setProblem(t("docs.chooseFile"));
    const bad = checkFile(file);
    if (bad) return setProblem(t(`docs.problem.${bad}`)); // fail before sending anything
    setProblem(null);
    try {
      setPhase({ name: "uploading", fraction: 0 });
      const slot = await unwrap(api.POST("/v1/documents/upload-url", { body: {
        owner_type: ownerType, owner_id: ownerId, type, filename: file.name,
        content_type: file.type as "application/pdf", size_bytes: file.size,
      } }));
      await uploadToS3(slot.upload, file, (fraction) => setPhase({ name: "uploading", fraction }));
      setPhase({ name: "scanning" });
      if (config.authMode === "mock" && me) {
        // locally there is no S3 event or scanner: this dev-only call plays both
        await fetch(`${config.apiBaseUrl}/dev/documents/${slot.document_id}/scan`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ org_id: me.org_id, verdict: "clean" }),
        });
      }
      const status = await waitForScan(slot.document_id, () => void qc.invalidateQueries({ queryKey: ["documents", ownerType, ownerId] }));
      setPhase({ name: "done", status });
      if (status === "clean") onClean?.(slot.document_id);
      if (input.current) input.current.value = "";
      setChosen(null);
    } catch (err) {
      setPhase({ name: "idle" });
      setError(err instanceof UploadError ? new Error(uploadMessage(err, t)) : err);
    } finally {
      await qc.invalidateQueries({ queryKey: ["documents", ownerType, ownerId] });
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className={`card dropzone${dragging ? " dragging" : ""}`}
      onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false); }}
      onDrop={drop}>
      {error != null && (error instanceof ApiError ? <ErrorBox error={error} /> : <div role="alert" className="error">{(error as Error).message}</div>)}
      <Field label={t("docs.type")}>
        <Select value={type} onChange={(e) => setType(e.target.value as DocType)} disabled={busy}>
          {types.map((x) => <option key={x} value={x}>{t(`docs.types.${x}`)}</option>)}
        </Select>
      </Field>
      <Field label={t("docs.file")} error={problem ?? undefined}>
        <input ref={input} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp" disabled={busy}
          onChange={(e) => { setProblem(null); setChosen(e.target.files?.[0]?.name ?? null); }} />
      </Field>
      <small className="muted">{dragging ? t("docs.dropHere") : chosen ? t("docs.chosen", { name: chosen }) : t("docs.dragHint")} {t("docs.limits")}</small>
      {phase.name === "uploading" && <progress aria-label={t("docs.uploading")} max={1} value={phase.fraction} />}
      {phase.name === "scanning" && <p role="status">{t("docs.scanning")}</p>}
      {phase.name === "done" && (
        <p role="status" className={phase.status === "clean" ? "success" : phase.status === "infected" ? "error-text" : "muted"}>{t(`docs.result.${phase.status}`)}</p>
      )}
      <Button type="submit" disabled={busy}>{t("docs.upload")}</Button>
    </form>
  );
}

/** Polls until the scan has finished (clean, infected or failed), or gives up quietly. */
async function waitForScan(id: string, tick: () => void): Promise<Doc["status"]> {
  let last: Doc["status"] = "scanning";
  for (let i = 0; i < SCAN_POLL_MAX; i++) {
    const d = await unwrap(api.GET("/v1/documents/{document_id}", { params: { path: { document_id: id } } }));
    last = d.status;
    if (last === "clean" || last === "infected" || last === "failed") break;
    tick();
    await sleep(SCAN_POLL_MS);
  }
  return last;
}

function uploadMessage(e: UploadError, t: (k: string) => string): string {
  if (e.s3Code === "EntityTooLarge") return t("docs.problem.size");
  if (e.s3Code === "EntityTooSmall") return t("docs.problem.empty");
  if (e.status === 403) return t("docs.problem.expired"); // the signed form is valid for ten minutes
  if (e.status === 0) return t("docs.problem.network");
  return t("docs.problem.failed");
}

export function DocumentList({ ownerType, ownerId }: { ownerType: OwnerType; ownerId: string }) {
  const { t } = useTranslation();
  const { has } = useAuth();
  const list = usePaged<Doc>(["documents", ownerType, ownerId], (cursor) =>
    unwrap(api.GET("/v1/documents", { params: { query: { owner_type: ownerType, owner_id: ownerId, limit: 25, cursor } } })));
  if (list.isLoading) return <Loading />;
  return (
    <>
      <ErrorBox error={list.error} />
      {list.items.length === 0 ? <Empty>{t("docs.none")}</Empty> : (
        <Table rows={list.items} rowKey={(d) => d.id} columns={[
          { header: t("docs.name"), cell: (d) => d.filename },
          { header: t("docs.type"), cell: (d) => t(`docs.types.${d.type}`) },
          { header: t("docs.size"), cell: (d) => formatBytes(d.size_bytes) },
          { header: t("common.status"), cell: (d) => <Badge value={d.status} /> },
          { header: t("docs.added"), cell: (d) => dateOnly(d.created_at) },
          { header: "", cell: (d) => <DocActions doc={d} ownerType={ownerType} ownerId={ownerId} staff={has(...STAFF)} /> },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </>
  );
}

function DocActions({ doc, ownerType, ownerId, staff }: { doc: Doc; ownerType: OwnerType; ownerId: string; staff: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function download() {
    setMessage(null);
    setBusy(true);
    try {
      const r = await unwrap(api.GET("/v1/documents/{document_id}/download-url", { params: { path: { document_id: doc.id } } }));
      window.open(r.url, "_blank", "noopener");
    } catch (e) {
      // 409 document_not_clean: still being scanned, or blocked. Say which instead of a generic error.
      setMessage(e instanceof ApiError && e.code === "document_not_clean" ? t(doc.status === "infected" ? "docs.blocked" : "docs.notReady") : t("common.unexpected"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(t("docs.confirmDelete"))) return;
    setMessage(null);
    setBusy(true);
    try {
      await unwrap(api.DELETE("/v1/documents/{document_id}", { params: { path: { document_id: doc.id } } }));
      await qc.invalidateQueries({ queryKey: ["documents", ownerType, ownerId] });
    } catch (e) {
      setMessage(e instanceof ApiError && e.status === 409 ? t("docs.held") : t("common.unexpected")); // retention hold
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="secondary" disabled={busy || doc.status === "infected"} onClick={() => void download()}>{t("docs.download")}</Button>
      {staff && <Button variant="danger" disabled={busy} onClick={() => void remove()}>{t("common.remove")}</Button>}
      {message && <small role="alert" className="error-text"> {message}</small>}
    </>
  );
}
