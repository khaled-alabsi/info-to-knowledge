// Puts a text on the clipboard. A browser offers `navigator.clipboard` only on https or localhost, so the page opened
// at the machine's network address (http://10.0.0…, the phone's way in) gets none; there, or when the clipboard
// refuses, the text is copied through a hidden text box and the browser's own copy command, which works on plain http.
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // refused (the page not focused, no permission): the copy command below may still work
    }
  }
  const box = document.createElement("textarea");
  box.value = text;
  box.setAttribute("readonly", "");
  box.style.position = "fixed";
  box.style.top = "0";
  box.style.left = "0";
  box.style.opacity = "0";
  document.body.appendChild(box);
  box.select();
  const isCopied = document.execCommand("copy");
  box.remove();
  if (!isCopied) throw new Error("the browser refused to copy");
}
