# HTML 报告：交互契约与自查方法

报告是 finverify 唯一的"人机界面"。它要证明的东西很具体：
**每个数字都能被点回原文里的那个格子**，结论不依赖信任，只依赖验证。

## 交互契约

打开 `samples/*.html` 后应当满足三条：

1. **自动聚焦**：页面加载后 400ms，自动定位到第一个被判定有问题的指标
   （`data-bad="1"`），右侧自动跳到对应页并高亮其 `bbox`。
2. **点击定位**：点击左栏任意指标，右侧跳到它所在页，并把该单元格框出来。
3. **唯一高亮**：任何时刻最多一个 `.item.sel`、一个 `.hl.on`，
   点新的会清掉旧的（`clearAll()` 先于 `focusItem` 的选中动作）。

实现只有 20 行 JS，全部在 `src/finverify/web.py` 末尾的 `<script>` 里。
它刻意不依赖任何外部资源——报告是单文件，离线可用。

## 自查方法

在浏览器控制台里执行：

```js
document.querySelector('.item[data-bad="1"]').click()
```

然后应当看到右栏跳转 + 红色高亮框。若无反应，在控制台执行：

```js
const el = document.querySelector('.item');
focusItem(el);                                   // 函数是全局的
console.log(el.classList.contains('sel'));       // 应为 true
console.log(document.getElementById('hl' + el.dataset.page).className);  // 应含 "hl on"
```

## 已知环境限制：嵌入式 webview 不投递事件

**在不投递 DOM 事件的嵌入式浏览器里，报告会"看起来是坏的"，但它本身没问题。**

实测（2026-10，MiniMax Code 右侧 FilePanel 浏览器）：

- 页面渲染正常，`<script>` 同步代码完整执行（可验证 `console.log` 有输出）
- 但 `setTimeout` 回调**不触发**，合成点击的 handler **不触发**，
  `requestAnimationFrame` 驱动的 `scrollTo({behavior:'smooth'})` **不滚动**
- 同一浏览器里打开一个 20 行的最小页面，同样复现 —— 与 finverify 无关

因此"自动聚焦没反应""点了没动静"是**量具坏了，不是产品坏了**。
绕开办法：在浏览器控制台里同步调用 `focusItem(el)`，
此时高亮框会立即正确出现（`.hl.on` + 精确百分比定位）。

排查时踩过的两个坑，一并记下：

1. **`query(kind='dom', selector=...)` 只返回可见元素**。
   用它查 `script` 或未加 `.on` 的 `.hl`（`opacity:0`）一律返回空，
   会误判成"脚本没进 DOM"。判断脚本是否执行要看 console，不是看 DOM 查询。
2. **console 空 ≠ 没报错**，但要确认 console 通道本身是通的——
   先在一个最小页面上验证量具，再回头下结论。
