# Universal Link Reader Cloud PoC v0.2.1

**用途：** 將「可取得嘅公開影片 → 真正解碼影格 → 有時間戳 PDF／HTML」放到免費雲端服務，研究 ChatGPT iPhone **一般對話**可否透過原生網頁工具直接讀取影格，毋須 Windows MCP。

**呢個係獨立 PoC，唔會修改你已安裝嘅 Universal Link Reader v0.1.1。**

## 已有能力

- `/healthz`：基本健康檢查。
- `/demo`：本機 FFmpeg 合成嘅 **4 秒測試影片**，經 FFprobe 驗證、FFmpeg 抽圖，再產生 HTML、影格拼圖同 PDF。唔係 Instagram 真實影片。
- `/demo/report.pdf`：帶時間戳 PDF，供 ChatGPT 原生網頁工具嘗試下載／截圖。
- `/demo/contact.jpg`：影格拼圖。
- `/demo/meta.json`：是否經影片驗證、時長、解像度、畫面數等機器可讀證據。
- 可選 `POST /api/jobs`：安全受控嘅公開 MP4 或 Instagram 原貼文實驗性讀取，結果存取地址 `/r/<隨機ID>`，附有對應 PDF／圖片／JSON。**預設停用**。

影片流存在唔等於有聲音轉錄。即使後端解碼成功，仍需 ChatGPT **實際打開影格圖片／PDF 頁面**先可以聲稱睇過畫面。

## 本地執行（Python 3.11+）

```bash
python -m pip install -r requirements-dev.txt
python scripts/build_demo.py
python -m pytest -q
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

需要系統 `ffmpeg` 及 `ffprobe`。瀏覽器打開 `http://127.0.0.1:8000/demo`。

## 部署 Render 免費 Web Service

1. 將此資料夾放入你自己嘅 GitHub 私人 repository（**勿將任何 token 放入 repo**）。
2. 打開 [Render Dashboard](https://dashboard.render.com/) 並登入；連接 GitHub repository。
3. 選 New → Blueprint，選擇 repository，讀取根目錄 `render.yaml`。或 New → Web Service → Docker → Free。
4. **保留** `ULR_ENABLE_REMOTE_JOBS=0` 和 `ULR_ENABLE_INSTAGRAM=0`，先部署示範功能。Render 會自動產生 `ULR_ADMIN_TOKEN`，但喺遙距任務仍停用期間唔會用到；若要啟用正式任務，先喺 Render Dashboard 查看或重新設定私密 token。
5. Render 會提供實際 `https://<服務名稱>.onrender.com` 網址；依次打開 `/healthz`、`/demo` 同 `/demo/report.pdf`。
6. 喺 iPhone 普通 ChatGPT 對話（毋須選插件）貼上 **實際** `/demo/report.pdf` 網址，要求讀取 PDF 頁面與時間戳，並交代是否真的取得頁面截圖。

如果唔打算提供任何對外讀取任務，無需啟用管理 Token。**唔好喺 ChatGPT 公開對話貼出秘密 token。**

Render 免費服務閒置後會休眠，喚醒可能需要一分鐘左右；本機臨時輸出會喺休眠、重啟或部署後失去。示範檔案係 Docker build image 內，部署後仍可重新提供；實際任務結果只可短期存取，唔保證永久保存。免費額度及可能產生嘅費用須按 Render 帳戶設定確認；若要確保零費用，使用時避免綁定付費方案，並設定支出限制。

## 可選：啟用少量公開影片任務（測試用途）

請喺 Render 服務嘅環境變數設：

```
ULR_ENABLE_REMOTE_JOBS=1
ULR_ADMIN_TOKEN=<至少24字元嘅私密隨機字串>
ULR_ENABLE_INSTAGRAM=0
```

先只接受預設 allowlist 中嘅直接 MP4 host：`interactive-examples.mdn.mozilla.net`、`media.w3.org`。必要時用 `ULR_ALLOWED_MP4_HOSTS` 加入**你明確信任**嘅確切 hostname，唔接受任意 host 或 IP。

例如用你自己終端機發送（唔好將真 token 放入對話中）：

```bash
curl -X POST 'https://<your-service>.onrender.com/api/jobs' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <YOUR_SECRET_TOKEN>' \
  --data '{"url":"https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"}'
```

回傳 `result_url` 後，等候任務完成，打開 `/r/<ID>`、`/r/<ID>/report.pdf`、`/r/<ID>/contact.jpg`。

**Instagram 特別注意：** 只喺網站授權／平台政策容許嘅情況下，另外設定 `ULR_ENABLE_INSTAGRAM=1`。會只處理公開原貼文且 ID 完全吻合嘅嵌入 JSON 宣告 MP4；唔登入、唔獲取 cookies、唔用隱藏 API、唔繞過限制。雲端 IP 是否讀到同一 Reel **未經驗證**。

## 設計限制／安全邊界

- 管理 API 預設關閉、需要 Bearer token；唔向 ChatGPT 讀取頁面提供可以啟動新下載嘅 GET endpoint。
- 只接受 HTTPS、確切 host allowlist、拒絕私有／保留 IP DNS；有跳轉亦逐次驗證。但**DNS rebinding** 仍有風險，正式部署應於網絡層阻斷內部 IP outbound。
- 限制最多一個同時任務、每次啟動 12 個任務、影片 40MiB（可配低）、長度 120 秒、最多 8 格影格。
- 已處理結果以不可猜測 URL 提供，**任何知道網址嘅人都可讀到內容**；只適合非敏感公開影片。預設 30 分鐘後自動清理，但 Render 服務中斷會提早遺失。
- 此 PoC 不是通用代理，唔提供任意網站抓取、登入、付費牆或私人媒體存取。
- 無後端觸發權限嘅普通 ChatGPT `web.run`，可以讀現成結果頁，但**未證實能直接建立任務並抽圖**。下一階段需要驗證一般對話如何安全地啟動任務，唔會因為結果頁可讀就宣稱已做到「貼任何連結直接分析」。

## 測試階段界線

1. 離線／本地：合成 MP4 → FFprobe → FFmpeg → JPG／PDF。
2. 部署：Render 真正產生公開 `onrender.com` URL。
3. iPhone：普通 ChatGPT 對話真正用原生網頁工具打開 PDF，再開啟頁面影像。
4. 社交平台：Render 本身能否合法取得指定公開 Instagram Reel 及驗證後生成 PDF。
5. 最終自動化：Plugin 唔經 Windows MCP 點樣授權啟動雲端任務（仍屬待研究）。

每階段需獨立實證，唔可跳步宣稱完成。

## v0.2.1 visibility and cache policy
Only built-in synthetic demo content can be indexed and briefly cached; health is briefly cached but noindex. Private job results, APIs, failed responses and unknown endpoints remain noindex/no-store. The demo now exposes robots.txt. These hints cannot guarantee that ChatGPT native web search can access new Vercel domains. Remote video jobs remain disabled. The GitHub subfolder retains its v0.2.0 name for Vercel Root Directory compatibility.
