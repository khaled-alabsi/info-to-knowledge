import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { saveRunTags } from "../api";
import { installServer } from "../test/fakeServer";
import { RunTags } from "./RunTags";

const ROUTE = "PUT /api/runs/output/Gateway-review/tags";

// the run page's save: the tags put on the run, a refusal thrown with the command's words
async function saveToRun(tags: string[]) {
  const answer = await saveRunTags("/runs/output/Gateway-review", tags);
  if (answer.exit_code !== 0) throw new Error(answer.output);
}

describe("RunTags", () => {
  it("adds a tag on Enter, several split on commas, takes one off with its button, and saves every list", async () => {
    const calls = installServer({ [ROUTE]: { exit_code: 0, output: "saved" } });
    const onChanged = vi.fn();
    render(<RunTags save={saveToRun} tags={["demo"]} onChanged={onChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "add a tag" }));
    await userEvent.type(screen.getByRole("combobox", { name: "add a tag" }), "client Acme, DEMO, Q3{Enter}");
    expect(calls.at(-1)?.body).toEqual({ tags: ["demo", "client Acme", "Q3"] });
    expect(screen.getByText("client Acme")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "remove tag demo" }));
    expect(calls.at(-1)?.body).toEqual({ tags: ["client Acme", "Q3"] });
    expect(screen.queryByText("demo")).not.toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("shows the server's refusal and saves nothing for an empty box", async () => {
    const calls = installServer({ [ROUTE]: { exit_code: 2, output: "no such run folder" } });
    render(<RunTags save={saveToRun} tags={[]} onChanged={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "add a tag" }));
    await userEvent.type(screen.getByRole("combobox", { name: "add a tag" }), "   {Enter}");
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    await userEvent.type(screen.getByRole("combobox", { name: "add a tag" }), "demo{Enter}");
    expect(await screen.findByText("no such run folder")).toBeInTheDocument();
    // a refused tag is not kept on screen: the run has the tags it had
    expect(screen.queryByRole("button", { name: "remove tag demo" })).not.toBeInTheDocument();
  });

  it("shows a + tag pill that opens the box, which closes back into the pill when left empty", async () => {
    installServer({ [ROUTE]: { exit_code: 0, output: "saved" } });
    render(<RunTags save={saveToRun} tags={["demo"]} onChanged={() => {}} />);
    expect(screen.queryByRole("combobox", { name: "add a tag" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "add a tag" }));
    expect(screen.getByRole("combobox", { name: "add a tag" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("combobox", { name: "add a tag" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "add a tag" })).toHaveTextContent("+ tag");
  });
});
