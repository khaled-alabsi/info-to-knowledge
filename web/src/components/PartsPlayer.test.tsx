import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PartsPlayer from "./PartsPlayer";

describe("PartsPlayer", () => {
  it("shows a PDF in the browser's viewer, never in a video player", () => {
    const { container } = render(<PartsPlayer parts={[{ name: "AcmeSync-manual.pdf", size_bytes: 2048 }]}
      buildSrc={() => "/api/runs/output/AcmeSync-manual/recording?part=0"} />);
    const viewer = container.querySelector("iframe");
    expect(viewer).toHaveAttribute("src", "/api/runs/output/AcmeSync-manual/recording?part=0");
    expect(viewer).toHaveAttribute("title", "AcmeSync-manual.pdf");
    expect(container.querySelector("video")).toBeNull();
  });

  it("plays a video part in a video player", () => {
    const { container } = render(<PartsPlayer parts={[{ name: "AcmeSync-demo.mp4", size_bytes: 2048 }]}
      buildSrc={() => "/demo.mp4"} />);
    expect(container.querySelector("video")).not.toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
  });
});
