// Stock Clerk storefront: search, recommendations, cart, and support chat.

const EXAMPLE_SEARCHES = [
  "small L-shaped metal tool for furniture bolts",
  "red rubber thing on a stick for a clogged toilet",
  "blue tape that peels off without ruining paint",
  "little cone-shaped caps you twist onto wires",
  "something to stop my chair scratching the floor",
  "long thin grater for lemon peel",
];
const CHAT_SUGGESTIONS = [
  "What's your return policy?",
  "How long do refunds take?",
  "Can I return opened paint?",
  "What do I need to hang a heavy mirror?",
];

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => `$${Number(n).toFixed(2)}`;

const state = {
  products: [],
  byId: new Map(),
  category: "All",
  searchResults: null, // null = browsing the catalog
  shoppers: [],
  shopperId: "",
  cart: loadCart(), // Map of product id -> quantity
  guestId: loadGuestId(), // identifies this browser's purchase history
  checkout: { stripe: false, free_shipping_over: 35, standard_shipping: 5.99 },
  chat: [], // [{role, content}]
  chatBusy: false,
};

async function api(path, body) {
  const response = await fetch(path, body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : undefined);
  if (!response.ok) {
    const error = new Error(`${path} failed (${response.status})`);
    if (response.status === 429) { // rate limited: the server explains when to retry
      error.friendly = (await response.json().catch(() => ({}))).detail;
    }
    throw error;
  }
  return response.json();
}

const activeShopperId = () => state.shopperId || state.guestId;

function loadGuestId() {
  const fresh = "g-" + Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) => b.toString(16).padStart(2, "0")).join("");
  try {
    const saved = localStorage.getItem("stockclerk.guest");
    if (saved && /^g-[a-f0-9]{12}$/.test(saved)) return saved;
    localStorage.setItem("stockclerk.guest", fresh);
  } catch { /* storage unavailable: history lasts for this page only */ }
  return fresh;
}

let noticeTimer;
function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 6000);
}

// ---------- cart ----------

function loadCart() {
  try {
    return new Map(JSON.parse(localStorage.getItem("stockclerk.cart") || "[]"));
  } catch {
    return new Map();
  }
}

function saveCart() {
  try {
    localStorage.setItem("stockclerk.cart", JSON.stringify([...state.cart]));
  } catch { /* storage unavailable: cart just won't persist */ }
}

function changeCart(id, delta) {
  const quantity = (state.cart.get(id) || 0) + delta;
  if (quantity > 0) state.cart.set(id, quantity);
  else state.cart.delete(id);
  saveCart();
  renderCartCount();
  renderCart();
  loadRecommendations();
}

function renderCartCount() {
  $("cart-count").textContent = [...state.cart.values()].reduce((a, b) => a + b, 0);
}

async function renderCart() {
  const lines = [...state.cart].filter(([id]) => state.byId.has(id));
  if (!lines.length) {
    $("cart-items").innerHTML = `<p class="muted">Your cart is empty.</p>`;
    $("cart-recs").innerHTML = "";
    return;
  }
  let total = 0;
  const linesHtml = lines.map(([id, quantity]) => {
    const p = state.byId.get(id);
    total += p.price * quantity;
    return `<div class="cart-line">
      <span class="rec-emoji">${p.emoji}</span>
      <span class="grow"><strong>${escapeHtml(p.name)}</strong><br><span class="muted">${money(p.price)}</span></span>
      <span class="qty">
        <button type="button" data-qty="-1" data-id="${p.id}" aria-label="Remove one ${escapeHtml(p.name)}">−</button>
        <span>${quantity}</span>
        <button type="button" data-qty="1" data-id="${p.id}" aria-label="Add one ${escapeHtml(p.name)}">+</button>
      </span>
    </div>`;
  }).join("");
  const shipping = total >= state.checkout.free_shipping_over ? 0 : state.checkout.standard_shipping;
  $("cart-items").innerHTML = `${linesHtml}
    ${sumRows(total, shipping)}
    <button class="btn btn-primary btn-block" type="button" data-checkout>Checkout · ${money(total + shipping)}</button>
    <p class="cart-note muted">${state.checkout.stripe
      ? "You'll pay securely on Stripe. This store is in test mode: use card 4242 4242 4242 4242, any future date, any CVC."
      : "Stripe isn't configured, so this checkout is simulated and no payment is taken."}</p>`;

  try {
    const data = await api("/api/recommendations", { cart: lines.map(([id]) => id) });
    $("cart-recs").innerHTML = data.recommendations.length
      ? `<h3>Goes well with your cart</h3><div class="rec-row">${data.recommendations.slice(0, 4).map(recCard).join("")}</div>`
      : "";
  } catch {
    $("cart-recs").innerHTML = "";
  }
}

