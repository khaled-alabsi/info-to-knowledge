// Browser parts jsdom does not have, given the behaviour the page relies on, and a fresh fake server per test.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { FakeUpdateSource, FakeXMLHttpRequest, installServer } from "./fakeServer";
import { setUpdateSourceOpener } from "../updateSource";

HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
  this.open = true;
};
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
  this.open = false;
  this.dispatchEvent(new Event("close"));
};
Element.prototype.scrollIntoView = function scrollIntoView() {};
Element.prototype.setPointerCapture = function setPointerCapture() {};
HTMLMediaElement.prototype.play = function play() {
  return Promise.resolve();
};
HTMLMediaElement.prototype.pause = function pause() {};
window.matchMedia = (query: string) => ({
  matches: false, media: query, onchange: null,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
});
window.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
URL.createObjectURL = () => "blob:figure";
URL.revokeObjectURL = () => {};

beforeEach(() => {
  FakeUpdateSource.instances = [];
  FakeXMLHttpRequest.instances = [];
  setUpdateSourceOpener((buildUrl) => new FakeUpdateSource(buildUrl));
  vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
  installServer({});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// The page keeps where it was in a cookie; each test starts with none.
afterEach(() => {
  document.cookie = "i2k-view=; path=/; max-age=0";
});
