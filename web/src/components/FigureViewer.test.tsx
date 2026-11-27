import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import FigureViewer from "./FigureViewer";
import type { FigureContent } from "./FigureViewer";

const DIAGRAM: FigureContent = { kind: "svg", markup: '<svg width="400" height="200"><g></g></svg>', width: 400, height: 200,
  source: "graph TD; Gateway-->AcmeSync", name: "AcmeSync-diagram-1" };

// The stage measures 1060 x 560 on screen, so Fit puts a 400 x 200 drawing at 250% with 60 px to spare.
function measureStage() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { x: 0, y: 0, left: 0, top: 0, right: 1060, bottom: 560, width: 1060, height: 560, toJSON: () => ({}) });
}

function readScale(): string {
  return document.querySelector(".viewer-scale")!.textContent ?? "";
}

function readTransform(): string {
  return document.querySelector<HTMLElement>(".viewer-layer")!.style.transform;
}

function captureSavedFiles(): { name: string; blob: Blob | MediaSource }[] {
  const saved: { name: string; blob: Blob | MediaSource }[] = [];
  let lastBlob: Blob | MediaSource | null = null;
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    lastBlob = blob;
    return "blob:figure";
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) {
    saved.push({ name: this.download, blob: lastBlob! });
  });
  return saved;
}

