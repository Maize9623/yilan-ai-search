# 一览 AI智搜

本机跨站检索，再收成一份跟搜索目的对齐的判断。

输入一个话题，按你勾的站去搜。国内站用本机专用 Chrome 的真实搜索，国外站走公开检索。收回来的材料交给十一，先读完全部材料，再写成结论、导图和深度阅读。

它不是资讯聚合器，也不是对外的网站。人要判断一件眼前的事，材料散在各站；一览把「自己找材料」和「按题目收口」放在同一块本机看板上。

本仓库已公开，供查看源码。它仍是本机自用工具，不对外提供服务。检索底座遵循 MediaCrawler 的非商业学习许可，不是一份新的开源许可证。

入口：本机 [http://127.0.0.1:8765](http://127.0.0.1:8765)。若从 Aico 项目控制台启动，端口以控制台为准（常见是 `5230`）。

---

## 它做什么

- 一次搜索可勾：小红书、抖音、快手、B站、微博、贴吧、知乎、公众号；国外默认不勾：Google News、Bluesky、Reddit、YouTube。
- 国内站共用一扇专用 Chrome，登录态留在这台电脑。验证码和扫码由人点，程序等。
- 材料会核对地点、节日和明显跑题，并打上时效标。
- 十一按题目写结论，而不是不管搜什么都写成「选 / 避 / 再看」。
- 最近 10 次可回看，也可连本机记录一起删掉。
- 密钥、登录态、搜索记录都不离开这台机器。

更细的产品说明在 [`board/README.md`](board/README.md)。十一的人格在 [`board/eleven.md`](board/eleven.md)。

---

## 本机启动

需要 Python 3.11+、[uv](https://github.com/astral-sh/uv)、本机已安装的 Google Chrome。

```bash
uv sync
cp board/.env.example board/.env
```

在 `board/.env` 或首页「本机已就绪」里填写 DeepSeek key。勾 YouTube 再加 `YOUTUBE_API_KEY`。不要提交 `.env`、`keys.json`、`browser_data/`。

```bash
.venv/bin/python board/server.py
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。第一次搜某个国内站会弹出爬虫 Chrome，在那个窗口登录，回到页面可以点「我登录好了，接着搜」。

也可用 Aico 项目控制台里的「一览 AI智搜」卡片启动。不要和手动 `board/server.py` 同时开两份。

---

## 边界

- 只给自己用，不对外提供服务，不接日常正在用的 Chrome。
- 不自动过验证码，不接代理池，不并行开多个浏览器。
- 不默认抓国外站，不把社交热度写成官方统计。
- 检索底座来自 MediaCrawler，遵守它的非商业学习许可：自用、小规模、先登录再搜。

完整许可见 [`LICENSE`](LICENSE)。上游说明见 [`docs/MediaCrawler.md`](docs/MediaCrawler.md)。

---

## 致谢

一览 AI智搜盖在别人已经做好的工具上面。没有这些项目，这块看板起不来。

### 检索底座

- **[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)**（程序员阿江 / Relakkes）  
  小红书、抖音、快手、B站、微博、贴吧、知乎的登录态检索都建立在这个仓库上。本仓库是在它之上做的本机判断层，不是另起一套爬虫。许可是非商业学习协议，一览遵守同一边界。

### 本机插件与控制台

- **Aico 项目控制台**  
  本机/局域网启动入口。一览 AI智搜作为一张项目卡挂在控制台里，用来一键起本地服务，而不是再记一条命令。
- **信息看板 / feedgrab**  
  公众号搜狗索引、部分 Reddit 公开读取，复用了本机信息看板里已经跑通的公开检索，而不是把另一个仓库嵌进来。

### 浏览器与签名

- **[Playwright](https://playwright.dev/)**  
  专用 Chrome 的启动、登录态和页面读取。
- **[xhshow](https://pypi.org/project/xhshow/)**  
  小红书网页请求签名。没有它，登录后的搜索接口走不通。
- **Google Chrome**  
  实际弹出的是本机安装的 Chrome，不是无头黑盒。

### 判断与页面

- **[DeepSeek](https://www.deepseek.com/)**  
  十一默认用的模型。材料自己找，判断交给模型，两者分开。
- **[FastAPI](https://fastapi.tiangolo.com/) / [Uvicorn](https://www.uvicorn.org/)**  
  本机看板服务。
- **[jieba](https://github.com/fxsjy/jieba)**  
  中文分词，用在检索词压缩和地点核对。

### 写作环境

- **[Cursor](https://cursor.com/)**  
  这块看板是在 Cursor 里写出来的。

若这份产品以后开源，上述致谢会一起保留。有遗漏的插件或依赖，欢迎补进这一节。
