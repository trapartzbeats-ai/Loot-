document.getElementById("highlight").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      document.querySelectorAll("a").forEach(a => a.style.outline = "2px solid red");
    }
  });
});
