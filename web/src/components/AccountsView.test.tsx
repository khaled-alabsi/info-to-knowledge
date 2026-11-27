import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { answerJson, installServer } from "../test/fakeServer";
import AccountsView from "./AccountsView";
import ManagerPanel from "./ManagerPanel";

const ADA = { name: "Ada", role: "admin", created: "2026-10-24T10:00:00Z" };
const LEA = { name: "Lea", role: "viewer", created: "2026-10-24T11:00:00Z" };

describe("AccountsView", () => {
  it("lists the accounts, adds one, changes a role, sets a password and removes one", async () => {
    let accounts = [ADA];
    const calls = installServer({
      "GET /api/accounts": () => ({ accounts }),
      "POST /api/accounts": (body) => {
        if (body.name === "Ada") return answerJson({ detail: "an account named 'Ada' exists" }, 400);
        accounts = [ADA, { ...LEA, role: String(body.role) }];
        return { accounts };
      },
      "PUT /api/accounts/Lea": (body) => {
        accounts = [ADA, { ...LEA, role: String(body.role ?? accounts[1].role) }];
        return { accounts };
      },
      "DELETE /api/accounts/Lea": () => { accounts = [ADA]; return { accounts }; },
    });
    const onMessage = vi.fn();
    vi.spyOn(window, "prompt").mockReturnValue("new-pw");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AccountsView onBack={() => {}} onMessage={onMessage} />);
    expect(await screen.findByText("Ada")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("new account's name"), "Ada");
    await userEvent.type(screen.getByLabelText("new account's password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: /Add account/ }));
    expect(onMessage).toHaveBeenCalledWith("an account named 'Ada' exists", true);

    await userEvent.clear(screen.getByLabelText("new account's name"));
    await userEvent.type(screen.getByLabelText("new account's name"), "Lea");
    await userEvent.selectOptions(screen.getByLabelText("new account's role"), "operator");
    await userEvent.click(screen.getByRole("button", { name: /Add account/ }));
    expect(await screen.findByText("Lea")).toBeInTheDocument();
    expect(calls.find((call) => call.method === "POST" && (call.body as { name: string }).name === "Lea")?.body)
      .toEqual({ name: "Lea", password: "pw", role: "operator" });

    await userEvent.selectOptions(screen.getByLabelText("Lea's role"), "viewer");
    expect(calls.at(-1)).toMatchObject({ method: "PUT", url: "/api/accounts/Lea", body: { role: "viewer" } });
    const row = screen.getByText("Lea").closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /Password/ }));
    expect(calls.at(-1)).toMatchObject({ method: "PUT", body: { password: "new-pw" } });
    await userEvent.click(within(row).getByRole("button", { name: /Remove/ }));
    expect(calls.at(-1)).toMatchObject({ method: "DELETE", url: "/api/accounts/Lea" });
    expect(onMessage).toHaveBeenCalledWith("Account Lea removed");
  });

  it("says when no account exists", async () => {
    installServer({ "GET /api/accounts": { accounts: [] } });
    render(<AccountsView onBack={() => {}} onMessage={() => {}} />);
    expect(await screen.findByText(/No account yet/)).toBeInTheDocument();
  });

  it("says when the accounts cannot be read", async () => {
    installServer({ "GET /api/accounts": answerJson({ detail: "sign in first" }, 401) });
    const onMessage = vi.fn();
    render(<AccountsView onBack={() => {}} onMessage={onMessage} />);
    await vi.waitFor(() => expect(onMessage).toHaveBeenCalledWith(expect.stringContaining("Accounts not read"), true));
  });
});

describe("ManagerPanel", () => {
  it("shows the admin tools as a section of the Manager tab, to an admin alone defect", () => {
    // The users, groups and projects had a tab of their own beside the Manager tab; they are now its Admin section,
    // under the run tools, and nobody who is not an admin sees it
    const { rerender } = render(<ManagerPanel openTool={null} onOpenTool={() => {}} isAdminShown />);
    expect(screen.getAllByRole("button").map((button) => button.textContent?.trim()))
      .toEqual(["Download", "Rename", "Compress videos", "Users", "Groups", "Projects"]);
    expect(screen.getByRole("group", { name: "Admin tools" })).toHaveTextContent("Admin");
    rerender(<ManagerPanel openTool={null} onOpenTool={() => {}} />);
    expect(screen.queryByRole("button", { name: /Users/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download/ })).toBeInTheDocument();
  });
});
