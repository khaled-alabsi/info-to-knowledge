import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import SearchBox from "./SearchBox";

function Holder() {
  const [value, setValue] = useState("");
  return <SearchBox label="find a run" placeholder="find" value={value} onChange={setValue} />;
}

describe("SearchBox", () => {
  it("shows an X only while there is text, which empties the box and keeps the focus in it", async () => {
    render(<Holder />);
    expect(screen.queryByRole("button", { name: "clear the search" })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("find a run"), "daily");
    await userEvent.click(screen.getByRole("button", { name: "clear the search" }));
    expect(screen.getByLabelText("find a run")).toHaveValue("");
    expect(screen.getByLabelText("find a run")).toHaveFocus();
    expect(screen.queryByRole("button", { name: "clear the search" })).not.toBeInTheDocument();
  });
});