function sumRows(subtotal, shipping) {
  return `<div class="cart-sum"><span>Subtotal</span><span>${money(subtotal)}</span></div>
    <div class="cart-sum"><span>Shipping</span><span>${shipping ? money(shipping) : "Free"}</span></div>
    <div class="cart-total"><span>Total</span><span>${money(subtotal + shipping)}</span></div>`;
}

// ---------- checkout (Stripe) ----------

async function startCheckout(button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Starting checkout…";
  try {
    const data = await api("/api/checkout", { cart: Object.fromEntries(state.cart), shopper_id: activeShopperId() });
    window.location.assign(data.url); // Stripe's payment page (or straight back here when simulated)
  } catch (error) {
    button.disabled = false;
    button.textContent = label;
    notice(error.friendly || "Checkout couldn't be started. Please try again.");
  }
}

// Runs when Stripe sends the shopper back to the store.
async function handleCheckoutReturn() {
  const params = new URLSearchParams(location.search);
  const outcome = params.get("checkout");
  if (!outcome) return;
  history.replaceState(null, "", location.pathname);
  if (outcome !== "success") return notice("Checkout cancelled. Your cart is saved.");
  try {
    const order = await api(`/api/checkout/confirm?session_id=${encodeURIComponent(params.get("session_id") || "")}`);
    if (order.status !== "paid") return notice("Your payment hasn't completed yet. Your cart is saved.");
    state.cart.clear();
    saveCart();
    renderCartCount();
    showOrder(order);
    loadRecommendations();
  } catch {
    notice("We couldn't confirm that order. If you were charged, please contact support.");
  }
}

function showOrder(order) {
  $("order-detail").innerHTML = `
    <div class="order-check" aria-hidden="true">✓</div>
    <h2 id="order-heading">Thanks, your order is confirmed</h2>
    <p class="muted">Order ${escapeHtml(order.order_id.slice(-10).toUpperCase())} · ${order.mode === "stripe"
      ? "Paid with Stripe (test mode)" : "Simulated payment (Stripe isn't configured)"}</p>
    <div class="order-lines">${order.items.map(({ product: p, quantity }) => `<div class="cart-line">
      <span class="rec-emoji" aria-hidden="true">${p.emoji}</span>
      <span class="grow"><strong>${escapeHtml(p.name)}</strong><br><span class="muted">${quantity} × ${money(p.price)}</span></span>
      <span class="price">${money(p.price * quantity)}</span>
    </div>`).join("")}</div>
    ${sumRows(order.subtotal, order.shipping)}
    <p class="cart-note muted">These items are now in your purchase history, so your recommendations reflect this order.</p>
    <button class="btn btn-primary btn-block" type="button" data-close>Keep shopping</button>`;
  $("order-dialog").showModal();
}

// ---------- product cards ----------

function productCard(p, badge) {
  return `<article class="card">
    ${badge ? `<span class="badge">${badge}</span>` : ""}
    <button class="card-open" type="button" data-open="${p.id}" aria-label="View ${escapeHtml(p.name)}">
      <span class="thumb" aria-hidden="true">${p.emoji}</span>
      <span class="card-body">
        <span class="card-cat">${escapeHtml(p.category)}</span>
        <span class="card-name" style="display:block">${escapeHtml(p.name)}</span>
        <span class="card-desc">${escapeHtml(p.description)}</span>
      </span>
    </button>
    <div class="card-foot">
      <span class="price">${money(p.price)}</span>
      <button class="btn btn-ghost btn-small" type="button" data-add="${p.id}">Add to cart</button>
    </div>
  </article>`;
}

function recCard(p) {
  const why = p.because ? `Bought with ${escapeHtml(p.because.name)}` : "Popular with shoppers";
  return `<button class="rec" type="button" data-open="${p.id}">
    <span class="rec-emoji" aria-hidden="true">${p.emoji}</span>
    <span><span class="rec-name">${escapeHtml(p.name)}</span><span class="rec-why">${why} · ${money(p.price)}</span></span>
  </button>`;
}

