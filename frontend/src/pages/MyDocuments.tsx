import { useTranslation } from "react-i18next";

import { useAuth } from "../auth/AuthContext";
import { DocumentsSection } from "../components/Documents";
import { Page } from "../components/ui";

/** A tenant's own documents: identity papers and anything else they want the landlord to have. */
export default function MyDocuments() {
  const { t } = useTranslation();
  const { me } = useAuth();
  if (!me?.tenant_id) return null;
  return (
    <Page title={t("docs.mine")}>
      <p className="muted">{t("docs.mineHelp")}</p>
      <DocumentsSection ownerType="tenant" ownerId={me.tenant_id} types={["identity", "other"]} />
    </Page>
  );
}
