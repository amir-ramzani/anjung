import { useQuery } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { ApiError, api, unwrap, type Schemas } from "../api/client";
import { Button, ErrorBox, Loading, Page } from "../components/ui";
import { formatMoney } from "../lib/money";
import { formatDate } from "../lib/dates";

type Qr = Schemas["QrResult"];

/** Whole seconds left, never negative. Pure so it can be tested without timers. */
export const secondsLeft = (expiresAt: string, now = Date.now()): number => Math.max(Math.ceil((Date.parse(expiresAt) - now) / 1000), 0);

/**
 * The pay screen (plan P3-F2). The provider's QR can be valid for about a minute, so the QR is created when
 * this screen opens (never in a message), counts down, and is replaced as soon as it expires. On a phone a QR
 * cannot be scanned from its own screen, so "Save QR image" offers it for import into a banking app.
 */
export default function Pay() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const invoice = useQuery({
    queryKey: ["invoice", id],
    queryFn: () => unwrap(api.GET("/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id } } })),
    // poll while unpaid: the webhook may land at any moment
    refetchInterval: (q) => (q.state.data && ["paid", "void"].includes(q.state.data.status) ? false : 4000),
  });
  const [qr, setQr] = useState<Qr | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [left, setLeft] = useState(0);
  const [creating, setCreating] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const inflight = useRef(false);

  const status = invoice.data?.status;
  const unpaid = status === "open" || status === "partially_paid" || status === "overdue";

  const create = useCallback(async () => {
    if (inflight.current) return; // never two QR requests at once
    inflight.current = true;
    setCreating(true);
    try {
      const next = await unwrap(api.POST("/v1/invoices/{invoice_id}/qr", { params: { path: { invoice_id: id } } }));
      setQr(next);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      inflight.current = false;
      setCreating(false);
    }
  }, [id]);

  // create on open, and again whenever the outstanding amount changed (a partial payment)
  const outstanding = invoice.data?.outstanding.amount_minor;
  useEffect(() => {
    if (unpaid && (!qr || qr.amount.amount_minor !== outstanding) && !creating && !error) void create();
  }, [unpaid, qr, outstanding, creating, error, create]);

  // countdown, and a new QR the moment it runs out
  useEffect(() => {
    if (!qr) return;
    setLeft(secondsLeft(qr.expires_at));
    const timer = setInterval(() => {
      const s = secondsLeft(qr.expires_at);
      setLeft(s);
      if (s === 0) {
        clearInterval(timer);
        setQr(null); // the effect above asks for a fresh one, without a page reload
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [qr]);

  useEffect(() => {
    if (qr && canvas.current) void QRCode.toCanvas(canvas.current, qr.qr_payload, { width: 280, margin: 2 });
  }, [qr]);

  const save = () => {
    canvas.current?.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `anjung-${invoice.data?.number ?? "invoice"}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  };

  if (invoice.isLoading) return <Loading />;
  if (!invoice.data) return <ErrorBox error={invoice.error} />;
  const inv = invoice.data;

  if (status === "paid") {
    return (
      <Page title={t("pay.title")}>
        <p role="status" className="success big">{t("pay.paid")}</p>
        <Receipt id={inv.id} />
        <p><Link to={`/invoices/${inv.id}`}>{t("pay.backToInvoice")}</Link></p>
      </Page>
    );
  }
  if (!unpaid) return <Page title={t("pay.title")}><p role="alert" className="error">{t("pay.notPayable", { status: t(`status.${status}`) })}</p></Page>;

  const rateLimited = error instanceof ApiError && error.status === 429;
  return (
    <Page title={t("pay.title")}>
      <p className="amount">{formatMoney(inv.outstanding)}</p>
      <p className="muted">{inv.number} · {t("invoices.due")} {formatDate(inv.due_date)}</p>
      {error != null && (
        <>
          <ErrorBox error={error} />
          <Button onClick={() => { setError(null); }}>{rateLimited ? t("pay.tryAgainSoon") : t("pay.retry")}</Button>
        </>
      )}
      <div className="qr">
        <canvas ref={canvas} aria-label={t("pay.qrLabel")} hidden={!qr} />
        {!qr && !error && <Loading />}
      </div>
      {qr && (
        <>
          <p role="timer" aria-live="off" className={left <= 15 ? "warn" : ""}>{t("pay.expiresIn", { seconds: left })}</p>
          <ol className="steps">
            <li>{t("pay.step1")}</li><li>{t("pay.step2")}</li>
          </ol>
          <Button variant="secondary" onClick={save}>{t("pay.saveImage")}</Button>
          <p className="muted">{t("pay.phoneHelp")}</p>
        </>
      )}
      {status === "partially_paid" && <p className="notice">{t("pay.partial", { paid: formatMoney(inv.paid) })}</p>}
    </Page>
  );
}

function Receipt({ id }: { id: string }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const open = async () => {
    setBusy(true);
    try {
      const r = await unwrap(api.GET("/v1/invoices/{invoice_id}/receipt", { params: { path: { invoice_id: id } } }));
      window.open(r.url, "_blank", "noopener");
    } finally {
      setBusy(false);
    }
  };
  return <Button onClick={() => void open()} disabled={busy}>{t("invoices.receipt")}</Button>;
}
