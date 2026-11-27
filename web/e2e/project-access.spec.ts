// Accounts and projects: the sign-in when the server asks for it, what a viewer is not shown, the Admin tab's Users,
// Groups and Projects, and the top bar's project menu that narrows the inbox and the run list to one project.
import { expect, test } from "./fixtures/mockApi";
import type { ApiMock } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import * as data from "./fixtures/data";
import type { Account, AccountRow, ProjectsState, RunSummary } from "../src/api";

const JONAS_ADMIN: Account = { name: "Jonas", role: "admin" };

function signedInAs(account: Account) {
  return { has_accounts: true, account, roles: ["viewer", "operator", "admin"], is_sign_in_required: true };
}

// A server that asks for a sign-in: Jonas with the password "acme-pass" signs in, anything else is refused
function answerSignIn(api: ApiMock) {
  let account: Account | null = null;
  api.answer("GET /auth/me", () => ({ json: { ...signedInAs(JONAS_ADMIN), account } }));
  api.answer("POST /auth/login", (request) => {
    const { name, password } = request.body as { name: string; password: string };
    if (name !== "Jonas" || password !== "acme-pass") return { status: 401, json: { detail: "wrong name or password" } };
    account = JONAS_ADMIN;
    return { json: account };
  });
  api.answer("POST /auth/logout", () => {
    account = null;
    return { json: { is_signed_out: true } };
  });
}

async function openAdminTool(page: Page, tool: "Users" | "Groups" | "Projects") {
  // the admin tools are the Manager tab's Admin section
  await page.getByRole("tab", { name: "Manager" }).click();
  await page.getByRole("group", { name: "Admin tools" }).getByRole("button", { name: tool }).click();
  await expect(page.getByRole("heading", { name: tool, level: 2 })).toBeVisible();
}

test("sign-in on: a wrong password is refused in the server's words, the right one opens the page, Sign out leaves it",
  async ({ page, api }) => {
    answerSignIn(api);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: /Sign in/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Inbox/ })).toHaveCount(0);

    await page.getByLabel("Name").fill("Jonas");
    await page.getByLabel("Password").fill("wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toHaveText("wrong name or password");

    await page.getByLabel("Password").fill("acme-pass");
    await page.getByRole("button", { name: "Sign in" }).click();
    const topBar = page.getByRole("banner");
    await expect(topBar.getByText("Jonas · admin")).toBeVisible();
    await expect(page.getByRole("tab", { name: /Inbox/ })).toBeVisible();
    expect(api.sentTo("POST /auth/login").at(-1)?.body).toEqual({ name: "Jonas", password: "acme-pass" });

    await topBar.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: /Sign in/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Inbox/ })).toHaveCount(0);
    expect(api.sentTo("POST /auth/logout")).toHaveLength(1);
  });

