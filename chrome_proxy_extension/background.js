// Chrome Proxy Extension - Background Script
// Автоматически авторизуется на прокси

const PROXY_HOST = "185.88.101.106";
const PROXY_PORT = "3128";
const PROXY_USER = "subject";
const PROXY_PASS = "XnglxoA4WDUv02wuMksvtA";

// Настройка прокси
chrome.proxy.settings.set({
  value: {
    mode: "fixed_servers",
    rules: {
      singleProxy: {
        scheme: "http",
        host: PROXY_HOST,
        port: parseInt(PROXY_PORT)
      },
      bypassList: ["localhost", "127.0.0.1"]
    }
  },
  scope: "regular"
}, function() {
  console.log("Proxy configured:", PROXY_HOST + ":" + PROXY_PORT);
});

// Обработка авторизации
chrome.webRequest.onAuthRequired.addListener(
  function(details) {
    console.log("Proxy authentication required");
    return {
      authCredentials: {
        username: PROXY_USER,
        password: PROXY_PASS
      }
    };
  },
  { urls: ["<all_urls>"] },
  ["blocking"]
);

console.log("Proxy extension loaded");