function renderGrid() {
  const searching = state.searchResults !== null;
  $("categories").hidden = searching;
  $("clear-search").hidden = !searching;
  if (searching) {
    $("grid").innerHTML = state.searchResults.length
      ? state.searchResults.map((p, i) => productCard(p, i === 0 ? "Best match" : "")).join("")
      : `<div class="empty">Nothing in the store matches that description. Try describing its shape, color, or what you'd use it for — or ask Clerk in the chat.</div>`;
    return;
  }
  const list = state.products.filter((p) => state.category === "All" || p.category === state.category);
  $("grid").innerHTML = list.map((p) => productCard(p)).join("");
}

function renderCategories(categories) {
  $("categories").innerHTML = ["All", ...categories].map((c) =>
    `<button class="chip" type="button" data-category="${escapeHtml(c)}" aria-pressed="${c === state.category}">${escapeHtml(c)}</button>`
  ).join("");
}

// ---------- semantic search ----------

async function runSearch(query) {
  query = query.trim();
  if (!query) return clearSearch();
  $("search-input").value = query;
  $("results-title").textContent = `Searching for “${query}”…`;
  $("results-mode").hidden = true;
  $("categories").hidden = true;
  $("grid").innerHTML = `<div class="skeleton"></div>`.repeat(4);
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    if ($("search-input").value.trim() !== query) return; // a newer search replaced this one
    state.searchResults = data.results;
    const n = data.results.length;
    $("results-title").textContent = `${n} ${n === 1 ? "match" : "matches"} for “${query}”`;
    $("results-mode").textContent = data.mode === "semantic" ? "✨ AI semantic search" : "Keyword search (AI unavailable)";
    $("results-mode").hidden = false;
  } catch (error) {
    state.searchResults = [];
    $("results-title").textContent = error.friendly || "Search is unavailable right now";
  }
  renderGrid();
  $("results-title").parentElement.scrollIntoView({ behavior: "smooth", block: "start" });
}

function clearSearch() {
  state.searchResults = null;
  $("search-input").value = "";
  $("results-title").textContent = "All products";
  $("results-mode").hidden = true;
  renderGrid();
}

// ---------- recommendations (item-based collaborative filtering) ----------

async function loadRecommendations() {
  try {
    const data = await api("/api/recommendations", { shopper_id: activeShopperId(), cart: [...state.cart.keys()] });
    const shopper = state.shoppers.find((s) => s.id === state.shopperId);
    const names = data.history.map((p) => p.name);
    const bought = names.length > 4 ? `${names.slice(0, 4).join(", ")} and ${names.length - 4} more` : names.join(", ");
    $("recs-title").textContent = data.personalized ? "Recommended for you" : "Popular right now";
    $("recs-subtitle").textContent = names.length
      ? `Based on ${shopper ? `${shopper.label}'s` : "your"} past purchases (${bought})${state.cart.size ? " and your cart" : ""}.`
      : data.personalized
        ? "Based on what's in your cart and what other shoppers bought with it."
        : "Add to your cart, place an order, or pick a sample shopper above to see personalised picks.";
    $("recs").innerHTML = data.recommendations.map(recCard).join("");
    $("recs-section").hidden = !data.recommendations.length;
  } catch {
    $("recs-section").hidden = true;
  }
}

// ---------- product details ----------

async function openProduct(id) {
  const p = state.byId.get(id);
  if (!p) return;
  const detail = (alsoBought) => `
    <div class="detail-top">
      <span class="thumb" aria-hidden="true">${p.emoji}</span>
      <div>
        <span class="card-cat">${escapeHtml(p.category)}</span>
        <h2 id="product-name">${escapeHtml(p.name)}</h2>
        <p>${escapeHtml(p.description)}</p>
        <div class="detail-buy">
          <span class="price">${money(p.price)}</span>
          <button class="btn btn-primary" type="button" data-add="${p.id}">Add to cart</button>
        </div>
      </div>
    </div>
    ${alsoBought && alsoBought.length ? `<h3>Customers who bought this also bought</h3><div class="rec-row">${alsoBought.map((r) => recCard({ ...r, because: null }).replace("Popular with shoppers · ", "")).join("")}</div>` : ""}`;
  $("product-detail").innerHTML = detail();
  if ($("cart-dialog").open) $("cart-dialog").close();
  if (!$("product-dialog").open) $("product-dialog").showModal();
  try {
    const data = await api(`/api/products/${encodeURIComponent(id)}`);
    if ($("product-dialog").open && $("product-name")?.textContent === p.name) {
      $("product-detail").innerHTML = detail(data.also_bought);
    }
  } catch { /* details still show without recommendations */ }
}

// ---------- support chat ----------

