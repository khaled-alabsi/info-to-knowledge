import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { installServer } from "../test/fakeServer";
import GroupsView from "./GroupsView";
import ProjectsView from "./ProjectsView";

const DEFAULT = { slug: "default", title: "Default", runs: 0, role: "admin", members: {}, groups: {} };
const RUN = { root: "output", name: "gateway-review", folder: "output/gateway-review", display_name: "Gateway review", project: "default" };

describe("ProjectsView", () => {
  it("adds a project, gives a group a role there and moves a run in", async () => {
    let projects = [DEFAULT];
    let runs = [RUN];
    const calls = installServer({
      "GET /api/projects": () => ({ projects, groups: [{ name: "gateway-team", accounts: ["Jonas"] }] }),
      "GET /api/accounts": { accounts: [{ name: "Jonas", role: "viewer", created: "" }] },
      "GET /api/runs": () => runs,
      "POST /api/projects": () => {
        projects = [DEFAULT, { slug: "acme", title: "Acme", runs: 0, role: "admin", members: {}, groups: {} }];
        return { projects, groups: [{ name: "gateway-team", accounts: ["Jonas"] }] };
      },
      "PUT /api/projects/acme/groups/gateway-team": () => {
        projects = [DEFAULT, { ...projects[1], groups: { "gateway-team": "viewer" } }];
        return { projects, groups: [{ name: "gateway-team", accounts: ["Jonas"] }] };
      },
      "PUT /api/projects/acme/runs/gateway-review": () => {
        runs = [{ ...RUN, project: "acme" }];
        return { projects, groups: [{ name: "gateway-team", accounts: ["Jonas"] }] };
      },
    });
    const onMessage = vi.fn();
    render(<ProjectsView onBack={() => {}} onMessage={onMessage} />);
    await userEvent.type(await screen.findByLabelText("new project's title"), "Acme");
    await userEvent.click(screen.getByRole("button", { name: /Add project/ }));
    const card = await screen.findByRole("region", { name: "project Acme" });
    expect(within(card).getByText("Nobody has a role here yet: only admins see it.")).toBeInTheDocument();
    await userEvent.selectOptions(within(card).getByLabelText("give a role in Acme"), "group:gateway-team");
    expect(await within(card).findByText("gateway-team")).toBeInTheDocument();
    await userEvent.selectOptions(within(card).getByLabelText("move a run into Acme"), "gateway-review");
    expect(await within(card).findByRole("button", { name: /Move to Default/ })).toBeInTheDocument();
    expect(calls.map((call) => `${call.method} ${new URL(call.url, "http://x").pathname}`)).toContain("PUT /api/projects/acme/runs/gateway-review");
  });
});

describe("GroupsView", () => {
  it("adds a group and an account to it", async () => {
    let groups: { name: string; accounts: string[] }[] = [];
    installServer({
      "GET /api/projects": () => ({ projects: [DEFAULT], groups }),
      "GET /api/accounts": { accounts: [{ name: "Jonas", role: "viewer", created: "" }] },
      "POST /api/groups": (body) => { groups = [{ name: String(body.name), accounts: [] }]; return { projects: [DEFAULT], groups }; },
      "PUT /api/groups/gateway-team": (body) => { groups = [{ name: "gateway-team", accounts: body.accounts as string[] }];
        return { projects: [DEFAULT], groups }; },
    });
    render(<GroupsView onBack={() => {}} onMessage={vi.fn()} />);
    await userEvent.type(await screen.findByLabelText("new group's name"), "gateway-team");
    await userEvent.click(screen.getByRole("button", { name: /Add group/ }));
    await userEvent.selectOptions(await screen.findByLabelText("add an account to gateway-team"), "Jonas");
    expect(await screen.findByRole("button", { name: "take Jonas out of gateway-team" })).toBeInTheDocument();
  });
});
