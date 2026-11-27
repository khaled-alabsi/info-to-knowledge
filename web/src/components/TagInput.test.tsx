import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { installServer } from "../test/fakeServer";
import { TagInput, forgetKnownTags, listSuggestions, loadKnownTags } from "./TagInput";

const KNOWN = {
  tags: ["AcmeSync", "client Acme", "week 42"],
  profiles: [{ name: "knowledge", aliases: ["reference"] }, { name: "conversation", aliases: ["chat", "discussion"] },
    { name: "language-lesson", aliases: ["learn German"] }],
};

describe("listSuggestions", () => {
  it("puts tags that start with the text first, then those that hold it, letter case ignored", () => {
    expect(listSuggestions(["AcmeSync", "client Acme", "week 42", "chat"], [], "acme")).toEqual(["AcmeSync", "client Acme"]);
  });

  it("leaves out the tags already given, and shows nothing for an empty box", () => {
    expect(listSuggestions(["AcmeSync", "client Acme"], ["acmesync"], "acme")).toEqual(["client Acme"]);
    expect(listSuggestions(["AcmeSync"], [], "  ")).toEqual([]);
  });
});

describe("TagInput", () => {
  beforeEach(() => forgetKnownTags());

  it("asks the server for the known tags once per page, and knows a tag just given without asking again", async () => {
    const calls = installServer({ "GET /api/tags": KNOWN });
    await loadKnownTags();
    render(<TagInput given={[]} onAdd={vi.fn()} />);
    const box = screen.getByRole("combobox", { name: "add a tag" });
    await userEvent.type(box, "Gateway review{Enter}");
    await userEvent.type(box, "gate");
    expect(await screen.findByRole("option", { name: "Gateway review" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "GET" && call.url.endsWith("/tags"))).toHaveLength(1);
  });

  it("suggests the tags in use and the profiles' names and aliases as you type, and Enter takes the one marked", async () => {
    installServer({ "GET /api/tags": KNOWN });
    const onAdd = vi.fn();
    render(<TagInput given={[]} onAdd={onAdd} />);
    const box = screen.getByRole("combobox", { name: "add a tag" });
    await userEvent.type(box, "learn");
    expect(await screen.findByRole("option", { name: "learn German" })).toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onAdd).toHaveBeenLastCalledWith(["learn German"]);
    expect(box).toHaveValue("");
  });

  it("moves through the list with the arrow keys, takes one with Tab, and closes it with Escape", async () => {
    installServer({ "GET /api/tags": KNOWN });
    const onAdd = vi.fn();
    render(<TagInput given={["week 42"]} onAdd={onAdd} />);
    const box = screen.getByRole("combobox", { name: "add a tag" });
    await userEvent.type(box, "c");
    await screen.findByRole("option", { name: "client Acme" });
    expect(screen.queryByRole("option", { name: "week 42" })).not.toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    const marked = screen.getAllByRole("option").find((option) => option.getAttribute("aria-selected") === "true");
    await userEvent.keyboard("{Tab}");
    expect(onAdd).toHaveBeenLastCalledWith([marked?.textContent]);
    await userEvent.type(box, "c");
    await screen.findAllByRole("option");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });

  it("adds what was typed when nothing is marked, several split on commas, and a click takes a suggestion", async () => {
    installServer({ "GET /api/tags": KNOWN });
    const onAdd = vi.fn();
    render(<TagInput given={[]} onAdd={onAdd} />);
    const box = screen.getByRole("combobox", { name: "add a tag" });
    await userEvent.type(box, "sprint 7, Q3{Enter}");
    expect(onAdd).toHaveBeenLastCalledWith(["sprint 7", "Q3"]);
    await userEvent.type(box, "dis");
    await userEvent.click(await screen.findByRole("option", { name: "discussion" }));
    expect(onAdd).toHaveBeenLastCalledWith(["discussion"]);
  });

  it("still takes typed tags when the tags in use cannot be read, and says it has nothing to suggest", async () => {
    installServer({});
    const onAdd = vi.fn();
    render(<TagInput given={[]} onAdd={onAdd} />);
    await userEvent.type(screen.getByRole("combobox", { name: "add a tag" }), "dem");
    expect(await screen.findByText("the tags in use could not be read: no suggestions")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("combobox", { name: "add a tag" }), "o{Enter}");
    expect(onAdd).toHaveBeenLastCalledWith(["demo"]);
  });
});