describe("FigureViewer", () => {
  beforeEach(measureStage);

  it("fits the drawing on open and zooms about the centre with + - and 0", () => {
    render(<FigureViewer content={DIAGRAM} caption="AcmeSync.md · diagram 1" onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "AcmeSync.md · diagram 1" })).toBeInTheDocument();
    expect(readScale()).toBe("250%");
    expect(readTransform()).toBe("translate(30px, 30px) scale(2.5)");
    fireEvent.keyDown(window, { key: "+" });
    expect(readScale()).toBe("313%");
    fireEvent.keyDown(window, { key: "-" });
    fireEvent.keyDown(window, { key: "-" });
    expect(readScale()).toBe("200%");
    fireEvent.keyDown(window, { key: "=" });
    expect(readScale()).toBe("250%");
    fireEvent.keyDown(window, { key: "-" });
    fireEvent.keyDown(window, { key: "0" });
    expect(readTransform()).toBe("translate(30px, 30px) scale(2.5)");
    expect(document.body).toHaveClass("no-scroll");
  });

  it("zooms with the buttons, never past 1200%, and Fit sees it whole", async () => {
    render(<FigureViewer content={DIAGRAM} caption="diagram" onClose={vi.fn()} />);
    for (let index = 0; index < 12; index++) await userEvent.click(screen.getByRole("button", { name: "zoom in" }));
    expect(readScale()).toBe("1200%");
    await userEvent.click(screen.getByRole("button", { name: "zoom out" }));
    expect(readScale()).toBe("960%");
    await userEvent.click(screen.getByRole("button", { name: "Fit" }));
    expect(readScale()).toBe("250%");
  });

  it("zooms about the pointer on the wheel, faster on a pinch, and never below 5%", () => {
    render(<FigureViewer content={DIAGRAM} caption="diagram" onClose={vi.fn()} />);
    const stage = document.querySelector(".viewer-stage")!;
    const wheel = new WheelEvent("wheel", { deltaY: -100 * Math.log(2) / 0.25, clientX: 30, clientY: 30, cancelable: true });
    act(() => {
      stage.dispatchEvent(wheel);
    });
    expect(wheel.defaultPrevented).toBe(true);
    expect(readScale()).toBe("500%");
    expect(readTransform()).toBe("translate(30px, 30px) scale(5)");
    act(() => {
      stage.dispatchEvent(new WheelEvent("wheel", { deltaY: 1000, ctrlKey: true, clientX: 0, clientY: 0 }));
    });
    expect(readScale()).toBe("5%");
  });

  it("drags to move, and two pointers pinch to zoom", () => {
    render(<FigureViewer content={DIAGRAM} caption="diagram" onClose={vi.fn()} />);
    const stage = document.querySelector(".viewer-stage")!;
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 500, clientY: 500 });
    expect(readTransform()).toBe("translate(30px, 30px) scale(2.5)");
    fireEvent.pointerDown(stage, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 140, clientY: 90 });
    expect(readTransform()).toBe("translate(70px, 20px) scale(2.5)");

    fireEvent.pointerDown(stage, { pointerId: 2, clientX: 240, clientY: 90 });
    fireEvent.pointerMove(stage, { pointerId: 2, clientX: 240, clientY: 90 });
    expect(readScale()).toBe("250%");
    fireEvent.pointerMove(stage, { pointerId: 2, clientX: 340, clientY: 90 });
    expect(readScale()).toBe("500%");
    fireEvent.pointerUp(stage, { pointerId: 2 });
    fireEvent.pointerCancel(stage, { pointerId: 1 });
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 0, clientY: 0 });
    expect(readScale()).toBe("500%");
  });

  it("saves the drawing as SVG, as the .mmd source, and as PNG at twice its size", async () => {
    const saved = captureSavedFiles();
    const context = { fillStyle: "", fillRect: vi.fn(), drawImage: vi.fn() };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done) => done(new Blob(["png"], { type: "image/png" })));
    const loadedImages: HTMLImageElement[] = [];
    vi.spyOn(HTMLImageElement.prototype, "src", "set").mockImplementation(function setSource(this: HTMLImageElement) {
      loadedImages.push(this);
    });
    render(<FigureViewer content={DIAGRAM} caption="diagram" onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "SVG" }));
    await userEvent.click(screen.getByRole("button", { name: ".mmd" }));
    expect(saved.map((file) => file.name)).toEqual(["AcmeSync-diagram-1.svg", "AcmeSync-diagram-1.mmd"]);
    expect(await (saved[0].blob as Blob).text()).toBe(DIAGRAM.markup);
    expect(await (saved[1].blob as Blob).text()).toBe("graph TD; Gateway-->AcmeSync\n");

    await userEvent.click(screen.getByRole("button", { name: "PNG" }));
    act(() => loadedImages.at(-1)!.onload!(new Event("load")));
    expect(context.drawImage).toHaveBeenCalledWith(loadedImages.at(-1), 0, 0, 800, 400);
    expect(saved.at(-1)?.name).toBe("AcmeSync-diagram-1.png");
  });

  it("saves no PNG when the browser gives no drawing surface", async () => {
    const saved = captureSavedFiles();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const loadedImages: HTMLImageElement[] = [];
    vi.spyOn(HTMLImageElement.prototype, "src", "set").mockImplementation(function setSource(this: HTMLImageElement) {
      loadedImages.push(this);
    });
    render(<FigureViewer content={DIAGRAM} caption="diagram" onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "PNG" }));
    act(() => loadedImages.at(-1)!.onload!(new Event("load")));
    expect(saved).toHaveLength(0);
  });

  it("shows an image with Save, takes its size once it loads, and Esc or Close leaves", async () => {
    const onClose = vi.fn();
    render(<FigureViewer content={{ kind: "img", src: "/api/runs/x/files/figure-01.png", name: "figure-01.png" }}
      caption="The Gateway screen" onClose={onClose} />);
    expect(screen.queryByRole("button", { name: "SVG" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Save/ })).toHaveAttribute("href", "/api/runs/x/files/figure-01.png");
    const image = screen.getByAltText("figure-01.png");
    vi.spyOn(image as HTMLImageElement, "naturalWidth", "get").mockReturnValue(1000);
    vi.spyOn(image as HTMLImageElement, "naturalHeight", "get").mockReturnValue(500);
    fireEvent.load(image);
    expect(readScale()).toBe("100%");
    expect(document.querySelector<HTMLElement>(".viewer-layer")!.style.width).toBe("1000px");
    fireEvent.keyDown(window, { key: "Escape" });
    await userEvent.click(screen.getByRole("button", { name: /Close/ }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
