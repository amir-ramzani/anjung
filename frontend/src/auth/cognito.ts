/**
 * Our own sign-in page against Cognito (decision B3, contract v0.11.0), so each company can have a branded one.
 *
 * The password check uses SRP through AWS's `amazon-cognito-identity-js`: the password never leaves the browser in
 * clear and never reaches our API. Cognito still does everything else (lockout, throttling, MFA, password rules,
 * the forgot-password code). Tokens are kept the way they always were (sessionStorage, cleared with the tab); the
 * library's own storage is an in-memory stub so nothing is written to localStorage.
 */
import {
  AuthenticationDetails, CognitoUser, CognitoUserPool, type CognitoUserSession, type ICognitoStorage,
} from "amazon-cognito-identity-js";

import { config } from "../config";
import type { Tokens } from "./tokens";

export type Step = { kind: "done"; tokens: Tokens } | { kind: "new_password" } | { kind: "mfa" };

const memory: ICognitoStorage = (() => {
  const m = new Map<string, string>();
  return {
    setItem: (k: string, v: string) => void m.set(k, v),
    getItem: (k: string) => m.get(k) ?? null,
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
  };
})();

const pool = () => new CognitoUserPool({ UserPoolId: config.cognitoPoolId, ClientId: config.cognitoClientId, Storage: memory });
const userFor = (email: string) => new CognitoUser({ Username: email.trim().toLowerCase(), Pool: pool(), Storage: memory });
let pending: CognitoUser | null = null; // the sign-in waiting for a new password or an MFA code

const tokensOf = (s: CognitoUserSession): Tokens => ({
  accessToken: s.getAccessToken().getJwtToken(),
  refreshToken: s.getRefreshToken().getToken(),
  expiresAt: s.getAccessToken().getExpiration() * 1000,
});

function steps(resolve: (s: Step) => void, reject: (e: unknown) => void) {
  return {
    onSuccess: (s: CognitoUserSession) => { pending = null; resolve({ kind: "done", tokens: tokensOf(s) }); },
    onFailure: (e: unknown) => reject(e),
    newPasswordRequired: () => resolve({ kind: "new_password" }), // invited by email with a temporary password
    totpRequired: () => resolve({ kind: "mfa" }),
    mfaRequired: () => resolve({ kind: "mfa" }),
  };
}

export function signIn(email: string, password: string): Promise<Step> {
  const user = userFor(email);
  user.setAuthenticationFlowType("USER_SRP_AUTH");
  pending = user;
  return new Promise((resolve, reject) =>
    user.authenticateUser(new AuthenticationDetails({ Username: email.trim().toLowerCase(), Password: password }), steps(resolve, reject)));
}

export function setNewPassword(password: string): Promise<Step> {
  const user = pending;
  if (!user) return Promise.reject(new Error("no sign-in in progress"));
  return new Promise((resolve, reject) => user.completeNewPasswordChallenge(password, {}, steps(resolve, reject)));
}

export function sendMfaCode(code: string): Promise<Step> {
  const user = pending;
  if (!user) return Promise.reject(new Error("no sign-in in progress"));
  return new Promise((resolve, reject) => user.sendMFACode(code.trim(), steps(resolve, reject), "SOFTWARE_TOKEN_MFA"));
}

/** Cognito emails a code. It answers the same whether or not the email has an account. */
export function forgotPassword(email: string): Promise<void> {
  return new Promise((resolve, reject) =>
    userFor(email).forgotPassword({ onSuccess: () => resolve(), onFailure: reject, inputVerificationCode: () => resolve() }));
}

export function confirmNewPassword(email: string, code: string, password: string): Promise<void> {
  return new Promise((resolve, reject) =>
    userFor(email).confirmPassword(code.trim(), password, { onSuccess: () => resolve(), onFailure: reject }));
}

const region = () => config.cognitoPoolId.split("_")[0];

async function cognitoCall<T>(target: string, body: object): Promise<T> {
  const r = await fetch(`https://cognito-idp.${region()}.amazonaws.com/`, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${target} ${r.status}`);
  return (await r.json()) as T;
}

/** A fresh access token from the refresh token (no password involved). */
export async function refresh(refreshToken: string): Promise<Tokens> {
  const j = await cognitoCall<{ AuthenticationResult: { AccessToken: string; ExpiresIn: number; RefreshToken?: string } }>("InitiateAuth", {
    AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: config.cognitoClientId, AuthParameters: { REFRESH_TOKEN: refreshToken },
  });
  const a = j.AuthenticationResult;
  return { accessToken: a.AccessToken, refreshToken: a.RefreshToken ?? refreshToken, expiresAt: Date.now() + a.ExpiresIn * 1000 };
}

/** Best effort on sign-out: the refresh token stops working everywhere. */
export async function revoke(refreshToken: string): Promise<void> {
  try {
    await cognitoCall("RevokeToken", { Token: refreshToken, ClientId: config.cognitoClientId });
  } catch {
    /* signed out locally either way */
  }
}

/** i18n key for a Cognito error (`login.errors.*`). Never says whether an email has an account. */
export function errorKey(e: unknown): string {
  const code = (e as { code?: string; name?: string } | null)?.code ?? (e as { name?: string } | null)?.name ?? "";
  switch (code) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return "login.errors.wrong";
    case "PasswordResetRequiredException":
      return "login.errors.resetRequired";
    case "CodeMismatchException":
    case "ExpiredCodeException":
      return "login.errors.code";
    case "InvalidPasswordException":
      return "login.errors.weak";
    case "LimitExceededException":
    case "TooManyRequestsException":
    case "TooManyFailedAttemptsException":
      return "login.errors.tooMany";
    default:
      return "login.failed";
  }
}
