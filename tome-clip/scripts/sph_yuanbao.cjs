/* 视频号 → 腾讯元宝官方解析 → profile 文件。
 *
 * 用法：node sph_yuanbao.cjs <share_url> <profile_file> <bridge_path> <timeout_sec> <headless 0|1>
 *
 * 首次运行会打开可见 Chrome，请本人扫码登录 yuanbao.tencent.com。
 * 登录态保存在 ~/.cache/tome-clip/yuanbao-profile，后续可加 --headless。
 */
const puppeteer = require("puppeteer-core");
const { pathToFileURL } = require("url");
const os = require("os");
const path = require("path");

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PROFILE_DIR = path.join(os.homedir(), ".cache", "tome-clip", "yuanbao-profile");

async function main() {
  const [shareUrl, profileFile, bridgePath, timeoutRaw, headlessRaw] = process.argv.slice(2);
  if (!shareUrl || !profileFile || !bridgePath) {
    console.error("用法: node sph_yuanbao.cjs <share_url> <profile_file> <bridge_path> <timeout_sec> <headless 0|1>");
    process.exit(1);
  }
  const timeoutSec = Number(timeoutRaw || 300);
  const headless = headlessRaw === "1";
  const bridge = await import(pathToFileURL(bridgePath).href);

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: headless ? "new" : false,
    userDataDir: PROFILE_DIR,
    args: ["--no-first-run", "--no-default-browser-check", "--window-size=1200,900"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1200, height: 900 });
    await page.goto("https://yuanbao.tencent.com/", {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    }).catch(() => {});
    if (!headless) await page.bringToFront();

    const cdp = await page.target().createCDPSession();
    const deadline = Date.now() + timeoutSec * 1000;
    let tick = 0;
    let loggedIn = false;
    while (Date.now() < deadline) {
      const state = await bridge.checkYuanbaoLogin(cdp).catch(() => ({ ready: false, loggedIn: false }));
      if (state.loggedIn) {
        loggedIn = true;
        break;
      }
      if (tick % 10 === 0) {
        console.log(`WAITING_LOGIN ready=${state.ready} elapsed=${tick * 3}s`);
        if (!headless) {
          console.log("请在弹出的 Chrome 窗口里用微信扫码登录腾讯元宝。");
        }
      }
      tick += 1;
      await new Promise((r) => setTimeout(r, 3000));
    }
    if (!loggedIn) {
      console.log("LOGIN_TIMEOUT");
      process.exit(2);
    }
    console.log("LOGGED_IN");
    await bridge.resolveYuanbaoToProfile(cdp, shareUrl, profileFile);
    console.log("RESOLVED");
  } finally {
    await browser.close().catch(() => {});
  }
}

main().catch((err) => {
  console.error("ERROR", err && err.message ? err.message : err);
  process.exit(1);
});