test("sign-in off: a server with accounts opens the page with no sign-in", async ({ page, api }) => {
  api.answer("GET /auth/me", { json: { ...signedInAs(JONAS_ADMIN), account: null, is_sign_in_required: false } });
  await page.goto("/");

  await expect(page.getByRole("tab", { name: /Inbox/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Sign in/ })).toHaveCount(0);
  // nobody is signed in, so the page is this machine's own: the Manager's Admin section and the settings are there
  await page.getByRole("tab", { name: "Manager" }).click();
  await expect(page.getByRole("group", { name: "Admin tools" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
});

test("a viewer is shown no Admin section and no settings button", async ({ page, api }) => {
  api.answer("GET /auth/me", { json: signedInAs({ name: "Mira", role: "viewer" }) });
  await page.goto("/");

  await expect(page.getByRole("banner").getByText("Mira · viewer")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Manager" })).toBeVisible();
  await page.getByRole("tab", { name: "Manager" }).click();
  await expect(page.getByRole("group", { name: "Admin tools" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toHaveCount(0);
});

// The server's accounts, groups, projects and runs as one admin's changes leave them, starting with `groups`
function answerAdmin(api: ApiMock, groups: ProjectsState["groups"] = []) {
  let accounts: AccountRow[] = [...data.ACCOUNT_ROWS];
  let state: ProjectsState = { ...structuredClone(data.PROJECTS_DEFAULT_ONLY), groups };
  let runs: RunSummary[] = data.RUNS.map((run) => ({ ...run, project: "default" }));
  const projects = () => ({ json: state });

  api.answer("GET /auth/me", { json: signedInAs(JONAS_ADMIN) });
  api.answer("GET /accounts", () => ({ json: { accounts } }));
  api.answer("POST /accounts", (request) => {
    const { name, role } = request.body as { name: string; role: string };
    accounts = [...accounts, { name, role, created: "2026-10-25T09:00:00" }];
    return { json: { accounts } };
  });
  api.answer("GET /projects", projects);
  api.answer("GET /runs", () => ({ json: runs }));
  api.answer("POST /groups", (request) => {
    state = { ...state, groups: [...state.groups, { name: (request.body as { name: string }).name, accounts: [] }] };
    return projects();
  });
  api.answer("PUT /groups/reviewers", (request) => {
    const members = (request.body as { accounts: string[] }).accounts;
    state = { ...state, groups: state.groups.map((group) => (group.name === "reviewers" ? { ...group, accounts: members } : group)) };
    return projects();
  });
  api.answer("POST /projects", (request) => {
    const title = (request.body as { title: string }).title;
    state = { ...state, projects: [...state.projects, { slug: "project-y", title, runs: 0, role: null, members: {}, groups: {} }] };
    return projects();
  });
  api.answer("PUT /projects/project-y/groups/reviewers", (request) => {
    const role = (request.body as { role: string }).role;
    state = { ...state, projects: state.projects.map((project) => (project.slug === "project-y"
      ? { ...project, groups: { ...project.groups, reviewers: role } } : project)) };
    return projects();
  });
  api.answer("PUT /projects/project-y/runs/gateway-review", () => {
    runs = runs.map((run) => (run.name === "gateway-review" ? { ...run, project: "project-y" } : run));
    return projects();
  });
  api.answer("DELETE /projects/project-y", () => {
    state = { ...state, projects: state.projects.filter((project) => project.slug !== "project-y") };
    runs = runs.map((run) => ({ ...run, project: "default" }));
    return projects();
  });
}

test("an admin's Users lists the accounts and adds one", async ({ page, api }) => {
  answerAdmin(api);
  await page.goto("/");
  await openAdminTool(page, "Users");

  await expect(page.getByRole("combobox", { name: "Jonas's role" })).toHaveValue("admin");
  await page.getByLabel("new account's name").fill("Mira");
  await page.getByLabel("new account's password").fill("acme-pass");
  await page.getByLabel("new account's role").selectOption("operator");
  await page.getByRole("button", { name: "Add account" }).click();

  await expect(page.getByText("Account Mira added as operator")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Mira's role" })).toHaveValue("operator");
  expect(api.sentTo("POST /accounts")[0].body).toEqual({ name: "Mira", password: "acme-pass", role: "operator" });
});

test("an admin's Groups adds a group and an account to it", async ({ page, api }) => {
  answerAdmin(api);
  await page.goto("/");
  await openAdminTool(page, "Groups");

  await expect(page.getByText("No group yet.")).toBeVisible();
  await page.getByLabel("new group's name").fill("reviewers");
  await page.getByRole("button", { name: "Add group" }).click();
  await expect(page.getByText("Group reviewers added")).toBeVisible();

  await page.getByRole("combobox", { name: "add an account to reviewers" }).selectOption("Jonas");
  await expect(page.getByText("Jonas joined reviewers")).toBeVisible();
  await expect(page.getByLabel("reviewers's accounts")).toContainText("Jonas");
  expect(api.sentTo("POST /groups")[0].body).toEqual({ name: "reviewers" });
  expect(api.sentTo("PUT /groups/reviewers")[0].body).toEqual({ accounts: ["Jonas"] });
});

test("an admin's Projects adds a project, gives a group a role, moves a run in and removes the project",
  async ({ page, api }) => {
    // the group exists already: the Groups test adds one
    answerAdmin(api, [{ name: "reviewers", accounts: ["Jonas"] }]);
    await page.goto("/");
    await openAdminTool(page, "Projects");

    await page.getByLabel("new project's title").fill("Project Y");
    await page.getByRole("button", { name: "Add project" }).click();
    await expect(page.getByText("Project Project Y added")).toBeVisible();
    const card = page.getByRole("region", { name: "project Project Y" });
    await expect(card.getByText("Nobody has a role here yet: only admins see it.")).toBeVisible();

    await card.getByRole("combobox", { name: "give a role in Project Y" }).selectOption("group:reviewers");
    await expect(card.getByRole("combobox", { name: "reviewers's role in Project Y" })).toHaveValue("viewer");
    expect(api.sentTo("PUT /projects/project-y/groups/reviewers")[0].body).toEqual({ role: "viewer" });

    await card.getByRole("combobox", { name: "move a run into Project Y" }).selectOption("gateway-review");
    await expect(page.getByText("gateway-review moved into Project Y")).toBeVisible();
    await expect(card.getByRole("button", { name: "Move to Default" })).toBeVisible();
    await expect(card.getByText("1 run(s)")).toBeVisible();

    page.once("dialog", (dialog) => {
      expect(dialog.message()).toBe("Remove the project Project Y? Its runs go back to Default.");
      void dialog.accept();
    });
    await card.getByRole("button", { name: "Remove", exact: true }).click();
    await expect(page.getByText("Project Project Y removed")).toBeVisible();
    await expect(page.getByRole("region", { name: "project Project Y" })).toHaveCount(0);
    expect(api.sentTo("DELETE /projects/project-y")).toHaveLength(1);
  });

test("the project menu narrows the inbox and the runs to one project, and All projects shows every one again",
  async ({ page, api }) => {
    api.answer("GET /projects", { json: data.PROJECTS_TWO });
    api.answer("GET /runs", { json: data.RUNS.map((run) => ({ ...run,
      project: run.name === "gateway-review" ? "project-c" : "default" })) });
    await page.goto("/");

    const menuButton = page.getByRole("banner").getByRole("button", { name: "All projects" });
    await expect(page.getByText("AcmeSync kickoff.mp4")).toBeVisible();
    await expect(page.getByText("Jonas onboarding.m4a")).toBeVisible();

    await menuButton.click();
    await page.getByRole("menuitemradio", { name: "Project C" }).click();
    await expect(page.getByRole("banner").getByRole("button", { name: "Project C" })).toBeVisible();
    // only gateway-review is in Project C: the new video (never run, so in Default) and Jonas's run are gone
    await expect(page.getByText("AcmeSync kickoff.mp4")).toHaveCount(0);
    await expect(page.getByText("Jonas onboarding.m4a")).toHaveCount(0);
    await page.getByRole("button", { name: /^Done\s*1/ }).click();
    await expect(page.getByText("Gateway review.mp4")).toBeVisible();
    await expect(page.getByRole("button", { name: /acmesync-retro/ })).toHaveCount(0);

    await page.getByRole("banner").getByRole("button", { name: "Project C" }).click();
    await expect(page.getByRole("menuitemradio", { name: "Project C" })).toHaveAttribute("aria-checked", "true");
    await page.getByRole("menuitemradio", { name: "All projects" }).click();
    await expect(page.getByText("AcmeSync kickoff.mp4")).toBeVisible();
    await expect(page.getByText("Jonas onboarding.m4a")).toBeVisible();
    await expect(page.getByRole("button", { name: /acmesync-retro/ })).toBeVisible();
  });

test("with one project the top bar has no project menu", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("tab", { name: /Inbox/ })).toBeVisible();
  await expect(page.getByRole("banner").getByRole("button", { name: "All projects" })).toHaveCount(0);
  await expect(page.getByRole("menuitemradio")).toHaveCount(0);
});
