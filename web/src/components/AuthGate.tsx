import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { SIGNED_OUT_EVENT, fetchAuth, signOut } from "../api";
import type { Account, AuthState } from "../api";
import SignIn from "./SignIn";

// What the page shows first: the sign-in when the server has accounts and nobody is signed
// in, else the page, handed the account (none when the server has no account) and a way to sign out. A 401 from any
// call shows the sign-in again. A server that does not answer shows the page, which says so in its top bar.
export default function AuthGate({ children }: { children: (account: Account | null, onSignOut: () => void) => ReactNode }) {
  const [state, setState] = useState<AuthState | null>(null);

  const check = useCallback(async () => {
    try {
      setState(await fetchAuth());
    } catch {
      setState({ has_accounts: false, account: null, roles: [] });
    }
  }, []);

  useEffect(() => {
    void check();
    const onSignedOut = () => void check();
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, [check]);

  if (state === null) return null;
  // The server says whether it asks anyone to sign in (chosen when it starts); an older server only had accounts
  if ((state.is_sign_in_required ?? state.has_accounts) && !state.account) {
    return <SignIn onSignedIn={(account) => setState({ ...state, account })} />;
  }

  return <>{children(state.account, () => void signOut().finally(() => void check()))}</>;
}