function addMessage(role, text, products = []) {
  const log = $("chat-log");
  const bubble = document.createElement("div");
  bubble.className = `msg ${role === "user" ? "user" : "bot"}`;
  bubble.textContent = text;
  log.append(bubble);
  if (products.length) {
    const row = document.createElement("div");
    row.className = "msg-products";
    row.innerHTML = products.map((p) =>
      `<button class="chip" type="button" data-open="${p.id}">${p.emoji} ${escapeHtml(p.name)} · ${money(p.price)}</button>`).join("");
    log.append(row);
  }
  log.scrollTop = log.scrollHeight;
  return bubble;
}

async function sendChat(text) {
  text = text.trim();
  if (!text || state.chatBusy) return;
  state.chatBusy = true;
  $("chat-input").value = "";
  $("chat-suggestions").hidden = true;
  state.chat.push({ role: "user", content: text });
  addMessage("user", text);
  const typing = addMessage("assistant", "Clerk is typing…");
  typing.classList.add("typing");
  try {
    const data = await api("/api/chat", { messages: state.chat.slice(-12) });
    typing.remove();
    state.chat.push({ role: "assistant", content: data.reply });
    addMessage("assistant", data.reply, data.products || []);
  } catch (error) {
    typing.remove();
    state.chat.pop();
    addMessage("assistant", error.friendly || "Sorry, I couldn't reach the support service. Please try again in a moment.");
  }
  state.chatBusy = false;
  $("chat-input").focus();
}

function toggleChat(open) {
  $("chat-panel").hidden = !open;
  $("chat-toggle").hidden = open;
  $("chat-toggle").setAttribute("aria-expanded", String(open));
  if (open) {
    if (!$("chat-log").children.length) {
      addMessage("assistant", "Hi, I'm Clerk! Ask me about returns, refunds, shipping, or any product — or describe something you're trying to find.");
    }
    $("chat-input").focus();
  }
}

// ---------- events ----------

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-open], [data-add], [data-qty], [data-category], [data-example], [data-ask], [data-checkout], [data-close]");
  if (!target) return;
  const d = target.dataset;
  if (d.add) {
    changeCart(d.add, 1);
    const label = target.textContent;
    target.textContent = "Added ✓";
    setTimeout(() => { target.textContent = label; }, 1100);
  } else if (d.open) openProduct(d.open);
  else if (d.qty) changeCart(d.id, Number(d.qty));
  else if (d.category) {
    state.category = d.category;
    renderCategories([...new Set(state.products.map((p) => p.category))].sort());
    renderGrid();
  } else if (d.example) runSearch(d.example);
  else if (d.ask) sendChat(d.ask);
  else if ("checkout" in d) startCheckout(target);
  else if ("close" in d) target.closest("dialog").close();
});

// click on a dialog's backdrop closes it
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
}

$("search-form").addEventListener("submit", (event) => { event.preventDefault(); runSearch($("search-input").value); });
$("search-input").addEventListener("search", () => { if (!$("search-input").value) clearSearch(); });
$("clear-search").addEventListener("click", clearSearch);
$("cart-button").addEventListener("click", () => { renderCart(); $("cart-dialog").showModal(); });
$("shopper-select").addEventListener("change", (event) => { state.shopperId = event.target.value; loadRecommendations(); });
$("chat-toggle").addEventListener("click", () => toggleChat(true));
$("chat-close").addEventListener("click", () => toggleChat(false));
$("chat-form").addEventListener("submit", (event) => { event.preventDefault(); sendChat($("chat-input").value); });

// ---------- start ----------

async function start() {
  $("examples").innerHTML = EXAMPLE_SEARCHES.map((q) =>
    `<button class="chip" type="button" data-example="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join("");
  $("chat-suggestions").innerHTML = CHAT_SUGGESTIONS.map((q) =>
    `<button class="chip" type="button" data-ask="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join("");
  $("grid").innerHTML = `<div class="skeleton"></div>`.repeat(8);

  try {
    const [catalog, shoppers, status] = await Promise.all([api("/api/products"), api("/api/shoppers"), api("/api/status")]);
    state.checkout = status.checkout;
    state.products = catalog.products;
    state.byId = new Map(catalog.products.map((p) => [p.id, p]));
    state.shoppers = shoppers.shoppers;
    $("shopper-select").insertAdjacentHTML("beforeend", state.shoppers.map((s) =>
      `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join(""));
    renderCategories(catalog.categories);
    renderGrid();
    renderCartCount();
    loadRecommendations();
    handleCheckoutReturn();
  } catch {
    $("grid").innerHTML = `<div class="empty">Couldn't load the store. Is the server running?</div>`;
  }
}

start();
