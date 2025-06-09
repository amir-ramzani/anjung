export type AuthMode = "mock" | "cognito";

const env = import.meta.env;

export const config = {
  /** Empty means "same origin": the dev server proxies /v1 to the API. */
  apiBaseUrl: (env.VITE_API_BASE_URL as string | undefined) ?? "",
  authMode: ((env.VITE_AUTH_MODE as string | undefined) ?? "mock") as AuthMode,
  /** e.g. ap-southeast-5_AbCdEf123: our own sign-in page talks to this user pool (contract v0.11.0) */
  cognitoPoolId: (env.VITE_COGNITO_POOL_ID as string | undefined) ?? "",
  cognitoClientId: (env.VITE_COGNITO_CLIENT_ID as string | undefined) ?? "",
  /** The app can send WhatsApp messages itself. Off until a WhatsApp provider is integrated (decision D3);
   * until then staff share invitation links from their own WhatsApp. */
  whatsappSending: (env.VITE_WHATSAPP_SENDING as string | undefined) === "true",
};
