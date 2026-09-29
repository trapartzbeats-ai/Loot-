// Content script - runs on all pages
console.log("[Test Extension] Content script loaded on:", window.location.href);

// Mark that this extension is present
document.documentElement.setAttribute("data-test-extension", "active");
document.documentElement.setAttribute("data-test-extension-id", chrome.runtime.id);
