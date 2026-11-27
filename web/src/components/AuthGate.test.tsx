import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SIGNED_OUT_EVENT } from "../api";
import { answerJson, installServer } from "../test/fakeServer";
import AuthGate from "./AuthGate";

const show = (account: { name: string; role: string } | null, onSignOut: () => void) => (
  <div>
    <span>page for {account ? `${account.name} (${account.role})` : "this machine"}</span>
    <button onClick={onSignOut}>out</button>
  </div>
);

describe("AuthGate", () => {
  it("shows the page at once when the server has no account", async () => {
    installServer({ "GET /api/auth/me": { has_accounts: false, account: null, roles: [] } });
    render(<AuthGate>{show}</AuthGate>);
    expect(await screen.findByText("page for this machine")).toBeInTheDocument();
  });

  it("asks a server with accounts for a sign-in, says a wrong password, then shows the page for the account", async () => {
    let signedIn = false;
    const calls = installServer({
      "GET /api/auth/me": () => ({ has_accounts: true, account: signedIn ? { name: "Jonas", role: "operator" } : null, roles: [] }),
      "POST /api/auth/login": (body) => {
        if (body.password !== "pw") return answerJson({ detail: "wrong name or password" }, 401);
        signedIn = true;
        return { name: "Jonas", role: "operator", token: "t" };
      },
      "POST /api/auth/logout": () => { signedIn = false; return { is_signed_out: true }; },
    });
    render(<AuthGate>{show}</AuthGate>);
    await userEvent.type(await screen.findByLabelText("Name"), "Jonas");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: /Sign in/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong name or password");

    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: /Sign in/ }));
    expect(await screen.findByText("page for Jonas (operator)")).toBeInTheDocument();
    expect(calls.filter((call) => call.url === "/api/auth/login").at(-1)?.body).toEqual({ name: "Jonas", password: "pw" });

    await userEvent.click(screen.getByRole("button", { name: "out" }));
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });

  it("shows the sign-in again when a call answers that the session ended", async () => {
    let isLive = true;
    installServer({ "GET /api/auth/me": () => ({ has_accounts: true, account: isLive ? { name: "Ada", role: "admin" } : null, roles: [] }) });
    render(<AuthGate>{show}</AuthGate>);
    expect(await screen.findByText("page for Ada (admin)")).toBeInTheDocument();
    isLive = false;
    await act(async () => { window.dispatchEvent(new Event(SIGNED_OUT_EVENT)); });
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });

  it("shows the page when the server does not answer, which the page's top bar then says", async () => {
    installServer({ "GET /api/auth/me": answerJson({ detail: "down" }, 500) });
    render(<AuthGate>{show}</AuthGate>);
    expect(await screen.findByText("page for this machine")).toBeInTheDocument();
  });
});

describe("AuthGate and the server's sign-in switch", () => {
  it("shows the page with no sign-in when the server has accounts but sign-in off", async () => {
    installServer({ "GET /api/auth/me": { has_accounts: true, account: null, roles: [], is_sign_in_required: false } });
    render(<AuthGate>{show}</AuthGate>);
    expect(await screen.findByText("page for this machine")).toBeInTheDocument();
  });
});
