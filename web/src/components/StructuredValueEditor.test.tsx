import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import StructuredValueEditor from "./StructuredValueEditor";

describe("StructuredValueEditor", () => {
  it("edits a map of values as a table and gives back the whole map, never text to parse", async () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor settingKey="models.frame.sampling" label="sampling" value={{ top_k: 20 }} onChange={onChange} />);
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("spinbutton", { name: "sampling: Top K" }), "5");
    expect(onChange).toHaveBeenLastCalledWith({ top_k: 205 });
    await userEvent.type(screen.getByRole("textbox", { name: "sampling: new name" }), "min_p");
    await userEvent.type(screen.getByRole("textbox", { name: "sampling: new value" }), "0.1");
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    expect(onChange).toHaveBeenLastCalledWith({ top_k: 20, min_p: 0.1 });
    await userEvent.click(screen.getByRole("button", { name: "remove Top K from sampling" }));
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("names each row of a table keyed by settings in plain words, never by its code name defect", () => {
    // A speed preset's table showed "budgets.askWhyAfterRejection" and "review.isOn" as its row names on the phone.
    // Each row keyed by a setting now shows that setting's label, and a sampling row its usual name.
    render(<StructuredValueEditor settingKey="speedPresets" label="quality"
      value={{ "budgets.askWhyAfterRejection": true, "review.isOn": true, presence_penalty: 1 }} onChange={vi.fn()} />);
    const names = screen.getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(names).toEqual(["Ask why after a rejection", "Run the review", "Presence penalty"]);
  });

  it("shows the model servers as a table of address and key variable, and refuses a name a row has", async () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor settingKey="hosts" label="hosts" onChange={onChange}
      value={{ lmstudio: { url: "http://localhost:1234/v1" }, "gpu-box": { url: "http://gpu-box.local:8090/v1", apiKeyEnv: "GPU_KEY" } }} />);
    expect(screen.getByRole("columnheader", { name: "Address" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Token (environment variable name)" })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "hosts: new name" }), "gpu-box");
    expect(screen.getByText("a row has this name")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "remove gpu-box from hosts" }));
    expect(onChange).toHaveBeenLastCalledWith({ lmstudio: { url: "http://localhost:1234/v1" } });
  });

  it("picks a server's jobs by tick boxes with plain names, never a typing box of code words defect", async () => {
    // The jobs column was a typing box holding "chat", and the owner had to guess which words the program accepts.
    // It is now one tick box a job, named in plain words, written back as the stored words.
    const onChange = vi.fn();
    render(<StructuredValueEditor settingKey="hosts" label="hosts" onChange={onChange}
      value={{ lmstudio: { url: "http://localhost:1234/v1", apiKeyEnv: "", kinds: ["chat"] } }} />);
    expect(screen.getByRole("columnheader", { name: "Jobs it can do" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Jobs/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "hosts: lmstudio Jobs it can do: Language and picture models" })).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "hosts: lmstudio Jobs it can do: Speech to text" }));
    expect(onChange).toHaveBeenLastCalledWith({ lmstudio: { url: "http://localhost:1234/v1", apiKeyEnv: "", kinds: ["chat", "speech"] } });
    await userEvent.type(screen.getByRole("textbox", { name: "hosts: new name" }), "gpu-box");
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ "gpu-box": { url: "", apiKeyEnv: "", kinds: [] } }));
  });

  it("shows a list of names as a list of boxes, and the name rules as rows of prefix and tag", async () => {
    const onChange = vi.fn();
    const { unmount } = render(<StructuredValueEditor settingKey="document.textReaderLanguages" label="languages"
      value={["eng", "deu"]} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "remove item 1 from languages" }));
    expect(onChange).toHaveBeenLastCalledWith(["deu"]);
    unmount();
    render(<StructuredValueEditor settingKey="tags.nameRules" label="rules" value={[{ prefix: "weekly", tag: "meeting" }]}
      onChange={onChange} />);
    expect(screen.getByRole("columnheader", { name: "Name starts with" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Tag" })).toBeInTheDocument();
  });
});
