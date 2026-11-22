#!/usr/bin/env node
// 视频号 sph 链接 → 元数据 JSON（标题/作者/描述/互动数/封面）。
// 原理：headless Chrome 打开分享页，截获页面自己调用的 get_feed_info 接口。
// 注意：该接口不再返回视频流地址，只给元数据；视频本体需手动投送（见 SKILL.md）。
// 用法: node sph_meta.js <sph链接> [输出.json]
const puppeteer = require('puppeteer-core');
const fs = require('fs');

(async () => {
  const url = process.argv[2];
  if (!url) { console.error('用法: node sph_meta.js <sph链接> [输出.json]'); process.exit(1); }
  const out = process.argv[3];

  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new',
    args: ['--mute-audio'],
  });
  const page = await browser.newPage();
  await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

  let feed = null;
  page.on('response', async (res) => {
    if (res.url().includes('get_feed_info')) {
      try {
        const j = await res.json();
        if (j && j.data && j.data.feedInfo) feed = j;
      } catch (e) {}
    }
  });

  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 4000));
  await browser.close();

  if (!feed) { console.error('没抓到 get_feed_info，链接可能失效或页面结构变了'); process.exit(1); }

  const fi = feed.data.feedInfo, au = feed.data.authorInfo || {};
  const meta = {
    title: (fi.description || '').replace(/#[^ #]+/g, '').trim().slice(0, 60) || '视频号视频',
    author: au.nickname || '',
    description: (fi.description || '').trim(),
    source_url: url,
    platform: '微信视频号',
    published: fi.createtime ? new Date(fi.createtime * 1000).toISOString().slice(0, 10) : '',
    stats: { 收藏: fi.favCountFmt, 点赞: fi.likeCountFmt, 转发: fi.forwardCountFmt, 评论: fi.commentCountFmt },
    cover_url: fi.coverUrl || '',
  };
  const txt = JSON.stringify(meta, null, 2);
  if (out) { fs.writeFileSync(out, txt); console.log('已写入 ' + out); }
  else console.log(txt);
})().catch(e => { console.error(e.message || e); process.exit(1); });
