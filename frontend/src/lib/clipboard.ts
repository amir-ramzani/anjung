/** Copy text to the clipboard. Returns whether it worked.
 *
 * `navigator.clipboard` only exists on secure origins (HTTPS or localhost), so on a plain-HTTP
 * address such as the NAS demo it is undefined. There we fall back to the older
 * select-and-`execCommand("copy")` route, which still works from a click handler. */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the older route (e.g. permission denied)
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.focus();
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}
