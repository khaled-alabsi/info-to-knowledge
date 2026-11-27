import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Toasts from "./Toasts";

const CROSS = "M6 6l12 12M18 6L6 18";
const TRIANGLE = "M12 3l10 18H2zM12 10v5M12 18h.01";

// The shapes drawn in the message, by the path each icon draws.
function listDrawnShapes(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("path")).map((path) => path.getAttribute("d") ?? "");
}

describe("Toasts", () => {
  it("an error shows one cross, its close button, and a warning triangle as its mark defect", () => {
    // An error message showed two crosses: its red mark was the same × as its close button and read as a second
    // close button. Its mark is now the warning triangle, and the only × is the close button.
    const { container } = render(<Toasts toasts={[{ id: 1, text: "the reset was refused", isError: true }]}
      onDismiss={() => undefined} />);
    const shapes = listDrawnShapes(container);
    expect(shapes.filter((shape) => shape === CROSS)).toHaveLength(1);
    expect(shapes).toContain(TRIANGLE);
  });
});
